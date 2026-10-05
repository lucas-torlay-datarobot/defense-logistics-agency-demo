"""Validation ranking and a separately evaluated final-period audit."""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, log_loss, roc_auc_score

from .features import TARGET, require


def baseline_score(frame):
    """Expected supply minus 14-day demand, scaled by demand uncertainty.

    Expected arrivals are a planning assumption, not a causal transfer simulation.
    Higher score means greater shortage pressure. No fitted item-specific values.
    """
    scale = np.sqrt(14 * (frame.demand_std_28d.to_numpy() ** 2 + 1))
    return (-frame.coverage_margin_14d.to_numpy() / scale).clip(-30, 30)


class CoverageBaseline:
    """Fit just a sigmoid calibration on training data for probability comparison."""

    def fit(self, train):
        self.calibrator = LogisticRegression(C=1.0, random_state=42)
        self.calibrator.fit(baseline_score(train).reshape(-1, 1), train[TARGET])
        self.prevalence = float(train[TARGET].mean())
        return self

    def predict(self, frame):
        return self.calibrator.predict_proba(baseline_score(frame).reshape(-1, 1))[:, 1]


def metrics(frame, probabilities, review_fraction=0.10):
    p = np.asarray(probabilities, dtype=float)
    require(len(p) == len(frame) and p.ndim == 1, "Prediction row mismatch")
    require(np.isfinite(p).all() and ((p >= 0) & (p <= 1)).all(), "Invalid probabilities")
    y = frame[TARGET].to_numpy()
    require(len(np.unique(y)) == 2, "Evaluation needs both classes")
    ranked = frame[["date", "niin", "location", TARGET]].copy()
    ranked["probability"] = p
    ranked = ranked.sort_values(
        ["date", "probability", "niin", "location"], ascending=[True, False, True, True]
    )
    picks = ranked.groupby("date", sort=False).head(0)  # preserve schema
    blocks = []
    for _, group in ranked.groupby("date", sort=False):
        blocks.append(group.head(max(1, int(np.ceil(len(group) * review_fraction)))))
    if blocks:
        picks = pd.concat(blocks)
    positives = int(picks[TARGET].sum())
    return {
        "average_precision": float(average_precision_score(y, p)),
        "log_loss": float(log_loss(y, np.clip(p, 1e-7, 1 - 1e-7), labels=[0, 1])),
        "brier": float(brier_score_loss(y, p)),
        "roc_auc": float(roc_auc_score(y, p)),
        "precision_at_daily_budget": float(picks[TARGET].mean()),
        "recall_at_daily_budget": float(positives / y.sum()),
        "review_fraction": review_fraction,
        "rows": len(y),
        "positive_fraction": float(y.mean()),
    }


def positive_probabilities(predictions, expected_rows):
    """SDK project predictions: explicitly select class 1 and restore row order."""
    require(len(predictions) == expected_rows, "DataRobot prediction count mismatch")
    data = predictions.copy()
    if "row_id" in data:
        require(
            sorted(data.row_id.tolist()) == list(range(expected_rows)), "Invalid prediction row IDs"
        )
        data = data.sort_values("row_id")
    names = []
    for name in data:
        if name.startswith("class_"):
            try:
                if float(name[6:]) == 1.0:
                    names.append(name)
            except ValueError:
                pass
    require(len(names) == 1, "No unambiguous class_1 probability; refusing to use class labels")
    p = data[names[0]].to_numpy(dtype=float)
    require(np.isfinite(p).all() and ((p >= 0) & (p <= 1)).all(), "Invalid positive probabilities")
    return p


def eligible_models(models, max_train_pct):
    """Exclude models trained beyond the training partition, including full-data refits."""
    result = []
    for model in models:
        score = (model.metrics or {}).get("LogLoss", {}).get("validation")
        sample = getattr(model, "sample_pct", None)
        if (
            score is not None
            and np.isfinite(score)
            and sample is not None
            and sample <= max_train_pct + 1e-6
        ):
            result.append(model)
    return sorted(result, key=lambda m: (m.metrics["LogLoss"]["validation"], m.id))


def winner_from_validation(records):
    require(bool(records), "No successfully evaluated models")
    return sorted(
        records,
        key=lambda r: (
            -r["validation"]["average_precision"],
            r["validation"]["log_loss"],
            r["model_id"],
        ),
    )[0]


def passes_gate(model_metrics, baseline_metrics, prevalence_metrics):
    return (
        model_metrics["average_precision"] > baseline_metrics["average_precision"]
        and model_metrics["log_loss"] < prevalence_metrics["log_loss"]
    )
