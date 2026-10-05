#!/usr/bin/env python
"""Score item-location rows through the evaluated DataRobot deployment."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "prediction"))

from dla_prediction.predict import main

if __name__ == "__main__":
    main()
