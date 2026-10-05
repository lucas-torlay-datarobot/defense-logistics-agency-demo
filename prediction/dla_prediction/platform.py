"""DataRobot experiment lifecycle. Creates no cloud work until explicitly invoked."""

from __future__ import annotations

import time

import numpy as np
import pandas as pd

from .evaluation import (
    CoverageBaseline,
    eligible_models,
    metrics,
    passes_gate,
    positive_probabilities,
    winner_from_validation,
)
from .features import FEATURES, TARGET
from .io import atomic_json, log


def start_project(dr, dataset, use_case, name, config, state, save):
    if state.get("project_id"):
        project = dr.Project.get(state["project_id"])
    else:
        matches = [
            p
            for p in dr.Project.list(search_params={"project_name": name})
            if p.project_name == name
        ]
        if len(matches) > 1:
            raise RuntimeError("Multiple exact project matches; resolve before continuing")
        if matches:
            project = matches[0]
        else:
            if state.get("project_stage") == "creating":
                raise RuntimeError(
                    "Previous project creation outcome uncertain. Inspect DataRobot before resetting state"
                )
            state["project_stage"] = "creating"
            save()
            project = dataset.create_project(project_name=name, use_cases=[use_case.id])
        state["project_id"] = project.id
        save()
    project.refresh()
    if not project.target:
        if state.get("project_stage") == "starting_autopilot":
            raise RuntimeError(
                "Autopilot start outcome uncertain; inspect this project's status before retrying"
            )
        lists = [f for f in project.get_featurelists() if f.name == "DLA point-in-time features v1"]
        featurelist = (
            lists[0]
            if lists
            else project.create_featurelist(name="DLA point-in-time features v1", features=FEATURES)
        )
        state["project_stage"] = "starting_autopilot"
        save()
        project.analyze_and_model(
            target=TARGET,
            target_type=dr.enums.TARGET_TYPE.BINARY,
            positive_class=1,
            metric="LogLoss",
            mode=config["autopilot_mode"],
            worker_count=config["workers"],
            partitioning_method=dr.UserTVH(
                user_partition_col="partition",
                training_level="train",
                validation_level="validation",
                holdout_level=None,
                seed=42,
            ),
            featurelist_id=featurelist.id,
            max_wait=config["max_wait"],
        )
    elif project.target != TARGET:
        raise RuntimeError("Existing project target differs; do not reuse")
    state["project_stage"] = "modeling"
    save()
    log(f"Waiting for DataRobot Autopilot: {project.id}")
    project.wait_for_autopilot(timeout=config["max_wait"], verbosity=1)
    project.refresh()
    state["project_stage"] = "trained"
    save()
    return project


def predict_project(dr, project, model, frame, label, run, state, save, max_wait):
    """Reuse prediction dataset/jobs and cache probabilities, preserving row order."""
    cache = run / f"{label}_{model.id}_probabilities.csv"
    if cache.exists():
        predictions = pd.read_csv(cache)
        if len(predictions) != len(frame):
            raise RuntimeError("Prediction cache count mismatch")
        p = predictions.probability.to_numpy()
        if not np.isfinite(p).all() or not ((p >= 0) & (p <= 1)).all():
            raise RuntimeError("Invalid cached probabilities")
        return p
    dataset_key = f"{label}_prediction_dataset_id"
    if not state.get(dataset_key):
        path = run / f"{label}_features.csv"
        frame[FEATURES].to_csv(path, index=False)
        state[dataset_key] = project.upload_dataset(str(path), max_wait=max_wait).id
        save()
    jobs = state.setdefault("prediction_jobs", {})
    key = f"{label}:{model.id}"
    if key in jobs:
        job = dr.PredictJob.get(project.id, jobs[key])
    else:
        job = model.request_predictions(state[dataset_key])
        jobs[key] = job.id
        save()
    predictions = job.get_result_when_complete(max_wait=max_wait)
    p = positive_probabilities(predictions, len(frame))
    temporary = cache.with_suffix(".partial")
    pd.DataFrame({"probability": p}).to_csv(temporary, index=False)
    temporary.replace(cache)
    return p


def evaluate_models(dr, project, table, config, run, state, save):
    train = table[table.partition == "train"].reset_index(drop=True)
    validation = table[table.partition == "validation"].reset_index(drop=True)
    test = table[table.partition == "test"].reset_index(drop=True)
    baseline = CoverageBaseline().fit(train)
    baseline_metrics, prevalence_metrics = {}, {}
    for label, frame in [("validation", validation), ("test", test)]:
        baseline_metrics[label] = metrics(frame, baseline.predict(frame), config["review_fraction"])
        prevalence_metrics[label] = metrics(
            frame, np.repeat(baseline.prevalence, len(frame)), config["review_fraction"]
        )
    recommended = project.recommended_model()
    if "candidate_model_ids" not in state:
        models = eligible_models(project.get_models(), project.max_train_pct)
        if not models:
            raise RuntimeError(
                "No train-only models with finite validation LogLoss. Check the leaderboard"
            )
        # Freeze candidate pool before looking at the independent final period.
        state["candidate_model_ids"] = [m.id for m in models[: config["top_models"]]]
        save()
    records = []
    for model_id in state["candidate_model_ids"]:
        model = dr.Model.get(project.id, model_id)
        if model.sample_pct > project.max_train_pct + 1e-6:
            raise RuntimeError("Candidate was trained beyond training partition")
        log(f"Evaluating validation predictions: {model.model_type}")
        p = predict_project(
            dr, project, model, validation, "validation", run, state, save, config["max_wait"]
        )
        records.append(
            {
                "model_id": model.id,
                "model_type": model.model_type,
                "datarobot_recommended": bool(recommended and recommended.id == model.id),
                "sample_pct": model.sample_pct,
                "validation": metrics(validation, p, config["review_fraction"]),
            }
        )
    ranking = sorted(
        records,
        key=lambda r: (
            -r["validation"]["average_precision"],
            r["validation"]["log_loss"],
            r["model_id"],
        ),
    )
    selected = winner_from_validation(records)
    if state.get("selected_model_id") and state["selected_model_id"] != selected["model_id"]:
        raise RuntimeError(
            "Frozen selection changed; create a new run instead of reusing test results"
        )
    state["selected_model_id"] = selected["model_id"]
    save()  # Freeze choice BEFORE requesting any test model predictions.
    atomic_json(run / "model_ranking.json", ranking)
    pd.json_normalize(ranking).to_csv(run / "model_ranking.csv", index=False)
    model = dr.Model.get(project.id, selected["model_id"])
    p = predict_project(dr, project, model, test, "test", run, state, save, config["max_wait"])
    test_metrics = metrics(test, p, config["review_fraction"])
    validation_pass = passes_gate(
        selected["validation"], baseline_metrics["validation"], prevalence_metrics["validation"]
    )
    test_pass = passes_gate(test_metrics, baseline_metrics["test"], prevalence_metrics["test"])
    report = {
        "is_synthetic": True,
        "project_id": project.id,
        "selected_model": selected,
        "test": test_metrics,
        "coverage_baseline": baseline_metrics,
        "prevalence_baseline": prevalence_metrics,
        "validation_pass": validation_pass,
        "test_pass": test_pass,
        "deployment_eligible": validation_pass and test_pass,
        "selection_rule": "Top DataRobot validation LogLoss candidates, then highest validation average precision; LogLoss breaks ties.",
        "gate": "In validation AND test: AP exceeds coverage baseline; LogLoss beats training-prevalence baseline.",
        "limitations": "Synthetic scenario only; overlapping daily outcomes are correlated. Not evidence of DLA operational accuracy. Test does not select a runner-up.",
    }
    atomic_json(run / "evaluation.json", report)
    log(
        f"Selected {model.id}; final AP={test_metrics['average_precision']:.4f}; deployment eligible={report['deployment_eligible']}"
    )
    # Helpful for the UI, but unavailable explanations do not invalidate predictions.
    try:
        impact = model.get_or_request_feature_impact(max_wait=config["max_wait"])
        atomic_json(run / "feature_impact.json", impact)
    except Exception as exc:
        log(f"Feature impact unavailable ({type(exc).__name__}); evaluation remains saved")
    return report


def deploy_model(dr, project, report, name, prediction_environment_id, state, save, max_wait):
    if not report["deployment_eligible"]:
        raise RuntimeError(
            "Model did not pass baseline gates; inspect evaluation.json. No deployment created"
        )
    model_id = report["selected_model"]["model_id"]
    if state.get("deployment_id"):
        return dr.Deployment.get(state["deployment_id"])
    matches = [d for d in dr.Deployment.list() if d.label == name]
    if len(matches) > 1:
        raise RuntimeError("Duplicate deployment labels")
    if matches:
        deployment = matches[0]
    else:
        if state.get("deployment_stage") == "creating":
            raise RuntimeError(
                "Prior deployment outcome uncertain; inspect DataRobot before retrying"
            )
        registered = [r for r in dr.RegisteredModel.list(search=name) if r.name == name]
        if len(registered) > 1:
            raise RuntimeError("Duplicate registered model names")
        rm = registered[0] if registered else None
        versions = (
            [v for v in rm.list_versions() if getattr(v, "model_id", None) == model_id]
            if rm
            else []
        )
        if state.get("registration_stage") == "creating" and not versions:
            raise RuntimeError(
                "Prior model registration outcome uncertain; inspect registry before retrying"
            )
        if versions:
            version = versions[0]
        else:
            state["registration_stage"] = "creating"
            save()
            kw = (
                {"registered_model_id": rm.id}
                if rm
                else {
                    "registered_model_name": name,
                    "registered_model_description": "SYNTHETIC DLA shortage-risk demonstration; not validated on operational DLA data.",
                }
            )
            version = dr.RegisteredModelVersion.create_for_leaderboard_item(
                model_id=model_id, name=name, **kw
            )
        state.update(
            registered_model_id=version.registered_model_id,
            registered_model_version_id=version.id,
            registration_stage="registered",
        )
        save()
        if prediction_environment_id:
            deadline = time.monotonic() + max_wait
            while True:
                current = dr.RegisteredModel.get(version.registered_model_id).get_version(
                    version.id
                )
                status = str(getattr(current, "build_status", "")).lower()
                if status in {"complete", "completed", "succeeded"}:
                    break
                if status in {"failed", "error"} or time.monotonic() > deadline:
                    raise RuntimeError(
                        f"Registered model build not ready: {status}; rerun after inspection"
                    )
                time.sleep(15)
            state["deployment_stage"] = "creating"
            save()
            deployment = dr.Deployment.create_from_registered_model_version(
                model_package_id=version.id,
                label=name,
                prediction_environment_id=prediction_environment_id,
                description="SYNTHETIC operations model — demonstration only.",
                max_wait=max_wait,
            )
        else:
            servers = dr.PredictionServer.list()
            if len(servers) != 1:
                raise RuntimeError(
                    "Set DLA_PREDICTION_ENVIRONMENT_ID for the intended environment; no unique dedicated server"
                )
            state["deployment_stage"] = "creating"
            save()
            deployment = dr.Deployment.create_from_learning_model(
                model_id,
                label=name,
                default_prediction_server_id=servers[0].id,
                description="SYNTHETIC operations model — demonstration only.",
                max_wait=max_wait,
            )
    state.update(deployment_id=deployment.id, deployment_stage="deployed")
    save()
    return deployment
