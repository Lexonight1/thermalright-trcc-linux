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
4.  Fall back to a full-screen grab + crop — slower but always works.

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
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QApplication

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
            raise OSError(
                f"Invalid region size {width}x{height} — both must be > 0",
            )

        pix = self._qt_grab(x, y, width, height)
        if pix is None or pix.isNull() or pix.width() <= 1:
            pix = self._external_grab(x, y, width, height)
        if pix is None or pix.isNull():
            raise OSError(
                "Screen capture failed — Qt returned a blank pixmap and "
                "no fallback tool produced output.  On Wayland install "
                "'grim'; on X11 install 'scrot'.",
            )
        return _pixmap_to_raw_frame(pix, width, height)

    # ── Implementations ──────────────────────────────────────────────

    def _qt_grab(
        self, x: int, y: int, w: int, h: int,
    ) -> QPixmap | None:
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
        # Sequence of tool, argument-list-builder pairs.  First one
        # whose binary exists + exits 0 wins.
        attempts: tuple[tuple[str, list[str]], ...] = (
            ("grim", ["grim", "-g", f"{x},{y} {w}x{h}", "{out}"]),
            ("scrot", ["scrot", "-a", f"{x},{y},{w},{h}", "{out}"]),
        )

        fd, tmp_path = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        try:
            for tool, template in attempts:
                if shutil.which(tool) is None:
                    continue
                cmd = [s.replace("{out}", tmp_path) for s in template]
                try:
                    result = subprocess.run(
                        cmd, capture_output=True,
                        timeout=_EXTERNAL_TIMEOUT_S, check=False,
                    )
                except subprocess.TimeoutExpired:
                    log.warning("%s timed out on region capture", tool)
                    continue
                if result.returncode != 0:
                    continue
                pix = QPixmap(tmp_path)
                if not pix.isNull():
                    return pix

            # Last-ditch: full-screen grab + crop.
            screen = QApplication.primaryScreen()
            if screen is not None:
                full = screen.grabWindow(0)  # type: ignore[arg-type]
                if not full.isNull() and full.width() > 1:
                    return full.copy(QRect(x, y, w, h))
        finally:
            try:
                Path(tmp_path).unlink()
            except OSError:
                pass

        return None


def _pixmap_to_raw_frame(
    pix: QPixmap, target_w: int, target_h: int,
) -> RawFrame:
    """Convert a :class:`QPixmap` to RGB24-packed :class:`RawFrame`.

    Resize to exactly ``target_w × target_h`` so callers can rely on
    the dimensions — external tools sometimes round geometry to even
    pixels.
    """
    image = pix.toImage().convertToFormat(QImage.Format.Format_RGB888)
    if image.width() != target_w or image.height() != target_h:
        image = image.scaled(target_w, target_h)
    # QImage's bits() returns memoryview-like; copy into immutable bytes
    # for hand-off across thread/UI boundaries.
    data = bytes(image.constBits())
    # Qt's RGB888 buffer is row-padded to a multiple of 4; strip the
    # pad if it's there so consumers see exactly width*height*3 bytes.
    expected = target_w * target_h * 3
    if len(data) != expected:
        stride = image.bytesPerLine()
        rows = bytearray()
        for row in range(target_h):
            offset = row * stride
            rows.extend(data[offset:offset + target_w * 3])
        data = bytes(rows)
    return RawFrame(data=data, width=target_w, height=target_h)
