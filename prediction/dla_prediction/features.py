"""Historical end-of-day features shared by training and scoring.

Only requested demand (not censored fulfilled demand) estimates consumption.
Actual order receipts affect a feature only on/after their observed receipt date.
Identifiers and synthetic scenario metadata never enter the model matrix.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

TARGET = "shortage_next_14d"
HORIZON = 14
KEYS = ["date", "niin", "location"]
FEATURES = [
    "closing_stock",
    "demand_mean_7d",
    "demand_mean_14d",
    "demand_mean_28d",
    "demand_std_28d",
    "demand_max_28d",
    "zero_demand_fraction_28d",
    "demand_trend_ratio",
    "unfulfilled_units_7d",
    "shortage_days_28d",
    "days_of_cover",
    "no_recent_demand",
    "open_order_count",
    "open_order_units",
    "expected_receipts_7d",
    "expected_receipts_14d",
    "overdue_order_units",
    "oldest_open_order_days",
    "next_expected_receipt_days",
    "no_expected_receipt",
    "observed_lead_mean_days",
    "observed_late_fraction",
    "observed_receipt_count",
    "coverage_margin_14d",
    "day_of_week",
    "month",
]
DAILY_NUMERIC = [
    "opening_stock",
    "requested_quantity",
    "fulfilled_quantity",
    "received_quantity",
    "closing_stock",
    "unfulfilled_quantity",
]
PROVENANCE = ["simulation_id", "source_items_dataset_id", "source_items_version_id"]


def require(condition, message):
    if not bool(condition):
        raise ValueError(message)


def validate_inputs(daily: pd.DataFrame, orders: pd.DataFrame):
    """Fail before modeling on mismatched scenarios or broken accounting."""
    d, o = daily.copy(), orders.copy()
    for frame, required in [
        (d, KEYS + DAILY_NUMERIC + ["is_synthetic"] + PROVENANCE),
        (
            o,
            [
                "order_id",
                "niin",
                "destination",
                "order_date",
                "quantity",
                "expected_receipt_date",
                "actual_receipt_date",
                "is_synthetic",
            ]
            + PROVENANCE,
        ),
    ]:
        require(set(required).issubset(frame), f"Missing columns: {set(required) - set(frame)}")
        require(len(frame) > 0, "Input dataset is empty")
        synthetic = frame.is_synthetic.astype(str).str.lower()
        require(synthetic.isin(["true", "1"]).all(), "Expected explicitly SYNTHETIC inputs")
        for column in PROVENANCE:
            require(
                frame[column].notna().all() and frame[column].nunique() == 1,
                f"Expected one non-null {column}",
            )
        frame["niin"] = frame.niin.astype(str).str.strip()
        require(frame.niin.str.fullmatch(r"[0-9]{9}").all(), "NIIN must remain 9-digit text")
    for column in PROVENANCE:
        require(
            str(d[column].iloc[0]) == str(o[column].iloc[0]),
            f"Daily and orders disagree on {column}",
        )
    for frame, columns in [
        (d, ["date"]),
        (o, ["order_date", "expected_receipt_date", "actual_receipt_date"]),
    ]:
        for column in columns:
            frame[column] = pd.to_datetime(frame[column].replace("", pd.NA), errors="raise")
            if column != "actual_receipt_date":
                require(frame[column].notna().all(), f"Missing {column}")
            require(
                (frame[column].dropna() == frame[column].dropna().dt.normalize()).all(),
                f"{column} must contain dates at midnight",
            )
    for frame, columns in [(d, DAILY_NUMERIC), (o, ["quantity"])]:
        for column in columns:
            frame[column] = pd.to_numeric(frame[column], errors="raise")
            require(
                np.isfinite(frame[column]).all()
                and (frame[column] >= 0).all()
                and (frame[column] % 1 == 0).all(),
                f"Invalid quantity: {column}",
            )
    require((o.quantity > 0).all(), "Order quantity must be positive")
    require(not d.duplicated(KEYS).any(), "Duplicate daily keys")
    require(o.order_id.notna().all() and o.order_id.is_unique, "Duplicate/missing order IDs")
    require(d.location.notna().all() and o.destination.notna().all(), "Missing locations")
    require(
        (d.closing_stock == d.opening_stock + d.received_quantity - d.fulfilled_quantity).all(),
        "Inventory balance does not reconcile",
    )
    require(
        (d.requested_quantity == d.fulfilled_quantity + d.unfulfilled_quantity).all(),
        "Demand balance does not reconcile",
    )
    require(
        (d.fulfilled_quantity <= d.opening_stock + d.received_quantity).all(),
        "Fulfilled quantity exceeds available inventory",
    )
    d = d.sort_values(["niin", "location", "date"]).reset_index(drop=True)
    g = d.groupby(["niin", "location"], sort=False)
    prior = g.closing_stock.shift()
    require(
        (d.loc[prior.notna(), "opening_stock"] == prior.dropna()).all(), "Stock continuity broken"
    )
    delta = g.date.diff().dropna()
    require((delta == pd.Timedelta(days=1)).all(), "Daily history contains gaps")
    bounds = g.date.agg(["min", "max"])
    require(
        bounds["min"].nunique() == bounds["max"].nunique() == 1,
        "All item-location series must share the same date range",
    )
    require((o.expected_receipt_date > o.order_date).all(), "Expected receipt before order")
    received = o.actual_receipt_date.notna()
    require(
        (o.loc[received, "actual_receipt_date"] > o.loc[received, "order_date"]).all(),
        "Receipt precedes order placement",
    )
    require(
        (o.order_date >= d.date.min()).all() and (o.order_date <= d.date.max()).all(),
        "Orders outside the simulation date range",
    )
    require(
        (o.loc[received, "actual_receipt_date"] <= d.date.max()).all(),
        "Actual receipt beyond observation horizon must be blank",
    )
    pairs = set(zip(d.niin, d.location))
    require(
        set(zip(o.niin, o.destination)).issubset(pairs), "Order references unknown item/location"
    )
    receipts = (
        o.loc[received].groupby(["actual_receipt_date", "niin", "destination"]).quantity.sum()
    )
    actual = d.set_index(KEYS).received_quantity
    receipts.index.names = actual.index.names
    require(receipts.index.isin(actual.index).all(), "Receipt outside daily observation range")
    require(
        (actual == receipts.reindex(actual.index, fill_value=0)).all(),
        "Orders and receipts disagree",
    )
    return d, o.sort_values("order_date").reset_index(drop=True)


def build_features(daily: pd.DataFrame, orders: pd.DataFrame, as_of=None) -> pd.DataFrame:
    """Build features through as_of, including that day's demand and end-of-day orders.

    Inputs must first pass validate_inputs. This function intentionally does not
    inspect order status-at-end, simulation parameters, or future realized demand.
    It is also used for the final (unlabeled) day and historical scoring.
    """
    cutoff = pd.Timestamp(as_of) if as_of is not None else daily.date.max()
    parts = []
    og = {(n, loc): group for (n, loc), group in orders.groupby(["niin", "destination"])}
    for (niin, location), group in daily[daily.date <= cutoff].groupby(
        ["niin", "location"], sort=True
    ):
        g = group.sort_values("date").reset_index(drop=True)
        f = g[KEYS].copy()
        f["closing_stock"] = g.closing_stock
        req = g.requested_quantity
        for window in (7, 14, 28):
            f[f"demand_mean_{window}d"] = req.rolling(window, min_periods=1).mean()
        f["demand_std_28d"] = req.rolling(28, min_periods=1).std(ddof=0)
        f["demand_max_28d"] = req.rolling(28, min_periods=1).max()
        f["zero_demand_fraction_28d"] = (req == 0).rolling(28, min_periods=1).mean()
        f["demand_trend_ratio"] = (f.demand_mean_7d + 0.1) / (f.demand_mean_28d + 0.1)
        f["unfulfilled_units_7d"] = g.unfulfilled_quantity.rolling(7, min_periods=1).sum()
        f["shortage_days_28d"] = (g.unfulfilled_quantity > 0).rolling(28, min_periods=1).sum()
        f["no_recent_demand"] = (f.demand_mean_28d == 0).astype(int)
        f["days_of_cover"] = (
            (f.closing_stock / f.demand_mean_28d.replace(0, np.nan)).fillna(365).clip(upper=365)
        )
        relevant = og.get((niin, location), orders.iloc[:0])
        # Vectorize within one item/location: ~365 dates x ~50 orders, not a
        # many-to-many merge of the whole dataset. Bound memory per series.
        dates = g.date.to_numpy(dtype="datetime64[D]")[:, None]
        placed = relevant.order_date.to_numpy(dtype="datetime64[D]")[None, :]
        due = relevant.expected_receipt_date.to_numpy(dtype="datetime64[D]")[None, :]
        actual = relevant.actual_receipt_date.to_numpy(dtype="datetime64[D]")[None, :]
        quantity = relevant.quantity.to_numpy()[None, :]
        known = placed <= dates
        received = known & ~np.isnat(actual) & (actual <= dates)
        pending = known & (np.isnat(actual) | (actual > dates))
        future = pending & (due > dates)
        f["open_order_count"] = pending.sum(axis=1)
        f["open_order_units"] = (pending * quantity).sum(axis=1)
        for window in (7, 14):
            f[f"expected_receipts_{window}d"] = (
                (future & (due <= dates + np.timedelta64(window, "D"))) * quantity
            ).sum(axis=1)
        f["overdue_order_units"] = ((pending & (due <= dates)) * quantity).sum(axis=1)
        age = (dates - placed).astype("timedelta64[D]").astype(float)
        f["oldest_open_order_days"] = np.where(pending, age, 0).max(axis=1, initial=0)
        until_due = (due - dates).astype("timedelta64[D]").astype(float)
        f["next_expected_receipt_days"] = np.where(future, until_due, 365).min(axis=1, initial=365)
        f["no_expected_receipt"] = (~future.any(axis=1)).astype(int)
        lead = (actual - placed).astype("timedelta64[D]").astype(float)
        count = received.sum(axis=1)
        f["observed_lead_mean_days"] = np.where(received, lead, 0).sum(axis=1) / np.maximum(
            count, 1
        )
        f["observed_late_fraction"] = (received & (actual > due)).sum(axis=1) / np.maximum(count, 1)
        f["observed_receipt_count"] = count
        f["coverage_margin_14d"] = (
            f.closing_stock + f.expected_receipts_14d - HORIZON * f.demand_mean_28d
        )
        f["day_of_week"] = g.date.dt.dayofweek
        f["month"] = g.date.dt.month
        parts.append(f)
    require(bool(parts), "No observations at requested as-of date")
    result = pd.concat(parts, ignore_index=True)
    require(np.isfinite(result[FEATURES].to_numpy()).all(), "Nonfinite model feature")
    return result


def add_targets(features: pd.DataFrame, daily: pd.DataFrame) -> pd.DataFrame:
    """Label t+1..t+14; leave incomplete horizons missing, never negative."""
    labels = []
    for _, group in daily.groupby(["niin", "location"], sort=True):
        g = group.sort_values("date").reset_index(drop=True)
        # Reverse rolling, shifted once, excludes today's shortage.
        y = (g.unfulfilled_quantity > 0).astype(int)
        future = y.iloc[::-1].rolling(HORIZON, min_periods=HORIZON).max().iloc[::-1].shift(-1)
        out = g[KEYS].copy()
        out[TARGET] = future.to_numpy()
        labels.append(out)
    return features.merge(pd.concat(labels), on=KEYS, how="left", validate="one_to_one")


def split_training(frame, warmup_days=60, validation_days=60, test_days=45):
    """Global calendar splits with a 14-day purge before validation and test.

    The final test is never sent to Autopilot, and is evaluated only after the
    validation winner is frozen. Same items across dates matches repeat daily use.
    """
    start = frame.date.min() + pd.Timedelta(days=warmup_days)
    usable = frame[frame[TARGET].notna() & (frame.date >= start)].copy()
    require(not usable.empty, "No complete training horizons after startup exclusion")
    test_start = usable.date.max() - pd.Timedelta(days=test_days - 1)
    val_end = test_start - pd.Timedelta(days=HORIZON + 1)
    val_start = val_end - pd.Timedelta(days=validation_days - 1)
    train_end = val_start - pd.Timedelta(days=HORIZON + 1)
    require(
        (train_end - start).days + 1 >= 90,
        "Need at least 90 training days after warmup and purge gaps",
    )
    usable["partition"] = "purged"
    usable.loc[usable.date <= train_end, "partition"] = "train"
    usable.loc[usable.date.between(val_start, val_end), "partition"] = "validation"
    usable.loc[usable.date >= test_start, "partition"] = "test"
    usable = usable[usable.partition != "purged"].reset_index(drop=True)
    usable[TARGET] = usable[TARGET].astype(int)
    report = {}
    for name in ("train", "validation", "test"):
        part = usable[usable.partition == name]
        require(part[TARGET].nunique() == 2, f"{name} must contain both outcomes")
        require(
            part[TARGET].value_counts().min() >= 20, f"Too few positive/negative rows in {name}"
        )
        report[name] = {
            "rows": len(part),
            "start": str(part.date.min().date()),
            "end": str(part.date.max().date()),
            "positive_fraction": float(part[TARGET].mean()),
        }
    return usable, report
