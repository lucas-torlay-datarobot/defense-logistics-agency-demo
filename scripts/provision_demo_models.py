#!/usr/bin/env python
"""Prepare, train, rank and optionally deploy the DLA synthetic shortage model.

From the repo root:
    uv run --project prediction python scripts/provision_demo_models.py --dry-run
    uv run --project prediction python scripts/provision_demo_models.py
    uv run --project prediction python scripts/provision_demo_models.py --deploy

Uses Codespace/.env credentials; never stores tokens in generated reports.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "prediction"))

from dla_prediction.cli import main

if __name__ == "__main__":
    main()
