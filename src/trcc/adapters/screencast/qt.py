"""Qt-backed :class:`ScreenCapture` adapter.

Uses :class:`QApplication.primaryScreen().grabWindow(0, x, y, w, h)`
on X11, where Qt can read the full desktop directly.  On Wayland the
native grab usually returns a black pixmap (the compositor refuses to
hand out other windows' contents), so we shell out to ``grim`` (the
canonical wlroots tool) or ``scrot`` (X11 last resort) and crop.

The order matters:

1.  Try Qt native — fastest path, no subprocess, no temp files.
2.  Try ``grim -g`` for the exact geometry — works on every wlroots
    compositor + sway + Hyprland.
3.  Try ``scrot -a`` — for X11 sessions where Qt's native grab was
    blocked by the security model.
4.  Try ``gnome-screenshot -f`` and crop — it has no scriptable region
    flag, and it is the only one of the three that works on GNOME and
    KDE Wayland, where ``grim`` is wlroots-only.
5.  Fall back to a Qt full-screen grab + crop.

**This is the one chain.**  It was written three times — here, in
``ui/gui/screen_capture.py`` and in ``ui/screen_overlay.py`` — and the copies
had already diverged: only the UI ones knew about ``gnome-screenshot``.  A
GNOME Wayland user could freeze the screen in the region picker and then get a
black screencast from the CLI, the API or qtgui, while the gui beside them
worked.  Every caller now comes through this port.

Every successful path returns a :class:`RawFrame` with packed RGB24
bytes, ready for :meth:`Renderer.from_raw_rgb24`.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from PySide6.QtCore import QRect
from PySide6.QtGui import QGuiApplication, QImage, QPixmap
from PySide6.QtWidgets import QApplication

from ...core._frames import unpad_rows
from ...core.models import RawFrame
from ...core.ports import ScreenCapture

log = logging.getLogger(__name__)


_EXTERNAL_TIMEOUT_S = 2


class QtScreenCapture(ScreenCapture):
    """Qt-native region grab with ``grim`` / ``scrot`` fallbacks."""

    def grab_region(
        self, x: int, y: int, width: int, height: int,
    ) -> RawFrame:
        if width <= 0 or height <= 0:
            log.error("QtScreenCapture: invalid region size %dx%d", width, height)
            raise OSError(
                f"Invalid region size {width}x{height} — both must be > 0",
            )

        log.debug("QtScreenCapture: grab region (%d,%d) %dx%d", x, y, width, height)
        pix = self._qt_grab(x, y, width, height)
        if pix is None or pix.isNull() or pix.width() <= 1:
            log.info(
                "QtScreenCapture: Qt native grab unusable (blank/Wayland) — "
                "falling back to external tool",
            )
            pix = self._external_grab(x, y, width, height)
        if pix is None or pix.isNull():
            log.error("QtScreenCapture: all capture paths failed for (%d,%d) %dx%d",
                      x, y, width, height)
            raise OSError(
                "Screen capture failed — Qt returned a blank pixmap and "
                "no fallback tool produced output.  On Wayland install "
                "'grim'; on X11 install 'scrot'.",
            )
        return _pixmap_to_raw_frame(pix, width, height)

    # ── Implementations ──────────────────────────────────────────────

    @staticmethod
    def _qt_can_grab() -> bool:
        """Whether Qt can see a real screen right now.

        An OFFSCREEN Qt has no window system to read, and ``grabWindow`` does
        not fail on it -- it returns a correctly-sized, non-null, essentially
        black pixmap.  Every "is this blank?" test in this file is a SIZE test
        (``isNull``, ``width() <= 1``), and that sails straight through them,
        so the black frame was accepted as a capture.  MEASURED against
        ImageMagick ground truth on the same rectangle: offscreen scores a
        mean absolute error of 68.9, the native xcb platform scores 0.0 --
        pixel-identical.

        Asked in ONE place because Qt is reached by TWO routes -- the region
        grab and the full-screen crop fallback -- and guarding only the first
        leaves the second serving the same blank pixmap.  That is exactly what
        happened when this guard was first written.

        Reachable from every non-GUI face: ``_ensure_qt_app`` forces
        ``QT_QPA_PLATFORM=offscreen`` for headless rendering without asking
        whether a display exists, and ``ui/qapp`` pops that variable back off
        for windowed launches -- the same collision seen from the other end.
        """
        # ``isinstance`` rather than ``is not None``: ``instance()`` is
        # inherited from ``QCoreApplication``, which has no ``platformName``,
        # and a console-only QCoreApplication genuinely cannot grab -- so the
        # narrowing the type checker wants is the check this needs anyway.
        app = QGuiApplication.instance()
        if not isinstance(app, QGuiApplication):
            log.debug("_qt_can_grab: no QGuiApplication (got %r)", type(app))
            return False
        if app.platformName() == "offscreen":
            log.debug("_qt_can_grab: platform is offscreen — Qt cannot see a "
                      "screen, leaving it to the external tools")
            return False
        return True

    def _qt_grab(
        self, x: int, y: int, w: int, h: int,
    ) -> QPixmap | None:
        log.debug("_qt_grab: x=%s y=%s", x, y)
        if not self._qt_can_grab():
            return None
        screen = QApplication.primaryScreen()
        if screen is None:
            return None
        # grabWindow with arguments captures a sub-region on X11; on
        # Wayland it tends to return a blank pixmap, which we detect
        # by width <= 1 in the caller.
        return screen.grabWindow(0, x, y, w, h)  # type: ignore[arg-type]

    def _external_grab(
        self, x: int, y: int, w: int, h: int,
    ) -> QPixmap | None:
        """Region tools first, then a full grab cropped to the region.

        Two stages because the tools split that way, not because the code
        wants to: ``grim`` and ``scrot`` take a geometry, while
        ``gnome-screenshot`` has no scriptable region flag (``-a`` is an
        interactive picker) and can only be cropped after the fact.

        ``gnome-screenshot`` is the branch that makes GNOME and KDE Wayland
        work at all.  It was present in ``ui/screen_overlay``'s copy of this
        chain and absent from this one, so the region picker could freeze the
        screen on those desktops and the screencast that followed got a black
        pixmap -- the gui recovered through its own third copy, and CLI, API
        and qtgui did not.  Same session, same desktop, different answer
        depending on which face the user opened.
        """
        fd, tmp_path = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        try:
            pix = self._run_tools((
                ("grim", ["grim", "-g", f"{x},{y} {w}x{h}", "{out}"]),
                ("scrot", ["scrot", "-a", f"{x},{y},{w},{h}", "{out}"]),
                ("maim", ["maim", "-g", f"{w}x{h}+{x}+{y}", "{out}"]),
                # ImageMagick.  Last of the region tools because it is the
                # least specialised, and first among those actually present on
                # a plain X11 desktop -- this box has no grim, no scrot and no
                # maim, and capture failed outright until ``import`` was here.
                ("import", ["import", "-window", "root", "-crop",
                            f"{w}x{h}+{x}+{y}", "+repage", "{out}"]),
            ), tmp_path)
            if pix is not None:
                return pix

            # Whole screen, then crop.  ``full`` stays a QPixmap so the crop
            # is one call whichever producer supplied it.
            full = self._run_tools((
                ("spectacle", ["spectacle", "-b", "-n", "-o", "{out}"]),
                ("gnome-screenshot", ["gnome-screenshot", "-f", "{out}"]),
            ), tmp_path)
            if full is None and self._qt_can_grab():
                log.info("QtScreenCapture: falling back to Qt full-screen grab")
                screen = QApplication.primaryScreen()
                if screen is not None:
                    shot = screen.grabWindow(0)  # type: ignore[arg-type]
                    if not shot.isNull() and shot.width() > 1:
                        full = shot
            if full is not None:
                log.info("QtScreenCapture: cropping %dx%d full grab to "
                         "(%d,%d) %dx%d", full.width(), full.height(), x, y, w, h)
                return full.copy(QRect(x, y, w, h))
        finally:
            try:
                Path(tmp_path).unlink(missing_ok=True)
            except OSError:
                pass

        return None

    def _run_tools(
        self, attempts: tuple[tuple[str, list[str]], ...], tmp_path: str,
    ) -> QPixmap | None:
        """First tool whose binary exists and exits 0 with usable output wins.

        One loop for both stages -- a second copy is how the region chain and
        the full chain drift apart, which is the defect this method exists to
        remove rather than repeat.
        """
        import time
        for tool, template in attempts:
            if shutil.which(tool) is None:
                log.debug("QtScreenCapture: %s not on PATH; skipping", tool)
                continue
            cmd = [s.replace("{out}", tmp_path) for s in template]
            log.debug("QtScreenCapture: trying %s", " ".join(cmd))
            try:
                result = subprocess.run(
                    cmd, capture_output=True,
                    timeout=_EXTERNAL_TIMEOUT_S, check=False,
                )
            except subprocess.TimeoutExpired:
                log.warning("QtScreenCapture: %s timed out", tool)
                continue
            if result.returncode != 0:
                log.warning("QtScreenCapture: %s exited %d (stderr=%r)",
                            tool, result.returncode,
                            result.stderr[:200].decode("utf-8", "replace"))
                continue
            for _ in range(20):
                if Path(tmp_path).exists() and Path(tmp_path).stat().st_size > 0:
                    break
                time.sleep(0.025)
            if not Path(tmp_path).exists() or Path(tmp_path).stat().st_size == 0:
                log.warning("QtScreenCapture: %s output file missing or empty", tool)
                continue
            pix = QPixmap(tmp_path)
            if not pix.isNull():
                log.info("QtScreenCapture: %s captured %dx%d",
                         tool, pix.width(), pix.height())
                return pix
            log.warning("QtScreenCapture: %s output was null QPixmap", tool)
        return None


def _pixmap_to_raw_frame(
    pix: QPixmap, target_w: int, target_h: int,
) -> RawFrame:
    """Convert a :class:`QPixmap` to RGB24-packed :class:`RawFrame`.

    Resize to exactly ``target_w × target_h`` so callers can rely on
    the dimensions — external tools sometimes round geometry to even
    pixels.
    """
    log.debug("_pixmap_to_raw_frame: pix=%s target_w=%s", pix, target_w)
    image = pix.toImage().convertToFormat(QImage.Format.Format_RGB888)
    if image.width() != target_w or image.height() != target_h:
        image = image.scaled(target_w, target_h)
    # ``constBits()`` is memoryview-like and row-padded to a multiple of 4;
    # copy to immutable bytes for the hand-off across thread/UI boundaries,
    # then strip the pad so consumers see exactly width*height*3.
    data = unpad_rows(bytes(image.constBits()), target_w, target_h,
                      image.bytesPerLine())
    return RawFrame(data=data, width=target_w, height=target_h)
