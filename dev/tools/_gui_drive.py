"""Bring up the REAL shipping GUI and drive a known number of rendered frames.

Shared by ``frame_profile.py --gui`` and ``record_rate.py --gui``.  Both need
the same thing — the actual window, its observers, its preview repaints and its
Qt event loop, rendering a fixed count — and differ only in what they do at the
warm-up boundary: one starts a profiler, the other marks a byte offset in the
log.  That difference is the ``on_mark`` callback; everything else is here so
there is one GUI drive to keep correct instead of two that drift.

``glass_bench._run_gui_arm`` deliberately does NOT use this.  It runs under
``perf`` in a subprocess and carries ``_logging_kwargs`` compatibility for trees
as old as v9.9.2, so it is a different job that happens to look similar.

**Why it is driven and not awaited.**  Letting the GUI tick at its own rate
makes the run length depend on whatever background the user last saved: with a
video it composed ~12 fps, and with none it fell back to the 2 s metrics tick,
where 900 frames is half an hour.  A zero-interval timer dispatching
``RenderAndSend`` makes the frame count the independent variable while every
observer, preview update and repaint still runs.

**Why it cannot go through ``trcc()``.**  That builds a ``QtRenderer``, which
creates a ``QGuiApplication``, and ``run()`` then refuses to construct a
``QApplication`` over it.

**The panel updates far more slowly than this renders**, and holds its last
frame once the run exits.  Neither is a bug: ``DeviceSender`` is asynchronous
and supersedes whatever frame is still pending, so only a fraction reach USB —
measured 200 frames built, 4 raw writes.  The renders are all real.
"""
from __future__ import annotations

from collections.abc import Callable

#: Frames rendered before ``on_mark`` fires, so filled caches are not what gets
#: measured.  Shared by both arms of both tools so they exclude the same prefix.
WARMUP = 20


def drive_gui(*, frames: int, on_mark: Callable[[], None],
              on_done: Callable[[], None] | None = None,
              warmup: int = WARMUP) -> int:
    """Render ``warmup + frames`` frames in the real GUI; return frames rendered.

    ``on_mark`` fires once, after ``warmup`` frames and before the measured
    ones — the caller starts its profiler or records its log offset there.

    ``on_done`` fires once the frame target is reached and **before the window
    tears down**, and callers that measure anything need it.  ``run()`` does not
    return until the GUI has fully shut down, and shutdown is loud: closing the
    tray, stopping the metrics loop, the hotplug monitor, the LED loop, the
    sender, disconnecting the device.  A caller that stops measuring after
    ``run()`` returns therefore attributes all of that to the frame path —
    measured, it put 32 one-shot teardown records into a GUI record-rate that is
    otherwise 0.00, and it had ``frame_profile`` profiling the teardown too.

    The return value is the count actually rendered past the mark, which is the
    denominator a caller must divide by: measured, never assumed, because the
    event loop can process a tick or two after ``quit()``.
    """
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from trcc.adapters.system import current_platform
    from trcc.core.commands import RenderAndSend
    from trcc.services.display import DisplayService
    from trcc.ui.gui import run

    counter = {"frames": 0}
    real_build = DisplayService.build_frame

    def counting_build(self, *a, **kw):
        counter["frames"] += 1
        return real_build(self, *a, **kw)

    # Counting at ``build_frame`` rather than at the timer: the tick is what we
    # ASK for, a composed frame is what actually happened, and the two differ
    # whenever a dispatch bails early.
    DisplayService.build_frame = counting_build   # type: ignore[method-assign]
    state: dict = {}

    def on_ready(window) -> None:
        app = getattr(window, "_app", None)
        if app is None:
            raise SystemExit("no App on the window — unknown tree layout")
        keys = list(getattr(app, "devices", {}))
        if not keys:
            raise SystemExit("GUI came up with no attached device")
        key = keys[0]

        def pump() -> None:
            if counter["frames"] == warmup:
                on_mark()
            if counter["frames"] >= warmup + frames:
                timer.stop()
                if on_done is not None:
                    on_done()          # BEFORE quit(): teardown is not a frame
                qapp = QApplication.instance()
                if qapp is not None:
                    qapp.quit()
                return
            app.dispatch(RenderAndSend(key=key))

        timer = QTimer()
        timer.setInterval(0)
        timer.timeout.connect(pump)
        timer.start()
        state["timer"] = timer          # keep it alive past this scope

    try:
        run(current_platform(), single_instance=False, ipc=False,
            force_exit=False, on_ready=on_ready)
    finally:
        DisplayService.build_frame = real_build  # type: ignore[method-assign]
    return max(counter["frames"] - warmup, 0)
