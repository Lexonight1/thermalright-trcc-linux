#!/usr/bin/env python3
"""Real-bytes color-path harness — ONE pipeline, in to out.

Exercises the REAL render→encode pipeline end-to-end (same single
solid path every frame travels in production).  Known input theme →
``DisplayService.build_frame`` → output bytes → decode → assert
pixels match expected.

Runnable against cutover (default) and legacy (``TRCC_LEGACY=1``)
side-by-side so divergences surface as raw byte differences.

Exit 0 if all sampled pixels match within tolerance; exit 1 with the
divergence table otherwise.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


# Sample positions inside a 64x64 test theme.  Each (x, y, expected_rgb)
# tuple says "at this pixel after the full pipeline, expect this colour".
SAMPLES: list[tuple[int, int, tuple[int, int, int]]] = [
    (8,  8,  (255, 0,   0)),    # top-left quadrant: red
    (56, 8,  (0,   255, 0)),    # top-right quadrant: green
    (8,  56, (0,   0,   255)),  # bottom-left quadrant: blue
    (56, 56, (255, 255, 255)),  # bottom-right quadrant: white
    (32, 32, (128, 128, 128)),  # center: mid-gray
]

CANVAS_W, CANVAS_H = 64, 64


def decode_rgb565(packed: int) -> tuple[int, int, int]:
    r5 = (packed >> 11) & 0x1F
    g6 = (packed >> 5) & 0x3F
    b5 = packed & 0x1F
    return (
        (r5 << 3) | (r5 >> 2),
        (g6 << 2) | (g6 >> 4),
        (b5 << 3) | (b5 >> 2),
    )


def decode_pixel(buf: bytes, x: int, y: int, width: int,
                 byte_order: str) -> tuple[int, int, int]:
    """Decode the pixel at (x, y) from a flat RGB565 buffer."""
    idx = (y * width + x) * 2
    b0, b1 = buf[idx], buf[idx + 1]
    packed = (b0 << 8) | b1 if byte_order == ">" else (b1 << 8) | b0
    return decode_rgb565(packed)


def within(actual: tuple[int, int, int],
           expected: tuple[int, int, int],
           tol: int = 8) -> bool:
    return all(abs(a - e) <= tol for a, e in zip(actual, expected, strict=True))


def _build_test_theme_png(tmp: Path) -> Path:
    """Write a 64x64 PNG with 4 colored quadrants + center mid-gray."""
    from PySide6.QtGui import QColor, QImage, QPainter

    img = QImage(CANVAS_W, CANVAS_H, QImage.Format.Format_ARGB32)
    img.fill(QColor(128, 128, 128, 255))   # center = mid-gray base

    painter = QPainter(img)
    quad = CANVAS_W // 2
    painter.fillRect(0, 0, quad, quad, QColor(255, 0, 0, 255))      # TL red
    painter.fillRect(quad, 0, quad, quad, QColor(0, 255, 0, 255))   # TR green
    painter.fillRect(0, quad, quad, quad, QColor(0, 0, 255, 255))   # BL blue
    painter.fillRect(quad, quad, quad, quad, QColor(255, 255, 255, 255))  # BR white

    # Restore center mid-gray dot — overwrite the 4-quadrant grid with the
    # known center sample we test for.
    painter.fillRect(quad - 4, quad - 4, 8, 8, QColor(128, 128, 128, 255))
    painter.end()

    path = tmp / "test_theme.png"
    img.save(str(path), "PNG")
    return path


def _ensure_qt_app() -> None:
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtGui import QGuiApplication
    if QCoreApplication.instance() is None:
        _ = QGuiApplication.instance() or QGuiApplication(sys.argv)


def _run_full_pipeline(renderer_module: str, tmp: Path) -> bytes:
    """Run the entire render path (open → composite → encode) against the
    test theme.  Returns the final RGB565 bytes the wire would carry.

    Pipeline replicates what ``DisplayService.build_frame`` does for a
    static-image theme with no overlay / no mask: load image, resize to
    canvas, apply brightness=100 (no-op), encode RGB565 big-endian
    (matches SCSI 320x320 profile defaults).
    """
    if renderer_module == "cutover":
        from trcc.adapters.render.qt import QtRenderer
    elif renderer_module == "legacy":
        from trcc.legacy.adapters.render.qt import QtRenderer
    else:
        raise ValueError(f"unknown renderer_module {renderer_module!r}")

    renderer = QtRenderer()
    theme_png = _build_test_theme_png(tmp)

    # The single solid path every frame travels:
    img = renderer.open_image(theme_png)
    # Resize step (no-op here since PNG is already canvas-sized) — call
    # anyway because production always does.
    img = renderer.resize(img, CANVAS_W, CANVAS_H)
    # Compose against a black canvas exactly like build_frame's bg layer.
    base = renderer.create_surface(CANVAS_W, CANVAS_H, color=(0, 0, 0, 255))
    composed = renderer.composite(base, img, (0, 0))
    # Brightness 100 is a no-op but still part of the production path.
    dimmed = renderer.apply_brightness(composed, 100)
    # Encode big-endian (SCSI default).
    return renderer.encode_rgb565(dimmed, byte_order=">")


def _check(bytes_out: bytes) -> list[tuple[str, tuple[int, int, int],
                                           tuple[int, int, int], bool]]:
    rows: list[tuple[str, tuple[int, int, int],
                     tuple[int, int, int], bool]] = []
    for x, y, expected in SAMPLES:
        actual = decode_pixel(bytes_out, x, y, CANVAS_W, ">")
        label = f"({x},{y})"
        rows.append((label, expected, actual, within(actual, expected)))
    return rows


def print_table(label: str,
                rows: list[tuple[str, tuple[int, int, int],
                                 tuple[int, int, int], bool]]) -> None:
    print(f"\n=== {label} ===")
    print(f"{'pixel':<10} {'expected':<16} {'actual':<16} {'ok':<3}")
    print("-" * 48)
    for name, expected, actual, ok in rows:
        print(f"{name:<10} {expected!s:<16} {actual!s:<16} "
              f"{'YES' if ok else 'NO'}")


def main() -> int:
    target = os.environ.get("TRCC_LEGACY", "").strip()
    label = "LEGACY" if target == "1" else "CUTOVER"
    module = "legacy" if target == "1" else "cutover"

    _ensure_qt_app()
    with tempfile.TemporaryDirectory() as td:
        out = _run_full_pipeline(module, Path(td))
    rows = _check(out)
    print_table(f"{label} full pipeline (open→resize→composite→brightness→encode)", rows)

    failures = [r for r in rows if not r[3]]
    if failures:
        print(f"\n{len(failures)}/{len(rows)} sampled pixels FAILED.")
        return 1
    print(f"\nAll {len(rows)} sampled pixels match within tolerance.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
