from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def scenario(n_items=6, days=365):
    """Small independent accounting fixture in the notebook's published schema."""
    rng = np.random.default_rng(123)
    daily, orders = [], []
    start = pd.Timestamp("2025-01-01")
    provenance = {
        "is_synthetic": True,
        "simulation_id": "fixture",
        "source_items_dataset_id": "real-source-id",
        "source_items_version_id": "v1",
    }
    for item in range(n_items):
        niin = f"{item + 1:09d}"
        for location in ["SYNTHETIC_DEPOT_A", "SYNTHETIC_DEPOT_B", "SYNTHETIC_DEPOT_C"]:
            stock, pending = 25, []
            for day in range(days):
                date = start + pd.Timedelta(days=day)
                opening = stock
                received = sum(q for arrival, q in pending if arrival == day)
                pending = [(a, q) for a, q in pending if a > day]
                demand = int(rng.poisson(3.5 + item / 2))
                fulfilled = min(demand, stock + received)
                stock += received - fulfilled
                daily.append(
                    {
                        **provenance,
                        "date": date,
                        "niin": niin,
                        "location": location,
                        "opening_stock": opening,
                        "requested_quantity": demand,
                        "fulfilled_quantity": fulfilled,
                        "received_quantity": received,
                        "closing_stock": stock,
                        "unfulfilled_quantity": demand - fulfilled,
                    }
                )
                if day % 7 == 0:
                    supply_factor = {
                        "SYNTHETIC_DEPOT_A": 0.75,
                        "SYNTHETIC_DEPOT_B": 1.25,
                        "SYNTHETIC_DEPOT_C": 1.0,
                    }[location]
                    quantity = int(7 * (3.5 + item / 2) * supply_factor + rng.integers(-4, 5))
                    arrival = day + 5 + int(rng.choice([0, 0, 0, 1, 7]))
                    pending.append((arrival, quantity))
                    orders.append(
                        {
                            **provenance,
                            "order_id": f"SYN-{len(orders)}",
                            "niin": niin,
                            "destination": location,
                            "order_date": date,
                            "quantity": quantity,
                            "expected_receipt_date": date + pd.Timedelta(days=5),
                            "actual_receipt_date": start + pd.Timedelta(days=arrival)
                            if arrival < days
                            else pd.NaT,
                        }
                    )
    return pd.DataFrame(daily), pd.DataFrame(orders)


@pytest.fixture(scope="session")
def inputs():
    from dla_prediction.features import validate_inputs

    return validate_inputs(*scenario())
