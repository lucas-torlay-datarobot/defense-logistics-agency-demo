import numpy as np
import pandas as pd
import pytest

from dla_prediction.features import (
    FEATURES,
    HORIZON,
    KEYS,
    TARGET,
    add_targets,
    build_features,
    split_training,
    validate_inputs,
)


def test_balances_and_scenario_validation(inputs):
    d, o = inputs
    bad = d.copy()
    bad.loc[0, "closing_stock"] += 1
    with pytest.raises(ValueError, match="balance"):
        validate_inputs(bad, o)
    bad = o.copy()
    bad["simulation_id"] = "other"
    with pytest.raises(ValueError, match="simulation_id"):
        validate_inputs(d, bad)
    with pytest.raises(ValueError, match="Duplicate daily"):
        validate_inputs(pd.concat([d, d.iloc[:1]]), o)
    bad = o.copy()
    bad.loc[0, "quantity"] += 2
    with pytest.raises(ValueError, match="receipts disagree"):
        validate_inputs(d, bad)


def test_horizon_excludes_today_and_censors_tail():
    dates = pd.date_range("2025-01-01", periods=40)
    d = pd.DataFrame(
        {"date": dates, "niin": "000000001", "location": "A", "unfulfilled_quantity": 0}
    )
    d.loc[14, "unfulfilled_quantity"] = 1
    result = add_targets(d[KEYS], d)
    assert result.loc[0, TARGET] == 1  # event exactly t+14 is included
    assert result.loc[13, TARGET] == 1
    assert result.loc[14, TARGET] == 0  # today's event excluded
    assert result[TARGET].tail(HORIZON).isna().all()
    assert result.loc[25, TARGET] == 0


def test_future_changes_cannot_change_past_features(inputs):
    d, o = inputs
    cutoff = pd.Timestamp("2025-04-01")
    before = build_features(d, o, as_of=cutoff)
    changed_d, changed_o = d.copy(), o.copy()
    changed_d.loc[changed_d.date > cutoff, "requested_quantity"] = 99999
    # Even eventual receipts known in the completed simulation must stay hidden.
    future_receipts = changed_o.actual_receipt_date > cutoff
    changed_o.loc[future_receipts, "actual_receipt_date"] += pd.Timedelta(days=50)
    changed_o.loc[changed_o.order_date > cutoff, "quantity"] = 99999
    changed_o["status_at_simulation_end"] = "INVENTED FUTURE FLAG"
    after = build_features(changed_d, changed_o, as_of=cutoff)
    pd.testing.assert_frame_equal(before, after)


def test_received_and_open_orders_as_of(inputs):
    d, o = inputs
    features = build_features(d, o, as_of="2025-01-15")
    row = features.iloc[-1]
    known = o[(o.niin == row.niin) & (o.destination == row.location) & (o.order_date <= row.date)]
    pending = known[known.actual_receipt_date.isna() | (known.actual_receipt_date > row.date)]
    assert row.open_order_units == pending.quantity.sum()
    assert row.open_order_count == len(pending)
    received = known[known.actual_receipt_date.notna() & (known.actual_receipt_date <= row.date)]
    assert row.observed_receipt_count == len(received)


def test_training_scoring_parity_and_splits(inputs):
    d, o = inputs
    features = build_features(d, o)
    historical = build_features(d, o, as_of="2025-07-01")
    pd.testing.assert_frame_equal(
        features[features.date <= "2025-07-01"].reset_index(drop=True), historical
    )
    table, report = split_training(add_targets(features, d))
    for a, b in [("train", "validation"), ("validation", "test")]:
        assert pd.Timestamp(report[a]["end"]) + pd.Timedelta(days=14) < pd.Timestamp(
            report[b]["start"]
        )
    assert table.date.min() == d.date.min() + pd.Timedelta(days=60)
    assert table.date.max() == d.date.max() - pd.Timedelta(days=14)
    assert not set(
        ["niin", "simulation_id", "date", "actual_receipt_date", "is_synthetic", TARGET]
    ) & set(FEATURES)
    assert np.isfinite(table[FEATURES].to_numpy()).all()


def test_gap_and_missing_provenance_rejected(inputs):
    d, o = inputs
    with pytest.raises(ValueError):
        validate_inputs(d.drop(index=100), o)
    bad = d.copy()
    bad.loc[0, "source_items_dataset_id"] = None
    with pytest.raises(ValueError, match="non-null"):
        validate_inputs(bad, o)
