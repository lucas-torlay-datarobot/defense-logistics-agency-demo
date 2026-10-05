import json

import pandas as pd

from dla_prediction.cli import main
from dla_prediction.features import FEATURES, TARGET


def test_local_dry_run_needs_no_cloud_credentials(inputs, tmp_path, monkeypatch):
    import dla_prediction.cli as cli

    monkeypatch.setattr(
        cli, "connect", lambda *a: (_ for _ in ()).throw(AssertionError("No cloud access expected"))
    )
    d, o = inputs
    daily, orders = tmp_path / "daily.csv", tmp_path / "orders.csv"
    d.to_csv(daily, index=False)
    o.to_csv(orders, index=False)
    argv = [
        "--daily-csv",
        str(daily),
        "--orders-csv",
        str(orders),
        "--dry-run",
        "--output-dir",
        str(tmp_path / "out"),
    ]
    run = main(argv)
    training = pd.read_csv(run / "SYNTHETIC_train_validation.csv")
    assert set(training.partition) == {"train", "validation"}
    assert list(training) == FEATURES + [TARGET, "partition"]
    manifest = json.loads((run / "manifest.json").read_text())
    assert manifest["is_synthetic"] is True
    assert main(argv) == run
