"""Launch ScoreShift.

    ScoreShift.exe                  the desktop app
    ScoreShift.exe --server         the JSON API, for the phone app
"""
from __future__ import annotations

import argparse
import os
import sys


def _attach_console() -> None:
    """A windowed build has no console; make one so server logs are visible."""
    if not sys.platform.startswith("win"):
        return
    try:
        import ctypes  # noqa: PLC0415
        if ctypes.windll.kernel32.GetConsoleWindow() == 0:
            ctypes.windll.kernel32.AllocConsole()
        sys.stdout = open("CONOUT$", "w", buffering=1)  # noqa: SIM115
        sys.stderr = open("CONOUT$", "w", buffering=1)  # noqa: SIM115
        sys.stdin = open("CONIN$", "r")                 # noqa: SIM115
    except Exception:  # noqa: BLE001
        pass


def main(argv: list[str] | None = None) -> int:
    # A windowed build has no stdout, and argparse writes to it for --help and
    # for its error messages. Without a console that turns into a crash dialog,
    # so give it somewhere to write before parsing anything.
    if (sys.stdout is None or sys.stderr is None) and (argv or sys.argv[1:]):
        _attach_console()
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")   # noqa: SIM115
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w")   # noqa: SIM115

    parser = argparse.ArgumentParser(prog="ScoreShift", add_help=True)
    parser.add_argument("--server", action="store_true",
                        help="run the JSON API instead of the desktop window")
    parser.add_argument("--host", default="127.0.0.1",
                        help="use 0.0.0.0 to let other devices connect")
    parser.add_argument("--port", type=int, default=8765)
    args, _ = parser.parse_known_args(argv)

    if args.server:
        _attach_console()
        try:
            import uvicorn  # noqa: PLC0415
        except ImportError:
            print("The API needs the server extras:\n"
                  "    pip install -r requirements-server.txt", file=sys.stderr)
            return 1
        from scoreshift.service.app import create_app  # noqa: PLC0415
        print(f"ScoreShift API on http://{args.host}:{args.port}  "
              f"(docs at /docs)  -  Ctrl+C to stop")
        uvicorn.run(create_app(), host=args.host, port=args.port)
        return 0

    from scoreshift.ui.main_window import run  # noqa: PLC0415
    return run()


if __name__ == "__main__":
    sys.exit(main())
