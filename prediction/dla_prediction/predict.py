"""Deployment scoring for the future app/agent; never silently substitutes demo scores."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from urllib.parse import urlparse

import numpy as np
import pandas as pd
from dotenv import load_dotenv

from .cli import ROOT, code_digest
from .features import FEATURES, build_features, require, validate_inputs
from .io import connect, log, read_inputs


def predict_rows(client, deployment_id, rows):
    """Same public/dedicated route pattern as VA, with strict positive-label parsing."""
    try:
        response = client.post(
            f"deployments/{deployment_id}/predictions",
            json=rows,
            keep_attrs=FEATURES,
            timeout=(30, 120),
        )
    except Exception as exc:
        if getattr(exc, "status_code", None) not in (404, 405):
            raise
        metadata = client.get(f"deployments/{deployment_id}/").json()
        server = metadata.get("defaultPredictionServer") or {}
        base = str(server.get("url") or "").rstrip("/")
        require(urlparse(base).scheme == "https", "No HTTPS dedicated prediction server available")
        headers = {}
        key = server.get("datarobot-key") or server.get("datarobotKey")
        if key:
            headers["DataRobot-Key"] = str(key)
        response = client.post(
            f"{base}/predApi/v1.0/deployments/{deployment_id}/predictions",
            json=rows,
            headers=headers,
            keep_attrs=FEATURES,
            timeout=(30, 120),
        )
    data = response.json().get("data")
    require(isinstance(data, list) and len(data) == len(rows), "Prediction response row mismatch")
    if all("rowId" in x for x in data):
        require(
            sorted(x["rowId"] for x in data) == list(range(len(rows))), "Invalid prediction row IDs"
        )
        data = sorted(data, key=lambda x: x["rowId"])
    probabilities = []
    for row in data:
        matches = []
        for value in row.get("predictionValues", []):
            try:
                if float(value["label"]) == 1.0:
                    matches.append(float(value["value"]))
            except (TypeError, ValueError, KeyError):
                continue
        require(
            len(matches) == 1 and np.isfinite(matches[0]) and 0 <= matches[0] <= 1,
            "Missing/invalid positive-class probability",
        )
        probabilities.append(matches[0])
    return probabilities


def main(argv=None):
    load_dotenv(ROOT / ".env", override=False)
    parser = argparse.ArgumentParser(
        description="Score a SYNTHETIC day with the deployed shortage model"
    )
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--deployment-id", default=os.getenv("DLA_SHORTAGE_DEPLOYMENT_ID"))
    parser.add_argument("--as-of", help="YYYY-MM-DD, default latest completed date")
    parser.add_argument("--daily-csv", type=Path)
    parser.add_argument("--orders-csv", type=Path)
    args = parser.parse_args(argv)
    if bool(args.daily_csv) != bool(args.orders_csv):
        parser.error("Supply both input CSVs or neither")
    manifest = json.loads((args.run_dir / "manifest.json").read_text())
    contract = json.loads((args.run_dir / "model_contract.json").read_text())
    require(
        contract["features"] == FEATURES and contract["code_sha256"] == code_digest(),
        "Scoring code differs from training; use the training commit",
    )
    state = json.loads((args.run_dir / "state.json").read_text())
    deployment_id = args.deployment_id or state.get("deployment_id")
    require(bool(deployment_id), "No deployment ID: deploy a passing model first")
    paths = (
        {"daily": args.daily_csv, "orders": args.orders_csv}
        if args.daily_csv
        else manifest["source_paths"]
    )
    daily, orders = validate_inputs(*read_inputs(paths))
    as_of = pd.Timestamp(args.as_of) if args.as_of else daily.date.max()
    require(
        daily.date.min() + pd.Timedelta(days=60) <= as_of <= daily.date.max(),
        "As-of date must follow startup and exist in the observations",
    )
    features = build_features(daily, orders, as_of=as_of)
    current = features[features.date == as_of].reset_index(drop=True)
    require(not current.empty, "No feature rows for this date")
    dr, client = connect(ROOT)
    deployment = dr.Deployment.get(deployment_id)
    deployed_model = deployment.model or {}
    require(
        deployed_model.get("id") == contract["model_id"],
        "Deployment does not serve the evaluated model",
    )
    p = []
    for start in range(0, len(current), 100):
        p.extend(
            predict_rows(
                client,
                deployment_id,
                current[FEATURES].iloc[start : start + 100].to_dict("records"),
            )
        )
    output = current[["date", "niin", "location", "closing_stock", "days_of_cover"]].copy()
    output["shortage_probability_14d"] = p
    output["prediction_source"] = "datarobot_deployment"
    output["deployment_id"] = deployment_id
    output["is_synthetic"] = True
    output["simulation_id"] = str(daily.simulation_id.iloc[0])
    output["model_run_id"] = contract["run_id"]
    output = output.sort_values(
        ["shortage_probability_14d", "niin", "location"], ascending=[False, True, True]
    )
    path = args.run_dir / f"SYNTHETIC_risk_{as_of.date()}.csv"
    output.to_csv(path, index=False)
    log(f"Scored {len(output)} item-location rows: {path}")
