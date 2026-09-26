"""Shared CLI context — App singleton + lightweight helpers.

In daemon mode (``TRCC_DAEMON=1``) the App is actually an
``AppProxy`` — same ``dispatch(cmd) -> Result`` surface, calls travel
over the Unix socket to the running daemon.  Resolved via the canonical
``_boot.trcc()`` factory.
"""
from __future__ import annotations

import dataclasses
import json
import logging
from functools import lru_cache
from typing import Any

import typer

from ..._boot import trcc
from ...app import App
from ...core.commands import DeviceState
from ...core.ports import Platform, Renderer

log = logging.getLogger(__name__)


def dumps_json(payload: Any) -> str:
    """JSON for CLI ``--json`` output — indented, ``default=str`` so enums /
    Paths / tuples serialise without a custom encoder.  One shape for every
    ``--json`` flag (per-result via :func:`emit_json`, composite via the
    top-level ``status``)."""
    log.debug("dumps_json: payload=%s", payload)
    return json.dumps(payload, default=str, indent=2)


def emit_json(result: Any) -> None:
    """Print a dataclass Result/Snapshot as indented JSON for scripts."""
    log.debug("emit_json: result=%s", result)
    typer.echo(dumps_json(dataclasses.asdict(result)))


def ensure_connected(app: App, key: str) -> None:
    """Attach + handshake *key* if this stateless CLI process hasn't yet.

    Every CLI invocation is a fresh, non-daemon App holding no attached
    devices, so a wire command (``color`` / ``play`` / ``load-theme`` / LED
    ``render`` …) dispatched straight away would fail with "not connected".
    ``EnsureConnected`` is idempotent — a no-op when a daemon/GUI already holds
    the device — so this is safe before a single wire command and once before a
    render/play loop.  Exits with the connect error on failure (a wire command
    against an unattached device can do nothing useful).
    """
    log.debug("ensure_connected: app=%s key=%s", app, key)
    from ...core.commands import EnsureConnected
    result = app.dispatch(EnsureConnected(key=key))
    if not result.ok:
        typer.echo(result.message, err=True)
        raise typer.Exit(code=1)


def resolution_for(key: str) -> tuple[int, int]:
    """The device's panel resolution, or exit 1 telling the user to connect.

    Collapses a block that ``cli/theme.py`` and ``cli/display.py`` carried
    BYTE-IDENTICALLY for 11 of its 12 lines, error string included — they
    differed only in the Command built on the last line.  Both reached
    ``app.devices.get(key)`` and read ``.profile.resolution`` off a live
    ``Device``, which CLAUDE.md forbids a UI holding and which raises under
    ``TRCC_DAEMON=1``.

    ``DeviceState`` answers it: an unattached key is ``ok=False``, and an
    attached-but-un-handshaken one reports ``resolution=None`` — precisely the
    two cases the old ``device is None or device.profile is None`` guard
    covered, and the reason this returns rather than raising on its own.
    """
    result = get_app().dispatch(DeviceState(key=key))
    log.info("resolution_for: key=%s ok=%s resolution=%s",
             key, result.ok, result.resolution)
    if not result.ok or result.resolution is None:
        typer.echo(
            f"Device {key} not connected — connect first so we know "
            "the target resolution",
            err=True,
        )
        raise typer.Exit(code=1)
    return result.resolution


def dispatch_echo(cmd: Any) -> Any:
    """Dispatch *cmd*, echo its ``message``, exit(1) on failure; return the Result.

    Collapses the `result = get_app().dispatch(...); typer.echo(result.message);
    if not result.ok: raise typer.Exit(1)` tail that every non-interactive CLI
    command repeats.  Commands that read fields off the Result keep the returned
    value; the rest just call it.
    """
    log.debug("dispatch_echo: cmd=%s", cmd)
    result = get_app().dispatch(cmd)
    typer.echo(result.message)
    if not result.ok:
        raise typer.Exit(code=1)
    return result


#: Seconds between reconnect attempts: quick at first (a replug or a resume
#: settles in a few seconds), then no more often than every 30 s.
_RECONNECT_BACKOFF_S = (2.0, 4.0, 8.0, 16.0, 30.0)


def recover_or_exit(app: Any, key: str, message: str) -> None:
    """A play-loop tick failed: bring a LOST panel back, or stop on a real error.

    ``trcc led play`` / ``display play`` exited 1 whenever a tick failed, so a
    panel that went away over suspend/resume ended the command -- and under
    systemd that was 1279 restarts in five hours (#270).  The daemon and the
    GUIs reconnect after a resume; these loops now do too.  A failure while the
    device is still connected (no theme, a bad argument) is a real error and
    still exits, so reconnecting cannot hide it.
    """
    state = app.dispatch(DeviceState(key=key))
    if state.ok and state.connected:
        log.warning("recover_or_exit: %s still connected — real failure: %s",
                    key, message)
        typer.echo(f"  tick failed: {message}", err=True)
        raise typer.Exit(code=1)
    log.warning("recover_or_exit: %s lost (%s) — reconnecting", key, message)
    typer.echo(f"  {key} stopped answering ({message}) — reconnecting…", err=True)
    reconnect_until_back(app, key)


def reconnect_until_back(app: Any, key: str) -> None:
    """Rebuild *key*'s connection, retrying with backoff until it answers.

    ``ResetDevice`` is the daemon's resume sequence as a Command (disconnect,
    reconnect, put the display back); a device the recovery already DROPPED is
    no longer attached, so ``ConnectDevice`` takes over.  Ctrl-C still stops.
    """
    import time

    from ...core.commands import ConnectDevice, ResetDevice

    attempt = 0
    while True:
        result = app.dispatch(ResetDevice(key=key))
        if not result.ok:
            result = app.dispatch(ConnectDevice(key=key))
        if result.ok:
            log.info("reconnect_until_back: %s back after %d attempt(s)",
                     key, attempt + 1)
            typer.echo(f"  {key} reconnected.", err=True)
            return
        wait = _RECONNECT_BACKOFF_S[min(attempt, len(_RECONNECT_BACKOFF_S) - 1)]
        log.info("reconnect_until_back: %s attempt %d failed (%s) — next in %.0fs",
                 key, attempt + 1, result.message, wait)
        attempt += 1
        time.sleep(wait)


def daemon_owns_the_panels(app: Any) -> bool:
    """True when ``app`` is the daemon's proxy — the daemon streams, not us.

    Under ``TRCC_DAEMON=1`` the daemon holds every panel and runs the session
    loops (metrics, LED, video), so a CLI command must not tick or warn as if
    the panel stops when it exits.
    """
    from ...proxy import AppProxy

    owned = isinstance(app, AppProxy)
    log.debug("daemon_owns_the_panels: %s", owned)
    return owned


def warn_blanking_panels() -> None:
    """At CLI exit, name every panel that goes blank now this process stops.

    Some firmware drops its image a moment after the last frame — a registry
    fact or a firmware quirk, ``DeviceState.needs_keepalive``.  A one-shot
    command such as ``trcc display color`` sends one frame and exits, so on
    those panels it flashed and went blank with no word why (#228, #267).
    Silent when the command never built an App, and in daemon mode, where the
    daemon owns the panel and keeps streaming after we exit.
    """
    from ...core.commands import ListDevices

    if get_app.cache_info().currsize == 0:
        log.debug("warn_blanking_panels: no App was built — nothing to warn")
        return
    app = get_app()
    if daemon_owns_the_panels(app):
        log.debug("warn_blanking_panels: daemon mode — the daemon keeps streaming")
        return
    for entry in app.dispatch(ListDevices()).devices:
        state = app.dispatch(DeviceState(key=entry.key))
        if state.connected and state.needs_keepalive:
            log.info("warn_blanking_panels: %s blanks when frames stop", entry.key)
            # Not ``display keepalive``: a new process has no copy of the frame
            # this one sent, so it holds the SAVED THEME instead.  A daemon keeps
            # the frame -- measured on Bulk, LY and HID: it went on resending
            # after the command exited (#267).
            typer.echo(
                f"Note: {entry.key} goes blank when frames stop, and this "
                "command has ended. To keep what you send on screen, run "
                "`export TRCC_DAEMON=1` first: trcc commands then hand the panel "
                "to a background daemon that keeps it showing. Or use the GUI.",
                err=True)


def parse_on_off(state: str) -> bool:
    """Parse an ``on``/``off`` CLI argument to bool, or raise ``BadParameter``."""
    log.debug("parse_on_off: state=%s", state)
    lowered = state.lower()
    if lowered not in ("on", "off"):
        raise typer.BadParameter(f"state must be 'on' or 'off', got {state!r}")
    return lowered == "on"


_platform_override: Platform | None = None
_renderer_override: Renderer | None = None


def set_platform(platform: Platform) -> None:
    """Override the autodetected Platform (tests, dev mock)."""
    log.debug("set_platform: platform=%s", platform)
    global _platform_override
    _platform_override = platform
    get_app.cache_clear()


def set_renderer(renderer: Renderer) -> None:
    """Override the default QtRenderer.  Mostly for tests."""
    log.debug("set_renderer: renderer=%s", renderer)
    global _renderer_override
    _renderer_override = renderer
    get_app.cache_clear()


@lru_cache(maxsize=1)
def get_app() -> App:
    """Lazy App singleton used by every CLI command handler.

    Returns an in-process ``App`` (default) or an ``AppProxy`` when
    ``TRCC_DAEMON=1`` is set — UIs don't distinguish, both expose
    ``dispatch(cmd) -> Result``.
    """
    log.debug("get_app")
    return trcc(platform=_platform_override, renderer=_renderer_override)
