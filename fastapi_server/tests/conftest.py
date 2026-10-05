import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
for directory in ("prediction", "agent", "fastapi_server"):
    sys.path.insert(0, str(ROOT / directory))

from app.prepare import build_snapshot  # noqa: E402
from dla_prediction.cli import code_digest  # noqa: E402
from dla_prediction.io import digest_file  # noqa: E402


@pytest.fixture
def prepared(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "prediction_fixture", ROOT / "prediction/tests/conftest.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    daily, orders = module.scenario(n_items=2, days=90)
    daily["item_name"] = "FIXTURE ITEM"
    daily["nsn"] = "1234" + daily.niin
    daily["fsc"] = "1234"
    run = tmp_path / "run"
    run.mkdir()
    paths = {}
    sources = {}
    for kind, frame in [("daily", daily), ("orders", orders)]:
        path = run / f"{kind}.csv"
        frame.to_csv(path, index=False)
        paths[kind] = str(path)
        sources[kind] = {
            "dataset_id": f"fixture-{kind}",
            "version_id": "v1",
            "sha256": digest_file(path),
        }
    manifest = {
        "source_paths": paths,
        "source_snapshots": sources,
        "simulation_id": "fixture",
        "code_sha256": code_digest(),
        "run_id": "fixture-run",
        "is_synthetic": True,
    }
    (run / "manifest.json").write_text(json.dumps(manifest))
    output = tmp_path / "app"
    build_snapshot(run, output)
    return run, output
