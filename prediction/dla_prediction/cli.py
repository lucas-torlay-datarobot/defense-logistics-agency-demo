"""Terminal workflow modeled on VA's scripts/provision_demo_models.py."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from filelock import FileLock

from .features import (
    FEATURES,
    HORIZON,
    TARGET,
    add_targets,
    build_features,
    split_training,
    validate_inputs,
)
from .io import (
    atomic_json,
    connect,
    digest_file,
    download_sources,
    log,
    read_inputs,
    register_csv,
    resolve_sources,
    resolve_use_case,
)
from .platform import deploy_model, evaluate_models, start_project

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = {
    "warmup_days": 60,
    "validation_days": 60,
    "test_days": 45,
    "autopilot_mode": "quick",
    "workers": 2,
    "top_models": 5,
    "review_fraction": 0.10,
    "max_wait": 7200,
}


def code_digest():
    digest = hashlib.sha256()
    for path in sorted(Path(__file__).parent.glob("*.py")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def write_runtime_env(path, updates):
    """Record IDs only; no token is generated or printed."""
    lines = path.read_text().splitlines() if path.exists() else []
    keys = set(updates)
    out = [line for line in lines if line.split("=", 1)[0].strip() not in keys]
    out.extend(f'{key}="{value}"' for key, value in updates.items())
    temp = path.with_suffix(".partial")
    temp.write_text("\n".join(out) + "\n")
    temp.replace(path)


def main(argv=None):
    load_dotenv(ROOT / ".env", override=False)
    parser = argparse.ArgumentParser(
        description="Build and rank SYNTHETIC DLA shortage-risk models"
    )
    parser.add_argument("--config", type=Path, default=ROOT / "prediction/config.json")
    parser.add_argument("--use-case-id", default=os.getenv("DLA_USE_CASE_ID"))
    parser.add_argument("--use-case-name", default="Defense_Logistics_Demo")
    parser.add_argument("--daily-dataset-id", default=os.getenv("DLA_DAILY_DATASET_ID"))
    parser.add_argument("--orders-dataset-id", default=os.getenv("DLA_ORDERS_DATASET_ID"))
    parser.add_argument("--daily-csv", type=Path)
    parser.add_argument("--orders-csv", type=Path)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build/validate files locally; create no cloud resources",
    )
    parser.add_argument(
        "--deploy",
        action="store_true",
        help="Deploy only if validation and test baseline gates pass",
    )
    parser.add_argument(
        "--prediction-environment-id", default=os.getenv("DLA_PREDICTION_ENVIRONMENT_ID")
    )
    parser.add_argument("--output-dir", type=Path, default=ROOT / "artifacts/prediction")
    args = parser.parse_args(argv)
    if bool(args.daily_csv) != bool(args.orders_csv):
        parser.error("Supply both local CSV paths or neither")
    if args.deploy and args.dry_run:
        parser.error("--deploy and --dry-run cannot be combined")
    config = dict(DEFAULT_CONFIG)
    if args.config.exists():
        overrides = json.loads(args.config.read_text())
        if set(overrides) - set(config):
            parser.error("Unknown configuration keys: " + str(set(overrides) - set(config)))
        config.update(overrides)
    if config["autopilot_mode"] not in {"quick", "auto"}:
        parser.error("autopilot_mode must be quick or auto")
    if not 1 <= config["top_models"] <= 20 or not 0 < config["review_fraction"] <= 1:
        parser.error("top_models must be 1..20; review_fraction must be in (0,1]")
    for key in ("warmup_days", "validation_days", "test_days", "workers", "max_wait"):
        if not isinstance(config[key], int) or config[key] <= 0:
            parser.error(f"{key} must be a positive integer")
    dr = client = use_case = None
    if not args.daily_csv or not args.dry_run:
        dr, client = connect(ROOT)
        use_case = resolve_use_case(dr, client, args.use_case_id, args.use_case_name)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    # Local lock avoids duplicate submissions from two terminals sharing this checkout.
    with FileLock(str(args.output_dir / ".pipeline.lock"), timeout=0):
        if args.daily_csv:
            paths = {"daily": args.daily_csv, "orders": args.orders_csv}
            sources = {
                key: {"sha256": digest_file(path), "local_file": str(path.resolve())}
                for key, path in paths.items()
            }
        else:
            datasets = resolve_sources(
                dr, use_case.id, args.daily_dataset_id, args.orders_dataset_id
            )
            paths, sources = download_sources(dr, datasets, args.output_dir / "source_cache")
        log("Validating the paired synthetic scenario")
        daily, orders = validate_inputs(*read_inputs(paths))
        identity = {
            "source_snapshots": sources,
            "config": config,
            "code_sha256": code_digest(),
            "use_case_id": use_case.id if use_case else None,
        }
        run_id = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:24]
        run = args.output_dir / run_id
        run.mkdir(exist_ok=True)
        state_path = run / "state.json"
        state = json.loads(state_path.read_text()) if state_path.exists() else {}

        def save():
            atomic_json(state_path, state)

        log("Building historical end-of-day features and future 14-day labels")
        features = build_features(daily, orders)
        table, split_report = split_training(
            add_targets(features, daily),
            config["warmup_days"],
            config["validation_days"],
            config["test_days"],
        )
        manifest = {
            **identity,
            "run_id": run_id,
            "is_synthetic": True,
            "simulation_id": str(daily.simulation_id.iloc[0]),
            "target": TARGET,
            "horizon_days": HORIZON,
            "features": FEATURES,
            "splits": split_report,
            "scenario_end": str(daily.date.max().date()),
            "source_paths": {k: str(v.resolve()) for k, v in paths.items()},
        }
        atomic_json(run / "manifest.json", manifest)
        training_path = run / "SYNTHETIC_train_validation.csv"
        table.loc[table.partition != "test", FEATURES + [TARGET, "partition"]].to_csv(
            training_path, index=False
        )
        table.to_csv(run / "SYNTHETIC_labeled_audit.csv", index=False)
        latest = features[features.date == daily.date.max()]
        latest.to_csv(run / "SYNTHETIC_latest_features.csv", index=False)
        log(f"Splits: {json.dumps(split_report)}")
        log(f"Run directory: {run}")
        if args.dry_run:
            log("Dry run complete; no dataset, experiment, or deployment created")
            return run
        dataset = register_csv(
            dr, client, use_case, training_path, f"SYNTHETIC_DLA_training__{run_id}", state, save
        )
        project = start_project(
            dr, dataset, use_case, f"SYNTHETIC DLA shortage 14d {run_id}", config, state, save
        )
        report = evaluate_models(dr, project, table, config, run, state, save)
        atomic_json(
            run / "model_contract.json",
            {
                "is_synthetic": True,
                "target": TARGET,
                "positive_class": 1,
                "horizon_days": HORIZON,
                "observation": "end_of_day",
                "features": FEATURES,
                "project_id": project.id,
                "model_id": report["selected_model"]["model_id"],
                "run_id": run_id,
                "code_sha256": identity["code_sha256"],
            },
        )
        runtime = {
            "DLA_SHORTAGE_PROJECT_ID": project.id,
            "DLA_SHORTAGE_MODEL_ID": report["selected_model"]["model_id"],
            "DLA_PREDICTION_RUN_DIR": str(run.resolve()),
        }
        if args.deploy:
            deployment = deploy_model(
                dr,
                project,
                report,
                f"SYNTHETIC DLA shortage 14d {run_id}",
                args.prediction_environment_id,
                state,
                save,
                config["max_wait"],
            )
            runtime["DLA_SHORTAGE_DEPLOYMENT_ID"] = deployment.id
        write_runtime_env(run / "runtime.env", runtime)
        log("Saved ranking, evaluation, model contract and runtime.env; inspect before deployment")
        return run
