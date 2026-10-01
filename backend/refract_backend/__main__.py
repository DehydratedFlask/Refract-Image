"""Entry point: `python -m refract_backend`.

The app spawns this and reads one line of stdout to learn where the service landed:

    REFRACT_READY {"port": 51234, "token": "…", "pid": 4242, "mock": false}

Binding the socket ourselves (rather than asking uvicorn for port 0 and parsing its logs)
means the announced port is the port that is actually open, with no race between the two.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import sys
import threading
import time

READY_PREFIX = "REFRACT_READY "


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="refract_backend", description="Refract Image local inference service")
    parser.add_argument("--host", default="127.0.0.1", help="bind address (loopback only by default)")
    parser.add_argument("--port", type=int, default=0, help="0 picks a free port")
    parser.add_argument("--token", default=None, help="bearer token; generated when omitted")
    parser.add_argument("--mock", action="store_true", help="force the placeholder runner")
    parser.add_argument("--log-level", default="warning")
    parser.add_argument("--no-announce", action="store_true", help="stay quiet on stdout (for tests)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    # First thing in the process: export where caches live (see paths.configure_environment)
    # while it is still early enough to affect huggingface_hub's own constants.
    from .paths import configure_environment, data_root, ensure_dirs

    configure_environment()

    from .app import create_app, default_token

    ensure_dirs()
    token = args.token or default_token()
    app = create_app(token=token, force_mock=args.mock)

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((args.host, args.port))
    sock.listen(128)
    port = sock.getsockname()[1]

    state = app.state.refract
    payload = {
        "port": port,
        "host": args.host,
        "token": token,
        "pid": os.getpid(),
        "mock": bool(state.mock),
        "mflux": None if state.mock else _safe_version(),
        "data_root": str(data_root()),
    }
    if not args.no_announce:
        print(READY_PREFIX + json.dumps(payload), flush=True)

    def shutdown(_signum, _frame) -> None:
        state.queue.shutdown(timeout=3)
        raise SystemExit(0)

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, shutdown)

    # Watchdog: if the parent app dies, this process must not linger holding 20 GB.
    if os.environ.get("REFRACT_PARENT_PID"):
        threading.Thread(target=_watch_parent, args=(int(os.environ["REFRACT_PARENT_PID"]), state), daemon=True).start()

    import uvicorn

    config = uvicorn.Config(app, log_level=args.log_level, access_log=False, lifespan="on")
    server = uvicorn.Server(config)
    try:
        server.run(sockets=[sock])
    except SystemExit:
        pass
    return 0


def _safe_version() -> str | None:
    from .sysinfo import mflux_version

    version = mflux_version()
    return None if version == "not installed" else version


def _watch_parent(parent_pid: int, state) -> None:
    """Exit when the app that spawned this service disappears."""
    while True:
        time.sleep(2.0)
        try:
            os.kill(parent_pid, 0)
        except OSError:
            state.queue.shutdown(timeout=2)
            os._exit(0)


if __name__ == "__main__":
    sys.exit(main())
