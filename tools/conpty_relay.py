#!/usr/bin/env python3
"""Launch a Windows program under a ConPTY and stream its console output to a file.

Why: when a console-subsystem program's stdout is redirected straight to a file,
the C runtime switches to full buffering (~4 KB). Small log messages then sit in
the buffer and can be lost if the program freezes. Under a pseudo console the
child believes it writes to a real console (line buffered), and this relay
forwards every chunk to the log immediately.

Requires: pywinpty (dev dependency: `uv sync --group dev`).

Usage:
    python tools/conpty_relay.py --log coppelia_console.log \
        --cwd "C:/Program Files/CoppeliaRobotics/CoppeliaSimEdu" \
        -- "coppeliaSim.exe" -vscriptinfos -GzmqRemoteApi.rpcPort=23000
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
import time
import traceback

try:
    from winpty import Backend, PtyProcess
except ImportError:  # pragma: no cover - gives an actionable message
    sys.stderr.write(
        "缺少 pywinpty。安装: uv sync --group dev  (或 pip install pywinpty)\n")
    raise

# CSI / OSC / other two-byte escape sequences emitted by the console host.
ANSI_RE = re.compile(
    r"\x1b(?:\[[0-?]*[ -/]*[@-~]"
    r"|\][^\x07\x1b]*(?:\x07|\x1b\\)?"
    r"|[@-Z\\-_])"
)


def _clean(text: str) -> str:
    text = ANSI_RE.sub("", text)
    # The pty delivers CRLF; normalize and turn stray CRs into line breaks.
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _child_env() -> tuple[dict[str, str], list[str]]:
    """Inherit the parent env minus uv's PYTHONHOME overrides.

    uv-managed venvs export PYTHONHOME / UV_INTERNAL__PYTHONHOME pointing at the
    uv Python. If CoppeliaSim inherits them, its own Python sandbox (e.g. an
    Anaconda 3.12 interpreter) is forced to load the uv 3.11 stdlib and dies
    with "SRE module mismatch". Strip them for the child.
    """
    env = dict(os.environ)
    dropped = []
    for key in ("PYTHONHOME", "UV_INTERNAL__PYTHONHOME"):
        if key in env:
            env.pop(key)
            dropped.append(key)
    return env, dropped


def run(app: str, argv: list[str], cwd: str | None, log_path: str,
        cols: int, rows: int) -> int:
    resolved = shutil.which(app)
    if resolved is None:
        raise FileNotFoundError(app)
    app = os.path.abspath(resolved)
    child_env, dropped = _child_env()
    proc = PtyProcess.spawn(
        [app] + argv,
        cwd=cwd or None,
        env=child_env,
        dimensions=(rows, cols),
        backend=Backend.ConPTY,
    )
    with open(log_path, "w", encoding="utf-8", buffering=1) as log:
        t0 = time.time()
        log.write(f"### conpty-relay: spawned pid={proc.pid} "
                  f"size={cols}x{rows} backend=ConPTY "
                  f"env_dropped={dropped or 'none'}\n")
        log.flush()
        try:
            while True:
                try:
                    data = proc.read(65536)
                except EOFError:
                    log.write(f"\n### conpty-relay: EOF "
                              f"t={time.time() - t0:.2f}s\n")
                    break
                if not data:
                    log.write(f"\n### conpty-relay: empty read "
                              f"t={time.time() - t0:.2f}s\n")
                    break
                text = _clean(data)
                if text:
                    log.write(text)
                    log.flush()
        except Exception:
            log.write("\n### conpty-relay: error\n")
            log.write(traceback.format_exc())
            log.flush()
        finally:
            try:
                proc.wait()
            except Exception:
                pass
            log.write(f"### conpty-relay: child pid={proc.pid} exited, "
                      f"status={proc.exitstatus}\n")
            log.flush()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", default="coppelia_console.log")
    ap.add_argument("--cwd", default=None)
    ap.add_argument("--cols", type=int, default=1000)
    ap.add_argument("--rows", type=int, default=200)
    ap.add_argument("command", nargs=argparse.REMAINDER,
                    help="program and arguments (prefix with -- if needed)")
    ns = ap.parse_args()
    cmd = ns.command
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        ap.error("missing program to launch")

    app, argv = cmd[0], cmd[1:]
    try:
        return run(app, argv, ns.cwd, os.path.abspath(ns.log),
                   ns.cols, ns.rows)
    except Exception:
        err_path = ns.log + ".relay.err"
        with open(err_path, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + traceback.format_exc())
        return 1


if __name__ == "__main__":
    sys.exit(main())
