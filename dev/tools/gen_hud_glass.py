"""Draw hudgui's glass background -- ``src/trcc/ui/hudgui/assets/glass.png``.

His call (2026-11-05): "use a png for now" for the transparent-glass look, so
no compositor is needed (his XFCE runs without one).  Drawn here, in code, so
the picture can be changed and regenerated rather than being an opaque binary:
a deep graphite gradient, a soft diagonal sheen across the upper left, faint
horizontal scanlines and a vignette.

    PYTHONPATH=src python3.12 dev/tools/gen_hud_glass.py
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import (
    QColor,
    QGuiApplication,
    QImage,
    QLinearGradient,
    QPainter,
    QRadialGradient,
)

log = logging.getLogger(__name__)

OUT = Path(__file__).resolve().parents[2] / "src/trcc/ui/hudgui/assets/glass.png"
#: Drawn large and scaled down by the window, never up.
SIZE = (1600, 1000)


def draw(width: int, height: int) -> QImage:
    """The glass, *width* x *height*."""
    log.info("draw: %dx%d", width, height)
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    base = QLinearGradient(0, 0, 0, height)
    base.setColorAt(0.0, QColor("#232a33"))
    base.setColorAt(0.55, QColor("#141920"))
    base.setColorAt(1.0, QColor("#0a0d11"))
    painter.fillRect(image.rect(), base)

    sheen = QLinearGradient(0, 0, width * 0.55, height * 0.75)
    sheen.setColorAt(0.0, QColor(255, 255, 255, 34))
    sheen.setColorAt(0.45, QColor(255, 255, 255, 10))
    sheen.setColorAt(0.46, QColor(255, 255, 255, 0))
    painter.fillRect(image.rect(), sheen)

    painter.setPen(QColor(255, 255, 255, 7))
    for y in range(0, height, 3):
        painter.drawLine(0, y, width, y)

    vignette = QRadialGradient(QPointF(width / 2, height / 2), max(width, height) * 0.7)
    vignette.setColorAt(0.6, QColor(0, 0, 0, 0))
    vignette.setColorAt(1.0, QColor(0, 0, 0, 120))
    painter.fillRect(image.rect(), vignette)
    painter.end()
    return image


def main() -> int:
    """Write the glass; 0 on success."""
    logging.basicConfig(level=logging.INFO)
    QGuiApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    ok = draw(*SIZE).save(str(OUT))      # the format from the .png name
    log.info("main: wrote %s (%s)", OUT, "ok" if ok else "FAILED")
    del app
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
