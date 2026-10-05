#!/usr/bin/env python
"""Serve the built React app and API from one Codespace port."""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for directory in ("prediction", "agent", "fastapi_server"):
    sys.path.insert(0, str(ROOT / directory))

if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=False)
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    if not (ROOT / "frontend_web/dist/index.html").exists():
        parser.error(
            "Build the React frontend first: npm --prefix frontend_web ci && npm --prefix frontend_web run build"
        )
    import uvicorn
    from app.main import create_app

    uvicorn.run(create_app(), host=args.host, port=args.port, access_log=False)
