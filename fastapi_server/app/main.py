from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Literal

from dla_agent.memory import Memory
from dla_agent.query import QueryService
from dla_agent.service import Agent, DataRobotLLM
from fastapi import Depends, FastAPI, Header, HTTPException, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[2]


class Text(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


class SQL(BaseModel):
    sql: str = Field(min_length=1, max_length=16000)


class Preference(BaseModel):
    text: str = Field(min_length=1, max_length=600)


class Review(BaseModel):
    session_id: str
    message_id: int


class ReviewStatus(BaseModel):
    status: Literal["pending", "reviewed", "dismissed"]


def create_app(data_dir=None, token=None, llm=None, retriever=None):
    directory = Path(data_dir or os.getenv("DLA_APP_DATA_DIR") or ROOT / "artifacts/app")
    token = token or os.getenv("DLA_APP_ACCESS_TOKEN")
    if not token or len(token) < 24:
        raise RuntimeError("Set DLA_APP_ACCESS_TOKEN to a random secret of at least 24 characters")
    if not (directory / "snapshot.duckdb").is_file():
        raise RuntimeError(
            "Prepare the app snapshot first: python scripts/prepare_app_data.py --run-dir ..."
        )
    query, memory = (
        QueryService(directory / "snapshot.duckdb"),
        Memory(directory / "memory.sqlite3"),
    )
    llm = llm or DataRobotLLM()
    agent = Agent(query, memory, llm, retriever)

    def authorize(x_dla_app_token: str = Header(default="", alias="X-DLA-App-Token")):
        # Authorization belongs to the hosting platform, which may inspect or replace it
        # before forwarding the request. Authenticate this app independently.
        if not secrets.compare_digest(x_dla_app_token.encode(), token.encode()):
            raise HTTPException(401, "Enter the app access token configured on the server")

    app = FastAPI(
        title="DLA Logistics Intelligence", docs_url=None, redoc_url=None, openapi_url=None
    )
    auth = [Depends(authorize)]

    @app.middleware("http")
    async def headers(request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'self'; base-uri 'self'"
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/api/v1/health")
    def health():
        return {"status": "ok"}

    @app.get("/api/v1/catalog", dependencies=auth)
    def catalog():
        return {
            **query.metadata(),
            "llm_configured": llm.configured,
            "retrieval": agent.retriever.info(),
            "memory_scope": "single-user demo workspace",
        }

    @app.get("/api/v1/overview", dependencies=auth)
    def overview():
        return {
            "metrics": query.execute("""SELECT count(*) AS item_locations, count(DISTINCT niin) AS items,
                count(DISTINCT location) AS locations, count(shortage_probability_14d) AS scored,
                count(*) FILTER (WHERE shortage_probability_14d >= 0.5) AS elevated_risk,
                count(*) FILTER (WHERE closing_stock=0) AS zero_stock FROM latest_inventory""")[
                "rows"
            ][0],
            "depots": query.execute("""SELECT location, count(*) AS item_locations,
                count(*) FILTER (WHERE shortage_probability_14d >= 0.5) AS elevated_risk,
                avg(shortage_probability_14d) AS mean_risk,
                count(*) FILTER (WHERE closing_stock=0) AS zero_stock
                FROM latest_inventory GROUP BY location ORDER BY location""")["rows"],
            "trend": query.execute("""SELECT date, round(100.0 * count(*) FILTER (WHERE unfulfilled_quantity>0) / count(*),2) AS shortage_pct
                FROM daily_inventory WHERE date > (SELECT max(date) - INTERVAL 28 DAY FROM daily_inventory)
                GROUP BY date ORDER BY date""")["rows"],
            "orders": query.execute(
                "SELECT count(*) FILTER (WHERE is_open) AS open_orders, count(*) FILTER (WHERE is_overdue) AS overdue_orders FROM replenishment_orders"
            )["rows"][0],
        }

    @app.post("/api/v1/query", dependencies=auth)
    def execute(request: SQL):
        try:
            return query.execute(request.sql)
        except Exception as exc:
            # No path or DB connection information returned; SQL is visible to this operator.
            raise HTTPException(
                400, "Query rejected or exceeded its resource limit: " + str(exc)[:400]
            ) from exc

    @app.get("/api/v1/sessions", dependencies=auth)
    def sessions():
        return memory.sessions()

    @app.post("/api/v1/sessions", dependencies=auth)
    def new_session():
        return memory.create_session()

    @app.get("/api/v1/sessions/{session_id}", dependencies=auth)
    def session(session_id: str):
        try:
            return memory.messages(session_id)
        except KeyError as exc:
            raise HTTPException(404, "Conversation not found") from exc

    @app.delete("/api/v1/sessions/{session_id}", dependencies=auth)
    def delete_session(session_id: str):
        memory.delete_session(session_id)
        return Response(status_code=204)

    @app.post("/api/v1/sessions/{session_id}/messages", dependencies=auth)
    def ask(session_id: str, request: Text):
        try:
            return agent.ask(session_id, request.message.strip())
        except KeyError as exc:
            raise HTTPException(404, "Conversation not found") from exc
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/v1/memories", dependencies=auth)
    def memories():
        return memory.memories()

    @app.post("/api/v1/memories", dependencies=auth)
    def remember(request: Preference):
        if not request.text.strip():
            raise HTTPException(400, "Preference cannot be blank")
        try:
            return memory.remember(request.text)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.delete("/api/v1/memories/{memory_id}", dependencies=auth)
    def forget(memory_id: str):
        memory.forget(memory_id)
        return Response(status_code=204)

    @app.get("/api/v1/reviews", dependencies=auth)
    def reviews():
        return memory.reviews()

    @app.post("/api/v1/reviews", dependencies=auth)
    def queue_review(request: Review):
        try:
            return memory.queue_review(request.session_id, request.message_id)
        except (KeyError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.patch("/api/v1/reviews/{review_id}", dependencies=auth)
    def review(review_id: str, request: ReviewStatus):
        memory.review(review_id, request.status)
        return {"status": request.status}

    dist = ROOT / "frontend_web/dist"
    if dist.is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/")
        def index():
            return FileResponse(dist / "index.html")

    return app
