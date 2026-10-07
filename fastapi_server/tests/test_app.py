import json
import time

import duckdb
import httpx
import pytest
from app.main import create_app
from app.prepare import build_snapshot
from dla_agent.memory import Memory
from dla_agent.query import QueryService, validate_sql
from dla_agent.service import Agent, DataRobotLLM
from fastapi.testclient import TestClient

AUTH = {"X-DLA-Profile": "12345678-1234-4234-8234-123456789abc"}


class AllowGuard:
    def check(self, text):
        return 0.01


class Unconfigured:
    configured = False


class FakeLLM:
    configured = True

    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def complete(self, messages):
        self.calls.append(messages)
        return next(self.responses)


def test_snapshot_grain_provenance_and_no_fake_predictions(prepared):
    _, output = prepared
    query = QueryService(output / "snapshot.duckdb")
    meta = query.metadata()
    assert meta["as_of"] == "2025-03-31"
    assert meta["row_counts"]["latest_inventory"] == 6
    assert meta["row_counts"]["risk_scores"] == 0
    assert meta["source_snapshots"]["daily"]["version_id"] == "v1"
    result = query.execute("SELECT niin, location, shortage_probability_14d FROM latest_inventory")
    assert result["rows"][0]["niin"] == "000000001"
    assert all(r["shortage_probability_14d"] is None for r in result["rows"])
    bounded = query.execute("SELECT * FROM daily_inventory")
    assert bounded["truncated"] and len(bounded["rows"]) == 200
    # Prohibit one-to-many joins in dashboard metrics by checking the known fixture grain.
    result = query.execute("SELECT count(*) AS n FROM latest_inventory i JOIN items c USING (niin)")
    assert result["rows"] == [{"n": 6}]


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM daily_inventory",
        "SELECT * FROM daily_inventory; DROP TABLE items",
        "SELECT * FROM read_csv('/etc/passwd')",
        "SELECT * FROM read_parquet('https://example.com/a')",
        "SELECT getenv('SECRET') FROM items",
        "SELECT * FROM information_schema.tables",
        "SELECT * FROM duckdb_settings()",
        "SELECT * FROM sqlite_scan('x', 'users')",
        "COPY items TO '/tmp/stolen.csv'",
        "ATTACH '/tmp/other.duckdb'",
        "SELECT * FROM query('SELECT * FROM items')",
        "SELECT * FROM range(10000000)",
        "SELECT repeat(item_name, 1000000000) FROM items",
        "WITH RECURSIVE t AS (SELECT 1 UNION ALL SELECT * FROM t) SELECT * FROM t",
        "SELECT * FROM missing_table",
        "SELECT current_date FROM items",
        "SELECT * FROM items INTO temp",
    ],
)
def test_reject_unsafe_or_unknown_queries(sql):
    with pytest.raises(Exception):
        validate_sql(sql)


def test_ctes_aggregations_and_order_join(prepared):
    _, output = prepared
    result = QueryService(output / "snapshot.duckdb").execute("""
        WITH pending AS (SELECT niin, destination, count(*) AS orders
          FROM replenishment_orders WHERE is_open GROUP BY niin, destination)
        SELECT i.niin, i.location, coalesce(p.orders, 0) AS open_orders
        FROM latest_inventory i LEFT JOIN pending p ON i.niin=p.niin AND i.location=p.destination
        ORDER BY i.niin, i.location
    """)
    assert len(result["rows"]) == 6
    assert result["sources"] == ["latest_inventory", "replenishment_orders"]


def test_query_timeout_is_recoverable(prepared):
    _, output = prepared
    query = QueryService(output / "snapshot.duckdb", timeout=0.02)
    started = time.monotonic()
    with pytest.raises(duckdb.InterruptException):
        query.execute(
            "SELECT sum(a.closing_stock*b.closing_stock*c.closing_stock) FROM daily_inventory a CROSS JOIN daily_inventory b CROSS JOIN daily_inventory c"
        )
    assert time.monotonic() - started < 5
    assert query.execute("SELECT count(*) AS n FROM items")["rows"] == [{"n": 2}]


def test_snapshot_mismatch_rejected_before_replacement(prepared):
    run, output = prepared
    marker = QueryService(output / "snapshot.duckdb").metadata()["snapshot_id"]
    with (run / "daily.csv").open("a") as f:
        f.write("\n")
    with pytest.raises(ValueError, match="does not match"):
        build_snapshot(run, output)
    assert QueryService(output / "snapshot.duckdb").metadata()["snapshot_id"] == marker


def test_persistent_conversation_memory_and_review(prepared):
    _, output = prepared
    memory = Memory(output / "test-memory.sqlite3")
    session = memory.create_session()["id"]
    memory.remember("Focus on Depot B")
    llm = FakeLLM(
        [
            json.dumps(
                {
                    "status": "query",
                    "message": "Count available items.",
                    "sql": "SELECT count(*) AS items FROM items",
                }
            ),
            "There are 2 catalog items in this synthetic scenario.",
            json.dumps(
                {
                    "status": "query",
                    "message": "Show their identifiers.",
                    "sql": "SELECT niin FROM items ORDER BY niin",
                }
            ),
            "The evidence lists the two NIINs.",
        ]
    )
    agent = Agent(QueryService(output / "snapshot.duckdb"), memory, llm, guard=AllowGuard())
    first = agent.ask(session, "How many items do we have?")
    second = agent.ask(session, "Show me their NIINs")
    assert first["result"]["rows"] == [{"items": 2}]
    assert second["result"]["rows"][0]["niin"] == "000000001"
    context = json.loads(llm.calls[2][1]["content"])
    assert context["conversation"][-1]["result_rows"] == [{"items": 2}]
    assert context["saved_preferences"] == ["Focus on Depot B"]
    memory.queue_review(session, first["id"])
    memory.queue_review(session, first["id"])  # idempotent save
    restored = Memory(output / "test-memory.sqlite3")
    assert len(restored.messages(session)) == 4
    assert len(restored.reviews()) == 1
    assert restored.reviews()[0]["evidence"]["snapshot_id"] == first["snapshot_id"]
    restored.review(restored.reviews()[0]["id"], "reviewed")
    assert restored.reviews()[0]["status"] == "reviewed"
    restored.forget(restored.memories()[0]["id"])
    assert not restored.memories()
    restored.delete_session(session)
    assert not restored.sessions()
    assert len(restored.reviews()) == 1  # review evidence intentionally survives chat deletion


def test_agent_repairs_once_and_clarifies(prepared):
    _, output = prepared
    memory = Memory(output / "m.sqlite3")
    session = memory.create_session()["id"]
    llm = FakeLLM(
        [
            json.dumps(
                {
                    "status": "query",
                    "message": "Query",
                    "sql": "SELECT * FROM read_csv('/etc/passwd')",
                }
            ),
            json.dumps(
                {"status": "clarification", "message": "Which depot should I examine?", "sql": None}
            ),
        ]
    )
    reply = Agent(QueryService(output / "snapshot.duckdb"), memory, llm, guard=AllowGuard()).ask(
        session, "What about there?"
    )
    assert reply["status"] == "clarification" and "result" not in reply
    assert len(llm.calls) == 2


def test_no_login_dashboard_and_unconfigured_llm(prepared):
    _, output = prepared
    client = TestClient(create_app(output, llm=Unconfigured(), guard=AllowGuard()))
    assert client.get("/api/v1/health").status_code == 200
    assert client.get("/api/v1/catalog").status_code == 200
    assert client.post("/api/v1/query", json={"sql": "SELECT * FROM items"}).status_code == 200
    assert client.get("/api/v1/catalog", headers=AUTH).json()["llm_configured"] is False
    overview = client.get("/api/v1/overview", headers=AUTH)
    assert overview.status_code == 200, overview.text
    assert overview.json()["metrics"]["scored"] == 0
    assert overview.json()["metrics"]["item_locations"] == 6
    session = client.post("/api/v1/sessions", json={}, headers=AUTH).json()["id"]
    reply = client.post(
        f"/api/v1/sessions/{session}/messages", json={"message": "What is at risk?"}, headers=AUTH
    ).json()
    assert reply["status"] == "error" and "deployment" in reply["message"] and "result" not in reply
    assert client.post("/api/v1/memories", json={"text": "   "}, headers=AUTH).status_code == 400
    assert (
        client.post(
            "/api/v1/query", json={"sql": "SELECT * FROM read_csv('/etc/passwd')"}, headers=AUTH
        ).status_code
        == 400
    )


def test_llm_route_and_secret_not_exposed(monkeypatch):
    monkeypatch.setenv("DATAROBOT_ENDPOINT", "https://tenant.example/api/v2/")
    monkeypatch.setenv("DATAROBOT_API_TOKEN", "secret-for-test")
    monkeypatch.setenv("DLA_LLM_DEPLOYMENT_ID", "deployment-id")
    real_client = httpx.Client

    def route(request):
        assert (
            str(request.url)
            == "https://tenant.example/api/v2/deployments/deployment-id/chat/completions"
        )
        assert request.headers["Authorization"] == "Bearer secret-for-test"
        assert json.loads(request.content)["model"] == "datarobot-deployed-llm"
        return httpx.Response(403, text="sensitive upstream content secret-for-test")

    monkeypatch.setattr(
        httpx, "Client", lambda **kw: real_client(transport=httpx.MockTransport(route), **kw)
    )
    with pytest.raises(RuntimeError, match="HTTP 403") as caught:
        DataRobotLLM().complete([{"role": "user", "content": "hello"}])
    assert "secret-for-test" not in str(caught.value)


def test_score_selected_model_and_preserve_identity(prepared, monkeypatch):
    from types import SimpleNamespace

    import app.prepare as prepare
    import dla_prediction.platform as platform
    import numpy as np
    from dla_prediction.cli import code_digest
    from dla_prediction.features import FEATURES

    run, output = prepared
    contract = {
        "features": FEATURES,
        "code_sha256": code_digest(),
        "run_id": "fixture-run",
        "project_id": "selected-project",
        "model_id": "selected-model",
    }
    (run / "model_contract.json").write_text(json.dumps(contract))
    (run / "evaluation.json").write_text(
        json.dumps({"selected_model": {"model_id": "selected-model"}, "deployment_eligible": True})
    )
    project, model = SimpleNamespace(id="selected-project"), SimpleNamespace(id="selected-model")
    dr = SimpleNamespace(
        Project=SimpleNamespace(get=lambda project_id: project),
        Model=SimpleNamespace(get=lambda project_id, model_id: model),
    )
    monkeypatch.setattr(prepare, "connect", lambda _: (dr, None))

    def predict(dr, project, model, frame, label, run, state, save, max_wait):
        assert model.id == "selected-model" and project.id == "selected-project"
        assert frame.date.nunique() == 1 and len(frame) == 6
        assert label == "latest" and set(FEATURES).issubset(frame)
        state["prediction_jobs"] = {"latest:selected-model": "job"}
        save()
        return np.arange(6) / 10

    monkeypatch.setattr(platform, "predict_project", predict)
    meta = build_snapshot(run, output, score=True)
    assert meta["row_counts"]["risk_scores"] == 6
    rows = QueryService(output / "snapshot.duckdb").execute(
        "SELECT niin, location, shortage_probability_14d, model_id FROM latest_inventory ORDER BY niin, location"
    )["rows"]
    assert [r["shortage_probability_14d"] for r in rows] == [0.0, 0.1, 0.2, 0.3, 0.4, 0.5]
    assert all(r["model_id"] == "selected-model" for r in rows)
    assert json.loads((run / "app_scoring/state.json").read_text())["prediction_jobs"]


def test_running_app_rejects_changed_snapshot(prepared):
    run, output = prepared
    query = QueryService(output / "snapshot.duckdb")
    build_snapshot(run, output)
    with pytest.raises(RuntimeError, match="restart the app"):
        query.execute("SELECT * FROM items")


def test_external_access_disabled_at_engine_level(prepared):
    _, output = prepared
    query = QueryService(output / "snapshot.duckdb")
    with query.connect() as con:
        with pytest.raises(duckdb.PermissionException):
            con.execute("SELECT * FROM read_csv('/etc/passwd')")
        with pytest.raises(duckdb.InvalidInputException):
            con.execute("SET enable_external_access=true")


def test_profile_scope_is_independent_of_platform_authorization(prepared, monkeypatch):
    _, output = prepared
    monkeypatch.setenv("DLA_APP_ACCESS_TOKEN", "old-unused-token")
    client = TestClient(create_app(output, llm=Unconfigured(), guard=AllowGuard()))
    forwarded = {**AUTH, "Authorization": "Bearer platform-session-credential"}
    assert client.get("/api/v1/catalog").status_code == 200
    assert client.post("/api/v1/sessions", headers=forwarded, json={}).status_code == 200
    assert client.get("/api/v1/sessions", headers=AUTH).json()
    assert client.get("/api/v1/sessions").status_code == 400
    assert (
        client.get("/api/v1/memories", headers={"X-DLA-Profile": "../../other"}).status_code == 400
    )


def test_browser_profiles_isolate_history_preferences_and_reviews(prepared):
    _, output = prepared
    other = {"X-DLA-Profile": "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"}
    llm = FakeLLM(
        [
            json.dumps(
                {"status": "query", "message": "Count", "sql": "SELECT count(*) AS n FROM items"}
            ),
            "There are 2 simulated items.",
        ]
    )
    client = TestClient(create_app(output, llm=llm, guard=AllowGuard()))
    session = client.post("/api/v1/sessions", headers=AUTH, json={}).json()["id"]
    reply = client.post(
        f"/api/v1/sessions/{session}/messages", headers=AUTH, json={"message": "Count items"}
    ).json()
    client.post("/api/v1/memories", headers=AUTH, json={"text": "Focus on Depot B"})
    reviews = client.post(
        "/api/v1/reviews", headers=AUTH, json={"session_id": session, "message_id": reply["id"]}
    ).json()
    assert client.get("/api/v1/sessions", headers=other).json() == []
    assert client.get("/api/v1/memories", headers=other).json() == []
    assert client.get("/api/v1/reviews", headers=other).json() == []
    assert client.get(f"/api/v1/sessions/{session}", headers=other).status_code == 404
    assert (
        client.post(
            f"/api/v1/sessions/{session}/messages", headers=other, json={"message": "hello"}
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/api/v1/reviews",
            headers=other,
            json={"session_id": session, "message_id": reply["id"]},
        ).status_code
        == 400
    )
    client.delete(f"/api/v1/sessions/{session}", headers=other)
    client.patch(f"/api/v1/reviews/{reviews[0]['id']}", headers=other, json={"status": "dismissed"})
    restored = TestClient(create_app(output, llm=Unconfigured(), guard=AllowGuard()))
    assert len(restored.get(f"/api/v1/sessions/{session}", headers=AUTH).json()) == 2
    assert restored.get("/api/v1/memories", headers=AUTH).json()[0]["text"] == "Focus on Depot B"
    assert restored.get("/api/v1/reviews", headers=AUTH).json()[0]["status"] == "pending"
