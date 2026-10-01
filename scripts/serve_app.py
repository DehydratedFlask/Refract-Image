"""Serve the built Refract Image UI over loopback, for the native app's window.

The service is a JSON API only; the React app is a static bundle in ``app/dist``. The Swift app
spawns this beside the service and loads its URL in the web view, rather than reading the bundle
with ``loadFileURL`` — a ``file://`` page may not call the API cross-origin without a private
WebKit preference, while a loopback origin needs no exceptions at all.

Two deliberate limits: it binds loopback only, and it exits when its parent does. An app that is
force-quit therefore cannot leave a web server listening on the machine.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import threading
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

READY_PREFIX = "REFRACT_STATIC_READY "


class Handler(SimpleHTTPRequestHandler):
    """Static files plus a single-page fallback, so any route lands on the app."""

    def end_headers(self) -> None:
        # A rebuild replaces the bundle in place; a cached copy from a previous launch would
        # silently outlive it.
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, *_args) -> None:  # noqa: D102 - the launcher owns the output
        pass

    def send_head(self):  # noqa: ANN201 - mirrors the stdlib signature
        # A request for a path the bundle does not contain is treated as an app route rather
        # than a 404, so a deep link renders the UI. This has to happen *before* delegating:
        # the stdlib writes the 404 as part of send_head, so a rewrite afterwards is too late.
        # A missing file that looks like an asset (it has an extension) still 404s.
        if not os.path.exists(self.translate_path(self.path)) and "." not in self.path.rsplit("/", 1)[-1]:
            self.path = "/index.html"
        return super().send_head()


def _watch_parent(parent_pid: int) -> None:
    """Exit when the launcher disappears, even if it was killed outright."""
    while True:
        time.sleep(1.0)
        try:
            os.kill(parent_pid, 0)
        except OSError:
            os._exit(0)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="serve-app", description="Serve the built Refract Image UI")
    parser.add_argument("--directory", required=True, help="directory holding index.html")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0, help="0 picks a free port")
    parser.add_argument("--parent-pid", type=int, default=0, help="exit when this process does")
    args = parser.parse_args(argv)

    root = Path(args.directory)
    if not (root / "index.html").is_file():
        print(f"no built UI at {root / 'index.html'}", file=sys.stderr)
        return 1

    httpd = ThreadingHTTPServer((args.host, args.port), partial(Handler, directory=str(root)))
    httpd.daemon_threads = True
    port = httpd.server_address[1]
    # The launcher reads this one line to learn the port it actually got.
    print(READY_PREFIX + json.dumps({"port": port, "host": args.host, "root": str(root)}), flush=True)

    if args.parent_pid:
        threading.Thread(target=_watch_parent, args=(args.parent_pid,), daemon=True).start()

    def shutdown(_signum, _frame) -> None:
        raise SystemExit(0)

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, shutdown)

    try:
        httpd.serve_forever()
    except SystemExit:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
