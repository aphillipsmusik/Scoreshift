"""Run the ScoreShift API:  python -m scoreshift.service --port 8765"""
from __future__ import annotations

import argparse
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="ScoreShift API server")
    parser.add_argument("--host", default="127.0.0.1",
                        help="0.0.0.0 to accept connections from your phone")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    try:
        import uvicorn
    except ImportError:
        print("Install the server extras first:\n"
              "    pip install -r requirements-server.txt", file=sys.stderr)
        return 1

    uvicorn.run("scoreshift.service.app:app", host=args.host, port=args.port,
                reload=args.reload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
