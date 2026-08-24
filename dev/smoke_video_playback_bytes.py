#!/usr/bin/env python3
"""Real-bytes video playback harness — does cursor advance actually
change the bytes that ``build_frame`` produces?

Constructs a fake 3-frame playback where frame 0 = solid red, frame 1
= solid green, frame 2 = solid blue.  Without ever decoding a real
mp4 (so the test runs anywhere), it exercises the full ``build_frame``
pipeline at each cursor position and asserts that the encoded bytes
match the cursor's expected colour.

If the bg-mask cache key is broken (the symptom we just fixed), this
harness reports frame 0's red bytes for cursor=1 and cursor=2, even
though the playback says we're on a different frame.

Exit 0 if cursor 0/1/2 produce red/green/blue respectively; exit 1
with the divergence otherwise.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


CANVAS_W = 8
CANVAS_H = 8


def _ensure_qt_app() -> None:
    from PySide6.QtCore import QCoreApplication
    from PySide6.QtGui import QGuiApplication

    if QCoreApplication.instance() is None:
        _ = QGuiApplication.instance() or QGuiApplication(sys.argv)


def _make_raw_frame(rgb: tuple[int, int, int]) -> bytes:
    """An ENCODED solid-colour frame at CANVAS_W × CANVAS_H.

    A ``Playback`` holds encoded bytes and decodes one per tick, so this
    harness has to hand it what the decoders now produce.  It used to build a
    ``RawFrame`` and stopped matching when playback frames stopped being raw
    (0c7e7119) — the suite was updated and this was not, so it broke silently
    until the next person ran it.  JPEG at quality 100 keeps the solid colour
    exact, which is what the pixel comparison below depends on.
    """
    from PySide6.QtCore import QBuffer, QByteArray
    from PySide6.QtGui import QImage

    r, g, b = rgb
    img = QImage(CANVAS_W, CANVAS_H, QImage.Format.Format_RGB888)
    img.fill((0xFF << 24) | (r << 16) | (g << 8) | b)
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QBuffer.OpenModeFlag.WriteOnly)
    img.save(buf, "JPEG", 100)
    buf.close()
    return bytes(ba)


def _decode_first_pixel(buf: bytes) -> tuple[int, int, int]:
    """RGB565 big-endian → (r, g, b) for the first pixel of the buffer."""
    b0, b1 = buf[0], buf[1]
    packed = (b0 << 8) | b1
    r5 = (packed >> 11) & 0x1F
    g6 = (packed >> 5) & 0x3F
    b5 = packed & 0x1F
    return (
        (r5 << 3) | (r5 >> 2),
        (g6 << 2) | (g6 >> 4),
        (b5 << 3) | (b5 >> 2),
    )


def _within(actual: tuple[int, int, int],
            expected: tuple[int, int, int],
            tol: int = 8) -> bool:
    return all(abs(a - e) <= tol for a, e in zip(actual, expected, strict=True))


def main() -> int:
    _ensure_qt_app()

    from trcc.adapters.render.qt import QtRenderer
    from trcc.core.models import Kind, ProductInfo, Theme, Wire
    from trcc.services.display import DisplayService
    from trcc.services.media import MediaService, Playback
    from trcc.services.overlay import OverlayService
    from trcc.services.settings import Settings
    from trcc.adapters.theme.filesystem import FileContentStore

    # Minimal Paths stub so Settings can boot.
    class _Paths:
        def __init__(self, base: Path) -> None:
            self._b = base

        def app_data_dir(self) -> Path:        return self._b
        def config_dir(self) -> Path:          return self._b
        def log_dir(self) -> Path:             return self._b
        def cache_dir(self) -> Path:           return self._b
        def user_content_dir(self) -> Path:    return self._b
        def cloud_theme_dir(self, w: int, h: int) -> Path:  return self._b
        def cloud_mask_dir(self, w: int, h: int) -> Path:   return self._b
        def theme_dir(self, w: int, h: int) -> Path:        return self._b
        def user_theme_dir(self, w: int, h: int) -> Path:   return self._b
        def data_dir(self) -> Path:            return self._b

    with tempfile.TemporaryDirectory() as td:
        base = Path(td)
        renderer = QtRenderer()
        media = MediaService()
        themes = FileContentStore()
        overlay = OverlayService(renderer)
        settings = Settings(_Paths(base))

        display = DisplayService(
            renderer=renderer,
            themes=themes,
            overlay=overlay,
            settings=settings,
            media=media,
            paths=_Paths(base),
        )

        # Fabricate a theme dir containing a PNG background (image-theme
        # — the exact pre-condition that triggered the bg-cache bug).
        theme_dir = base / "Theme_Test"
        theme_dir.mkdir()
        # 00.png = solid yellow background (anything not red/green/blue)
        from PySide6.QtGui import QColor, QImage
        bg = QImage(CANVAS_W, CANVAS_H, QImage.Format.Format_ARGB32)
        bg.fill(QColor(255, 255, 0, 255))
        bg.save(str(theme_dir / "00.png"), "PNG")
        theme = Theme(
            path=theme_dir, name="Theme_Test",
            resolution=(CANVAS_W, CANVAS_H),
            config={"elements": [], "overlay_enabled": False},
        )

        info = ProductInfo(
            vid=0x0402, pid=0x3922,
            vendor="smoke", product="smoke",
            wire=Wire.SCSI, kind=Kind.LCD,
            device_type=1, fbl=100,
            native_resolution=(CANVAS_W, CANVAS_H),
            orientations=(0, 90, 180, 270),
        )

        # Cloud-override mp4 path (file doesn't have to exist — we
        # populate the playback manually below).
        settings.set_background_path(info.key, str(base / "fake_cloud.mp4"))
        # Brightness 100 = no-op; overlay disabled.

        # Build the 3-frame fake playback: red / green / blue.
        expected = [
            (255, 0,   0),
            (0,   255, 0),
            (0,   0,   255),
        ]
        playback = Playback(
            frames=[_make_raw_frame(c) for c in expected],
            fps=15,
            loop=True,
        )
        media._playbacks[info.key] = playback

        results: list[tuple[int, tuple[int, int, int],
                            tuple[int, int, int], bool]] = []
        for cursor in range(3):
            playback.cursor = cursor
            frame_bytes = display.build_frame(info=info, theme=theme,
                                              sensors={}, profile=None)
            actual = _decode_first_pixel(frame_bytes)
            results.append((cursor, expected[cursor], actual,
                            _within(actual, expected[cursor])))

        print("\n=== Cursor advancement → bytes-out ===")
        print(f"{'cursor':<7} {'expected':<16} {'actual':<16} {'ok':<3}")
        print("-" * 45)
        for cursor, exp, act, ok in results:
            print(f"{cursor:<7} {exp!s:<16} {act!s:<16} "
                  f"{'YES' if ok else 'NO'}")

        failures = [r for r in results if not r[3]]
        if failures:
            print(f"\n{len(failures)}/{len(results)} cursors produced "
                  "the wrong frame.")
            print("\nThis means bg_mask cache is hitting across cursor "
                  "positions — playback won't appear to advance on the "
                  "device.")
            return 1
        print(f"\nAll {len(results)} cursor positions produced the right "
              "frame.  Cache invalidation across cursor advancement works.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
