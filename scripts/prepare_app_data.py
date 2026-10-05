#!/usr/bin/env python
"""Run with ./.venv-dla/bin/python -I scripts/prepare_app_data.py --run-dir ..."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for directory in ("prediction", "agent", "fastapi_server"):
    sys.path.insert(0, str(ROOT / directory))

from app.prepare import main  # noqa: E402

if __name__ == "__main__":
    main()
