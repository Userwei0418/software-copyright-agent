#!/usr/bin/env python3
"""Verify that a frozen Sidecar exits when its desktop parent disappears."""

from __future__ import annotations

import argparse
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
from pathlib import Path


def read_line(stream, result: queue.Queue) -> None:
    result.put(stream.readline())


def verify(sidecar: Path) -> dict:
    sidecar = sidecar.expanduser().resolve()
    if not sidecar.is_file():
        raise RuntimeError(f"Sidecar does not exist: {sidecar}")
    parent = subprocess.Popen([
        sys.executable, "-c", "import time; time.sleep(60)"
    ])
    child = None
    try:
        with tempfile.TemporaryDirectory(prefix="copyright-parent-exit-") as temporary:
            environment = os.environ.copy()
            environment["COPYRIGHT_AGENT_SESSION_TOKEN"] = "parent-exit-verify-" + "x" * 32
            environment["COPYRIGHT_AGENT_PARENT_PID"] = str(parent.pid)
            child = subprocess.Popen(
                [str(sidecar), "--data-dir", temporary],
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            assert child.stdout is not None
            output: queue.Queue = queue.Queue(maxsize=1)
            threading.Thread(target=read_line, args=(child.stdout, output), daemon=True).start()
            try:
                line = output.get(timeout=20)
            except queue.Empty as error:
                raise RuntimeError("Sidecar did not emit a startup handshake") from error
            if not line.strip():
                detail = child.stderr.read() if child.stderr else ""
                raise RuntimeError(f"Sidecar exited before startup: {detail[:300]}")
            handshake = json.loads(line)
            if handshake.get("event") != "sidecar.ready":
                raise RuntimeError("Sidecar emitted an invalid startup handshake")
            parent.terminate()
            parent.wait(timeout=5)
            try:
                exit_code = child.wait(timeout=8)
            except subprocess.TimeoutExpired as error:
                raise RuntimeError("Sidecar remained alive after its desktop parent exited") from error
            if exit_code != 0:
                detail = child.stderr.read() if child.stderr else ""
                raise RuntimeError(f"Sidecar parent monitor exited with {exit_code}: {detail[:300]}")
            return {"status": "ok", "sidecar_pid": handshake.get("pid"),
                    "parent_pid": parent.pid, "exit_code": exit_code}
    finally:
        if parent.poll() is None:
            parent.terminate()
            parent.wait(timeout=5)
        if child is not None and child.poll() is None:
            child.terminate()
            child.wait(timeout=5)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sidecar", type=Path, required=True)
    args = parser.parse_args(argv)
    print(json.dumps(verify(args.sidecar), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
