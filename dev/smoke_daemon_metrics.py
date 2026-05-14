"""Daemon-mode metrics smoke — verify ``TrccProxy.os.metrics`` works.

After the 2026-05-14 migration, CLI/API call sites read aggregate
metrics via ``_trcc().os.metrics``.  In daemon mode that goes through
``TrccProxy.os`` (an :class:`OSFacadeProxy`) which fetches over IPC via
``_meta.metrics`` and reconstructs a :class:`HardwareMetrics` on the
client.  This harness:

1. Boots a daemon in a subprocess (``trcc daemon``).
2. Builds a fresh ``TrccProxy`` pointed at the daemon's socket.
3. Reads ``proxy.os.metrics`` and asserts shape + populated fields.
4. Stops the daemon.

Run:
    PYTHONPATH=src python dev/smoke_daemon_metrics.py
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from trcc.core.models import HardwareMetrics  # noqa: E402
from trcc.core.trcc_proxy import TrccProxy  # noqa: E402
from trcc.ipc import _socket_path, daemon_running  # noqa: E402


@dataclass(slots=True)
class CheckResult:
    name: str
    passed: bool
    detail: str = ""

    def __str__(self) -> str:
        mark = "[ OK ]" if self.passed else "[FAIL]"
        suffix = f" — {self.detail}" if self.detail else ""
        return f"    {mark}  {self.name}{suffix}"


def _wait_for_socket(path: Path, deadline_s: float = 10.0) -> bool:
    """Block until the daemon socket accepts a connect or deadline expires."""
    end = time.monotonic() + deadline_s
    while time.monotonic() < end:
        if path.exists():
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                sock.settimeout(0.5)
                sock.connect(str(path))
                sock.close()
                return True
            except OSError:
                sock.close()
        time.sleep(0.1)
    return False


def check_daemon_starts(socket_path: Path) -> CheckResult:
    try:
        if _wait_for_socket(socket_path, deadline_s=10.0):
            return CheckResult("daemon socket up", True, str(socket_path))
        return CheckResult("daemon socket up", False,
                           f"timeout waiting for {socket_path}")
    except Exception as e:
        return CheckResult("daemon socket up", False,
                           f"{type(e).__name__}: {e}")


def check_proxy_metrics(socket_path: Path) -> CheckResult:
    """``proxy.os.metrics`` returns a populated HardwareMetrics over IPC."""
    try:
        proxy = TrccProxy(socket_path=socket_path, timeout=5.0)
        m = proxy.os.metrics
        if not isinstance(m, HardwareMetrics):
            return CheckResult("proxy.os.metrics type", False,
                               f"got {type(m).__name__}")
        if not m._populated:
            return CheckResult("proxy.os.metrics populated", False,
                               "_populated is empty")
        return CheckResult(
            "proxy.os.metrics", True,
            f"populated={len(m._populated)} readings={len(m.readings)} "
            f"cpu_temp={m.cpu_temp}")
    except Exception as e:
        return CheckResult("proxy.os.metrics", False,
                           f"{type(e).__name__}: {e}")


def check_proxy_metrics_roundtrip(socket_path: Path) -> CheckResult:
    """Two consecutive ``proxy.os.metrics`` calls each return fresh records."""
    try:
        proxy = TrccProxy(socket_path=socket_path, timeout=5.0)
        m1 = proxy.os.metrics
        m2 = proxy.os.metrics
        if not (m1._populated and m2._populated):
            return CheckResult("metrics two-call", False,
                               "one of the reads returned empty _populated")
        return CheckResult("metrics two-call", True,
                           f"both reads populated ({len(m1._populated)} fields)")
    except Exception as e:
        return CheckResult("metrics two-call", False,
                           f"{type(e).__name__}: {e}")


def main() -> int:
    print("\n  TRCC Daemon Metrics Smoke")
    print("  ──────────────────────────\n")
    sock = _socket_path()

    if daemon_running(socket_path=sock):
        print(f"  ! A daemon is already running on {sock} — exiting.")
        print("  ! Stop it (`trcc kill`) before running this smoke.")
        return 2

    # Boot the daemon as a child process; tear it down on exit.
    env = os.environ.copy()
    env["PYTHONPATH"] = str(_REPO_ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.Popen(
        [sys.executable, "-m", "trcc", "daemon"],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )

    try:
        results: list[CheckResult] = [
            check_daemon_starts(sock),
        ]
        if results[-1].passed:
            results.append(check_proxy_metrics(sock))
            results.append(check_proxy_metrics_roundtrip(sock))

        for r in results:
            print(r)
        failed = sum(1 for r in results if not r.passed)
    finally:
        # Shut the daemon down cleanly.
        try:
            from trcc.ipc import one_shot_request
            one_shot_request({"kill": True}, socket_path=sock, timeout=2.0)
        except Exception:
            pass
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)

    print()
    if failed:
        print(f"  {failed}/{len(results)} check(s) failed")
        return 1
    print(f"  All {len(results)} checks passed — daemon metrics chain holds")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
