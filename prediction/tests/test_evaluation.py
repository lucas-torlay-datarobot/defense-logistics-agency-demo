from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from dla_prediction.evaluation import (
    eligible_models,
    metrics,
    passes_gate,
    positive_probabilities,
    winner_from_validation,
)
from dla_prediction.features import TARGET
from dla_prediction.predict import predict_rows


def test_probability_column_and_row_alignment():
    pred = pd.DataFrame({"row_id": [1, 0], "prediction": [1, 0], "class_1.0": [0.8, 0.2]})
    assert positive_probabilities(pred, 2).tolist() == [0.2, 0.8]
    with pytest.raises(ValueError, match="unambiguous"):
        positive_probabilities(pred.drop(columns="class_1.0"), 2)
    with pytest.raises(ValueError, match="row IDs"):
        positive_probabilities(pred.assign(row_id=[0, 0]), 2)


def test_ranking_ignores_holdout_and_excludes_full_data_models():
    def m(name, score, sample):
        return SimpleNamespace(
            id=name, sample_pct=sample, metrics={"LogLoss": {"validation": score}}
        )

    assert [x.id for x in eligible_models([m("safe", 0.4, 60), m("refit", 0.1, 100)], 60)] == [
        "safe"
    ]
    records = [
        {
            "model_id": "A",
            "validation": {"average_precision": 0.8, "log_loss": 0.4},
            "test": {"average_precision": 0.1},
        },
        {
            "model_id": "B",
            "validation": {"average_precision": 0.7, "log_loss": 0.3},
            "test": {"average_precision": 0.99},
        },
    ]
    assert winner_from_validation(records)["model_id"] == "A"


def test_daily_budget_not_global_budget():
    f = pd.DataFrame(
        {
            "date": ["a"] * 10 + ["b"] * 10,
            "niin": list(map(str, range(20))),
            "location": "A",
            TARGET: [1] + [0] * 9 + [1] + [0] * 9,
        }
    )
    p = [0.95] + [0.9] * 9 + [0.2] + [0.1] * 9
    result = metrics(f, p, 0.1)
    assert result["precision_at_daily_budget"] == 1
    assert result["recall_at_daily_budget"] == 1
    assert not passes_gate(
        {"average_precision": 0.2, "log_loss": 0.3}, {"average_precision": 0.4}, {"log_loss": 0.5}
    )


def test_deployment_probabilities_never_fallback_to_class_labels():
    class Client:
        def post(self, *args, **kwargs):
            return SimpleNamespace(json=lambda: {"data": [{"prediction": 1}]})

    with pytest.raises(ValueError, match="positive-class"):
        predict_rows(Client(), "demo", [{"closing_stock": 1}])


def test_deployment_positive_class_and_reordering():
    class Client:
        def post(self, *args, **kwargs):
            assert "closing_stock" in kwargs["keep_attrs"]
            return SimpleNamespace(
                json=lambda: {
                    "data": [
                        {"rowId": 1, "predictionValues": [{"label": 1, "value": 0.8}]},
                        {"rowId": 0, "predictionValues": [{"label": "1.0", "value": 0.1}]},
                    ]
                }
            )

    assert np.allclose(predict_rows(Client(), "demo", [{}, {}]), [0.1, 0.8])
