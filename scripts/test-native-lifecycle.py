#!/usr/bin/env python3
"""Run after building the native app; no automation/accessibility permission required."""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
marker = ROOT / ".refract-workspace"
WORKSPACE = (ROOT / marker.read_text().strip()).resolve() if marker.is_file() else ROOT


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def descendants(pid: int) -> list[int]:
    rows = subprocess.check_output(["ps", "-axo", "pid=,ppid="], text=True)
    pairs = [tuple(map(int, line.split())) for line in rows.splitlines() if len(line.split()) == 2]
    found = {pid}
    while True:
        new = {child for child, parent in pairs if parent in found} - found
        if not new:
            return sorted(found - {pid})
        found |= new


def run_case(binary: Path, case: str) -> None:
    with tempfile.TemporaryDirectory(prefix="refract-native-lifecycle-") as temporary:
        data = Path(temporary)
        env = {**os.environ, "REFRACT_ROOT": str(data), "REFRACT_REPO_ROOT": str(ROOT),
               "REFRACT_LOG_DIR": str(data / "logs"), "REFRACT_MOCK_STEP_SECONDS": "2"}
        args = [str(binary), "--mock", "--lifecycle-close"]
        if case == "crash":
            # A smoke instance skips the single-instance guard, but its long mock
            # step ensures it is still running when the test terminates the app.
            args = [str(binary), "--mock", "--smoke"]
        children: list[int] = []
        with (data / "app.log").open("w") as log:
            process = subprocess.Popen(args, env=env, stdout=log, stderr=log)
            try:
                backend_log = data / "logs" / "native-backend.log"
                deadline = time.monotonic() + 30
                info = None
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise AssertionError(f"app exited before readiness: {(data / 'app.log').read_text()}")
                    if backend_log.exists():
                        for line in backend_log.read_text().splitlines():
                            if line.startswith("REFRACT_READY "):
                                info = json.loads(line.split(" ", 1)[1])
                    if info:
                        break
                    time.sleep(0.1)
                assert info, "backend did not announce readiness"
                children = descendants(process.pid)
                assert info["pid"] in children, "backend is not owned by the native app"
                # Machine-status requests may briefly spawn sysctl helpers. They
                # are valid children, but a legacy UI server must never be started.
                commands = subprocess.check_output(["ps", "-p", ",".join(map(str, children)), "-o", "command="], text=True)
                assert "serve_app.py" not in commands, f"native app started a legacy UI server: {commands}"
                if case == "crash":
                    os.kill(process.pid, signal.SIGKILL)
                status = process.wait(timeout=45)
                assert status == (0 if case == "close" else -signal.SIGKILL), status
                deadline = time.monotonic() + 8
                while any(alive(pid) for pid in children) and time.monotonic() < deadline:
                    time.sleep(0.1)
                assert not any(alive(pid) for pid in children), f"orphaned services after {case}: {children}"
                print(f"PASS {case}: app and {len(children)} owned backend service stopped")
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
                for pid in children:
                    if alive(pid):
                        os.kill(pid, signal.SIGKILL)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", type=Path, default=WORKSPACE / "native-build/Refract Native.app")
    args = parser.parse_args()
    binary = args.app.resolve() / "Contents/MacOS/RefractImage"
    assert binary.is_file(), f"Build the native app first: {binary}"
    run_case(binary, "close")
    run_case(binary, "crash")


if __name__ == "__main__":
    main()
