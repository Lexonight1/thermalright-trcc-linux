#!/usr/bin/env python3
"""Diagnose the live metrics + preview path for one summoned mock device.

dev/mock_gui fakes ONLY the handshake; sensors/render/observe/preview are the
real app.  This summons ONE device deterministically (no flaky offscreen
auto-click — it does NOT mount the dev console), then lets the REAL event loop
run for a few seconds so the genuine chain fires end to end:

    MetricsLoop tick → SensorsUpdated → render observer → RenderAndSend/RenderLed
        → device send + (LCD) FrameSent / (LED) LedColorsChanged
        → GUI observes → handler.handle_frame → preview updates + M1-M6 gauges

then reads the ACTUAL widget state (gauge ``_text`` + preview ``_colors``) — what
the user sees — and prints it.  Answers "do metrics + preview populate for this
device" with no guessing.

    PYTHONPATH=src QT_QPA_PLATFORM=offscreen python3.12 dev/tools/diagnose_metrics.py
    PYTHONPATH=src QT_QPA_PLATFORM=offscreen python3.12 dev/tools/diagnose_metrics.py 0416:8001 3
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

_DEV = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_DEV))
from _mock_bootstrap import bootstrap

_SETTLE_MS = 5000  # let several real MetricsLoop ticks drive the full chain


def _say(msg: str) -> None:
    print(msg, flush=True)


def _summon(window, vid: int, pid: int, pm: int, sub: int) -> str:
    from trcc.core.commands import ConnectDevice
    from trcc.core.protocol import pm_to_fbl

    app = window._app
    key = f"{vid:04x}:{pid:04x}"
    fbl = pm_to_fbl(pm, sub)
    _say(f"\n— summon {key} pm={pm} sub={sub} fbl={fbl} —")
    app.platform.set_active_reply(vid, pid, pm=pm, sub=sub, fbl=fbl)
    window._remove_handler(key)
    r = app.dispatch(ConnectDevice(key=key))
    _say(f"connect ok={getattr(r, 'ok', None)}")
    device = app.devices.get(key)
    window._add_handler(device)
    window._active_key = ""
    window._activate_device(key)
    h = window._handlers.get(key)
    _say(f"handler={type(h).__name__} active={getattr(h, 'active', '?')}")
    _say(f"letting the real loop run {_SETTLE_MS} ms…")
    return key


def _inspect_and_exit(window, key: str) -> None:
    import os
    try:
        h = window._handlers.get(key)
        panel = getattr(h, "_panel", None)
        # M1-M6 gauges
        imgs = getattr(panel, "_info_images", {}) or {}
        if imgs:
            _say("M1-M6 gauges: " + ", ".join(
                f"{k}={getattr(w, '_text', '?')}" for k, w in imgs.items()))
        # LED preview segment colors (lit = non-black)
        preview = getattr(panel, "_preview", None)
        colors = getattr(preview, "_colors", None)
        if colors is not None:
            lit = sum(1 for c in colors if tuple(c) != (0, 0, 0))
            _say(f"PREVIEW _colors: {len(colors)} segments, {lit} lit "
                 f"({'POPULATED' if lit else 'all black — NOT populated'})")
        else:
            _say("PREVIEW: not an LED segment panel (LCD frame preview)")
    except Exception:
        traceback.print_exc()
    finally:
        sys.stdout.flush()
        os._exit(0)


def main() -> None:
    vid_pid = sys.argv[1] if len(sys.argv) > 1 else "0416:8001"
    pm = int(sys.argv[2], 0) if len(sys.argv) > 2 else 3
    sub = int(sys.argv[3], 0) if len(sys.argv) > 3 else 0
    vid, pid = (int(x, 16) for x in vid_pid.split(":"))

    platform = bootstrap(verbosity=0, all_devices=False,
                         specs=[{"vid": f"{vid:04x}", "pid": f"{pid:04x}", "pm": pm}])

    def on_ready(window) -> None:
        from PySide6.QtCore import QTimer

        from trcc.adapters.repo.http import HttpFetchError, UrllibHttpFetcher
        UrllibHttpFetcher.fetch = lambda self, url, *a, **k: (
            _ for _ in ()).throw(HttpFetchError("net off"))
        try:
            key = _summon(window, vid, pid, pm, sub)
        except Exception:
            traceback.print_exc()
            import os
            os._exit(1)
        # Let the REAL MetricsLoop → observer → RenderLed → preview chain run
        # (queued signals need the event loop spinning), THEN inspect.
        QTimer.singleShot(_SETTLE_MS, lambda: _inspect_and_exit(window, key))

    from trcc.ui.gui import run_gui
    run_gui(platform, single_instance=False, ipc=False, force_exit=False,
            start_hidden=True, on_ready=on_ready)


if __name__ == "__main__":
    main()
