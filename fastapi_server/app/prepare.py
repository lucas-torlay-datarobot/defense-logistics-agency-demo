"""Build an atomic, read-only app snapshot from the existing prediction run."""

from __future__ import annotations

import argparse
import json
import os
import uuid
from pathlib import Path

import duckdb
import pandas as pd
from dla_prediction.cli import ROOT, code_digest
from dla_prediction.features import FEATURES, build_features, require, validate_inputs
from dla_prediction.io import atomic_json, connect, digest_file, log, read_inputs
from filelock import FileLock


def build_snapshot(run, output, *, score=False, paths=None):
    run, output = Path(run).resolve(), Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((run / "manifest.json").read_text())
    paths = paths or manifest["source_paths"]
    for kind in ("daily", "orders"):
        path = Path(paths[kind])
        require(
            path.is_file(),
            f"Missing cached {kind} CSV: {path}. Supply --daily-csv and --orders-csv from the original registered snapshots.",
        )
        require(
            digest_file(path) == manifest["source_snapshots"][kind]["sha256"],
            f"{kind} does not match the training snapshot; do not mix scenarios",
        )
    log("Preparing app snapshot from validated training sources")
    daily, orders = validate_inputs(*read_inputs(paths))
    as_of = daily.date.max()
    require(
        str(daily.simulation_id.iloc[0]) == manifest["simulation_id"],
        "Scenario differs from training",
    )
    require(
        manifest["code_sha256"] == code_digest(),
        "Use the prediction code version used for this training run",
    )
    features = build_features(daily, orders, as_of=as_of)
    latest = features[features.date == as_of].reset_index(drop=True)
    require(not latest.empty, "No current inventory")
    contract_path = run / "model_contract.json"
    contract = json.loads(contract_path.read_text()) if contract_path.exists() else None
    evaluation_path = run / "evaluation.json"
    evaluation = json.loads(evaluation_path.read_text()) if evaluation_path.exists() else None
    scores = latest[["date", "niin", "location"]].iloc[:0].copy()
    scores["shortage_probability_14d"] = pd.Series(dtype="float64")
    scores["prediction_source"] = pd.Series(dtype="str")
    scores["model_id"] = pd.Series(dtype="str")
    scores["project_id"] = pd.Series(dtype="str")
    if score:
        from dla_prediction.platform import predict_project

        require(contract is not None, "Finish model evaluation before --score")
        require(
            contract["features"] == FEATURES and contract["code_sha256"] == code_digest(),
            "Model contract mismatch",
        )
        require(contract["run_id"] == manifest["run_id"], "Model belongs to another run")
        require(
            evaluation and evaluation["selected_model"]["model_id"] == contract["model_id"],
            "Evaluation/contract model mismatch",
        )
        cache = run / "app_scoring"
        cache.mkdir(exist_ok=True)
        state_path = cache / "state.json"
        state = json.loads(state_path.read_text()) if state_path.exists() else {}
        dr, _ = connect(ROOT)
        project = dr.Project.get(contract["project_id"])
        model = dr.Model.get(project.id, contract["model_id"])
        log("Scoring the latest day with the selected DataRobot model (no new training)")
        probabilities = predict_project(
            dr,
            project,
            model,
            latest,
            "latest",
            cache,
            state,
            lambda: atomic_json(state_path, state),
            1800,
        )
        scores = latest[["date", "niin", "location"]].copy()
        scores["shortage_probability_14d"] = probabilities
        scores["prediction_source"] = "datarobot_project_model"
        scores["model_id"], scores["project_id"] = model.id, project.id
    item_columns = [c for c in ("niin", "nsn", "item_name", "fsc") if c in daily]
    items = daily[item_columns].drop_duplicates()
    require(not items.niin.duplicated().any(), "Conflicting catalog details for NIIN")
    for column in ("nsn", "item_name", "fsc"):
        if column not in items:
            items[column] = pd.Series(None, index=items.index, dtype="string")
        items[column] = items[column].astype("string")
    daily_columns = [
        "date",
        "niin",
        "location",
        "opening_stock",
        "requested_quantity",
        "fulfilled_quantity",
        "received_quantity",
        "closing_stock",
        "unfulfilled_quantity",
        "is_synthetic",
    ]
    order_columns = [
        "order_id",
        "niin",
        "destination",
        "order_date",
        "quantity",
        "expected_receipt_date",
        "actual_receipt_date",
        "is_synthetic",
    ]
    daily = daily[daily_columns]
    orders = orders[order_columns].copy()
    orders["is_open"] = orders.actual_receipt_date.isna() | (orders.actual_receipt_date > as_of)
    orders["is_overdue"] = orders.is_open & (orders.expected_receipt_date <= as_of)
    latest = latest.merge(items, on="niin", how="left", validate="many_to_one")
    latest = latest.merge(
        scores, on=["date", "niin", "location"], how="left", validate="one_to_one"
    )
    meta = {
        "snapshot_id": str(uuid.uuid4()),
        "run_id": manifest["run_id"],
        "as_of": str(as_of.date()),
        "history_start": str(daily.date.min().date()),
        "is_synthetic": True,
        "simulation_id": manifest["simulation_id"],
        "source_snapshots": manifest["source_snapshots"],
        "row_counts": {
            "daily_inventory": len(daily),
            "replenishment_orders": len(orders),
            "items": len(items),
            "latest_inventory": len(latest),
            "risk_scores": len(scores),
        },
        "model": contract,
        "evaluation": evaluation,
        "limitations": [
            "Synthetic operations; real catalog identities only.",
            "Probabilities estimate any shortage over the next 14 days, not units short.",
            "No prices, suppliers, approved substitutes, transport constraints or causal action model.",
            "One scenario and its catalog subset; not all registered DLA data.",
        ],
    }
    # Metadata lives inside the DB so readers cannot observe a mismatched manifest.
    temporary = output / f"snapshot-{uuid.uuid4().hex}.partial.duckdb"
    try:
        with duckdb.connect(str(temporary), config={"memory_limit": "1GB", "threads": "1"}) as con:
            for table, frame in (
                ("daily_inventory", daily),
                ("replenishment_orders", orders),
                ("items", items),
                ("latest_inventory", latest),
                ("risk_scores", scores),
            ):
                con.register("frame", frame)
                con.execute(f'CREATE TABLE "{table}" AS SELECT * FROM frame')
                con.unregister("frame")
            con.execute("CREATE TABLE app_metadata(payload VARCHAR)")
            con.execute(
                "INSERT INTO app_metadata VALUES (?)",
                [json.dumps(meta, default=str, allow_nan=False)],
            )
            con.execute("CHECKPOINT")
        os.replace(temporary, output / "snapshot.duckdb")
        atomic_json(output / "manifest.json", meta)
    finally:
        temporary.unlink(missing_ok=True)
    log(
        f"App ready: {output / 'snapshot.duckdb'} | {len(latest)} item-location rows | {len(scores)} model scores"
    )
    return meta


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Prepare the DLA app from an existing prediction run"
    )
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "artifacts/app")
    parser.add_argument(
        "--score",
        action="store_true",
        help="Score the latest day with the selected DataRobot project model; resume cached jobs on retry",
    )
    parser.add_argument("--daily-csv", type=Path)
    parser.add_argument("--orders-csv", type=Path)
    args = parser.parse_args(argv)
    if bool(args.daily_csv) != bool(args.orders_csv):
        parser.error("Supply both CSV overrides or neither")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.run_dir.mkdir(parents=True, exist_ok=True)
    with (
        FileLock(str(args.output_dir / "prepare.lock"), timeout=0),
        FileLock(str(args.run_dir / "app_scoring.lock"), timeout=0),
    ):
        paths = {"daily": args.daily_csv, "orders": args.orders_csv} if args.daily_csv else None
        build_snapshot(args.run_dir, args.output_dir, score=args.score, paths=paths)
