"""Shared helpers for platform setup adapters.

Functions here are identical across 3-4 platform adapters. Import them
instead of repeating the implementation in each adapter.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

# ── Interactive prompt ────────────────────────────────────────────────────────

def _confirm(prompt: str, auto_yes: bool) -> bool:
    """Ask [Y/n] question. Returns True on yes/enter, False on n."""
    if auto_yes:
        print(f"  {prompt} [Y/n]: y (auto)")
        return True
    try:
        answer = input(f"  {prompt} [Y/n]: ").strip().lower()
        return answer in ('', 'y', 'yes')
    except (EOFError, KeyboardInterrupt):
        print()
        return False


def _print_summary(
    actions: list[str],
    launch_hint: str = "Run 'trcc gui' to launch.",
) -> None:
    print("  Summary")
    if actions:
        for a in actions:
            print(f"    + {a}")
    else:
        print("    Nothing to do — system is ready.")
    print(f"\n  {launch_hint}\n")


# ── Asset copy (non-Linux platforms avoid sandboxed pkg paths) ────────────────

def _copy_assets_to_user_dir(pkg_assets_dir: Path) -> Path:
    """Copy bundled assets to ~/.trcc/assets/gui/ on first run."""
    import logging
    log = logging.getLogger(__name__)
    user_assets = Path.home() / '.trcc' / 'assets' / 'gui'
    if user_assets.exists() and any(user_assets.glob('*.png')):
        return user_assets
    if pkg_assets_dir.exists():
        user_assets.mkdir(parents=True, exist_ok=True)
        try:
            for f in pkg_assets_dir.iterdir():
                shutil.copy2(f, user_assets / f.name)
            log.info("Copied %d assets to %s",
                     len(list(user_assets.glob('*'))), user_assets)
            return user_assets
        except Exception:
            log.warning("Failed to copy assets to user dir", exc_info=True)
    return pkg_assets_dir


# ── Process listing (psutil — Windows / macOS / BSD) ─────────────────────────



# ── Single-instance lock (POSIX — Linux / macOS / BSD) ───────────────────────

def _posix_acquire_instance_lock(config_dir: str) -> object | None:
    """Acquire an exclusive lock file via fcntl. Returns handle or None."""
    import fcntl
    lock_path = Path(config_dir) / "trcc-linux.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fh = open(lock_path, "w")
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fh.write(str(os.getpid()))
        fh.flush()
        return fh
    except OSError:
        return None


def _posix_raise_existing_instance(config_dir: str) -> None:
    """Send SIGUSR1 to the PID stored in the lock file."""
    import signal
    lock_path = Path(config_dir) / "trcc-linux.lock"
    try:
        pid = int(lock_path.read_text().strip())
        os.kill(pid, signal.SIGUSR1)
    except (FileNotFoundError, ValueError, ProcessLookupError, PermissionError):
        pass


# ── SIGUSR1-driven raise-window glue (POSIX — Linux / macOS / BSD) ───────────

def _posix_wire_ipc_raise(app: Any, window: Any) -> None:
    """Install a SIGUSR1 handler that raises *window* in the Qt event loop.

    Pure CPython signals can't safely call into Qt directly (they fire
    asynchronously from the event loop's thread of control), so we marshal
    the signal into a self-pipe (``socketpair``) that a ``QSocketNotifier``
    drains in the main thread.

    Identical implementation across Linux / macOS / BSD — extracted to
    a shared helper rather than triplicated in each platform adapter.
    Windows uses a different cross-process raise mechanism so it doesn't
    consume this helper.

    `app` and `window` are typed ``Any`` because PySide6 is imported
    lazily inside the function — keeping the module-level signature
    framework-free.
    """
    import signal
    import socket

    from PySide6.QtCore import QSocketNotifier

    rsock, wsock = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    rsock.setblocking(False)
    wsock.setblocking(False)

    def _on_sigusr1(signum: object, frame: object) -> None:
        try:
            wsock.send(b'\x01')
        except OSError:
            pass

    signal.signal(signal.SIGUSR1, _on_sigusr1)
    notifier = QSocketNotifier(rsock.fileno(), QSocketNotifier.Type.Read, app)

    def _raise_window() -> None:
        try:
            rsock.recv(1)
        except OSError:
            pass
        window.showNormal()
        window.raise_()
        window.activateWindow()

    notifier.activated.connect(_raise_window)
