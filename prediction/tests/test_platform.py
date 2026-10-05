from types import SimpleNamespace
from unittest.mock import MagicMock

import datarobot as dr
import pytest

from dla_prediction.features import FEATURES, TARGET
from dla_prediction.platform import deploy_model, start_project


def test_user_partition_and_feature_allowlist_on_sdk():
    sdk = MagicMock()
    sdk.UserTVH = dr.UserTVH
    sdk.enums.TARGET_TYPE.BINARY = dr.enums.TARGET_TYPE.BINARY
    sdk.Project.list.return_value = []
    project = MagicMock(id="p", target=None)
    project.get_featurelists.return_value = []
    project.create_featurelist.return_value = SimpleNamespace(id="features")
    dataset = MagicMock()
    dataset.create_project.return_value = project
    state = {}
    result = start_project(
        sdk,
        dataset,
        SimpleNamespace(id="uc"),
        "demo",
        {"autopilot_mode": "quick", "workers": 2, "max_wait": 60},
        state,
        lambda: None,
    )
    assert result is project
    assert state["project_id"] == "p"
    kw = project.analyze_and_model.call_args.kwargs
    assert kw["target"] == TARGET and kw["positive_class"] == 1
    partition = kw["partitioning_method"]
    assert partition.training_level == "train" and partition.validation_level == "validation"
    assert partition.holdout_level is None
    assert project.create_featurelist.call_args.kwargs["features"] == FEATURES


def test_existing_project_does_not_restart_autopilot():
    sdk = MagicMock()
    project = MagicMock(id="p", target=TARGET)
    sdk.Project.get.return_value = project
    state = {"project_id": "p"}
    start_project(
        sdk, None, SimpleNamespace(id="uc"), "name", {"max_wait": 60}, state, lambda: None
    )
    project.analyze_and_model.assert_not_called()


def test_ambiguous_create_stops_and_no_failed_gate_deployment():
    sdk = MagicMock()
    sdk.Project.list.return_value = []
    with pytest.raises(RuntimeError, match="uncertain"):
        start_project(sdk, None, None, "name", {}, {"project_stage": "creating"}, lambda: None)
    with pytest.raises(RuntimeError, match="baseline gates"):
        deploy_model(sdk, None, {"deployment_eligible": False}, "name", None, {}, lambda: None, 60)
    sdk.Deployment.create_from_learning_model.assert_not_called()


def test_evaluation_freezes_winner_before_final_predictions(inputs, tmp_path, monkeypatch):
    import numpy as np

    import dla_prediction.platform as platform
    from dla_prediction.features import add_targets, build_features, split_training

    daily, orders = inputs
    table, _ = split_training(add_targets(build_features(daily, orders), daily))
    a = MagicMock(id="A", sample_pct=50, model_type="Candidate A")
    b = MagicMock(id="B", sample_pct=50, model_type="Candidate B")
    a.metrics = {"LogLoss": {"validation": 0.1}}
    b.metrics = {"LogLoss": {"validation": 0.2}}
    a.get_or_request_feature_impact.return_value = []
    sdk = MagicMock()
    sdk.Model.get.side_effect = lambda project, mid: {"A": a, "B": b}[mid]
    project = MagicMock(id="project", max_train_pct=60)
    project.recommended_model.return_value = b
    project.get_models.return_value = [a, b]
    state, calls = {}, []

    def predictions(dr, project, model, frame, label, run, state, save, max_wait):
        calls.append((model.id, label))
        if label == "test":
            assert state["selected_model_id"] == "A"
        y = frame[TARGET].to_numpy()
        return np.where(y == 1, 0.9, 0.1) if model.id == "A" else np.repeat(0.5, len(frame))

    monkeypatch.setattr(platform, "predict_project", predictions)
    result = platform.evaluate_models(
        sdk,
        project,
        table,
        {"review_fraction": 0.1, "top_models": 2, "max_wait": 60},
        tmp_path,
        state,
        lambda: None,
    )
    assert result["selected_model"]["model_id"] == "A"
    assert calls == [("A", "validation"), ("B", "validation"), ("A", "test")]
    assert state["candidate_model_ids"] == ["A", "B"]
    assert (tmp_path / "model_ranking.csv").exists()
