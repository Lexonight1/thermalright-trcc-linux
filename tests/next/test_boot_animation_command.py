"""UploadBootAnimation Command — orchestration tests.

Verifies the Command's gating (SCSI-only, connected, frame count), its
hand-off to ``DisplayService.encode_boot_anim_frame`` for each frame,
and the resulting ``BootAnimationResult`` shape.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from trcc.next.app import App
from trcc.next.core.commands import ConnectDevice, UploadBootAnimation
from trcc.next.core.ports import Renderer

from .conftest import FakePlatform

# ── Minimal Renderer that returns deterministic RGB565 byte buffers ──


class _AnimRenderer(Renderer):
    """Renderer that returns surfaces sized to whatever the test asks for.

    Tracks ``open_image_calls`` so tests can assert the right files
    flowed into the renderer.  encode_rgb565 returns a unique payload
    per surface so we can prove distinct frames reached the device.
    """

    def __init__(self) -> None:
        self.open_image_calls: list[Path] = []
        self.next_dims: tuple[int, int] = (320, 320)

    class _Surface:
        def __init__(self, w: int, h: int, tag: bytes) -> None:
            self.w = w
            self.h = h
            self.tag = tag

    def create_surface(self, width: int, height: int,
                       color: tuple[int, ...] | None = None) -> Any:
        return _AnimRenderer._Surface(width, height, b"\x00")

    def open_image(self, path: Path) -> Any:
        self.open_image_calls.append(path)
        return _AnimRenderer._Surface(*self.next_dims, path.name.encode())

    def surface_size(self, surface: Any) -> tuple[int, int]:
        return (surface.w, surface.h)

    def composite(self, base: Any, overlay: Any,
                  position: tuple[int, int],
                  mask: Any | None = None) -> Any:
        return base

    def resize(self, surface: Any, width: int, height: int) -> Any:
        return _AnimRenderer._Surface(width, height, surface.tag)

    def rotate(self, surface: Any, degrees: int) -> Any:
        return surface

    def apply_brightness(self, surface: Any, percent: int) -> Any:
        return surface

    def draw_text(self, surface: Any, x: int, y: int, text: str,
                  color: str, size: int, bold: bool = False,
                  italic: bool = False) -> None:
        pass

    def encode_rgb565(self, surface: Any) -> bytes:
        # Deterministic per-surface payload so the test can identify each
        # frame on the wire.  RGB565 buffers are 2 bytes/pixel.
        return surface.tag * (surface.w * surface.h * 2 // max(1, len(surface.tag)))

    def encode_jpeg(self, surface: Any, quality: int = 95,
                    max_size: int = 0) -> bytes:
        return b""

    def from_raw_rgb24(self, frame: Any) -> Any:
        return _AnimRenderer._Surface(100, 100, b"\x00")


# ── Helpers ─────────────────────────────────────────────────────────


def _poll_response(fbl: int, *, size: int = 0xE100) -> bytes:
    buf = bytearray(size)
    buf[0] = fbl
    return bytes(buf)


def _make_app(tmp_path: Path, fbl: int = 100,
              monkeypatch: pytest.MonkeyPatch | None = None) -> tuple[App, FakePlatform]:
    """App with one connected SCSI device + a recording renderer."""
    if monkeypatch is not None:
        monkeypatch.setattr(
            "trcc.next.adapters.device.scsi_lcd._POST_INIT_DELAY_S", 0.0,
        )
        monkeypatch.setattr(
            "trcc.next.adapters.device.scsi_lcd._ANIM_FIRST_DELAY_S", 0.0,
        )
        monkeypatch.setattr(
            "trcc.next.adapters.device.scsi_lcd._ANIM_FRAME_DELAY_S", 0.0,
        )

    platform = FakePlatform(tmp_path)
    platform.scsi.read_script.append(_poll_response(fbl))
    app = App(platform=platform, renderer=_AnimRenderer())

    result = app.dispatch(ConnectDevice(key="0402:3922"))
    assert result.ok, f"ConnectDevice failed: {result.message}"
    return app, platform


def _make_frames(dir_path: Path, count: int) -> list[Path]:
    """Make *count* dummy PNG files (real bytes don't matter — renderer is fake)."""
    paths: list[Path] = []
    for i in range(count):
        p = dir_path / f"frame_{i:03d}.png"
        p.write_bytes(b"\x89PNG\r\n\x1a\n" + bytes([i]) * 32)
        paths.append(p)
    return paths


# ── Gating ───────────────────────────────────────────────────────────


def test_returns_error_for_unknown_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _ = _make_app(tmp_path, monkeypatch=monkeypatch)
    frames = _make_frames(tmp_path, 1)

    result = app.dispatch(UploadBootAnimation(
        key="dead:beef", frame_paths=frames, delays_ds=[10],
    ))

    assert result.ok is False
    assert "dead:beef" in result.message.lower() or "not attached" in result.message.lower()


def test_rejects_non_scsi_device(tmp_path: Path) -> None:
    """A HID device (0416:5302) must reject boot animation — SCSI-only feature.

    Type-gate hits *before* the connected-check, so we don't need a fake
    handshake — attach() alone is enough to trigger the bail-out.
    """
    platform = FakePlatform(tmp_path)
    app = App(platform=platform, renderer=_AnimRenderer())
    app.attach(0x0416, 0x5302)              # HID Type 2 — not SCSI

    frames = _make_frames(tmp_path, 1)
    result = app.dispatch(UploadBootAnimation(
        key="0416:5302", frame_paths=frames, delays_ds=[10],
    ))

    assert result.ok is False
    assert "not a SCSI LCD" in result.message


def test_rejects_empty_frame_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _ = _make_app(tmp_path, monkeypatch=monkeypatch)

    result = app.dispatch(UploadBootAnimation(
        key="0402:3922", frame_paths=[], delays_ds=[],
    ))

    assert result.ok is False
    assert "No frames" in result.message


# ── Happy path ───────────────────────────────────────────────────────


def test_uploads_all_frames_through_renderer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, platform = _make_app(tmp_path, monkeypatch=monkeypatch)
    renderer = app._renderer
    assert isinstance(renderer, _AnimRenderer)

    frames = _make_frames(tmp_path, 5)
    platform.scsi.sent.clear()      # ignore handshake init CDB

    result = app.dispatch(UploadBootAnimation(
        key="0402:3922", frame_paths=frames, delays_ds=[5, 5, 5, 5, 5],
    ))

    assert result.ok is True
    assert result.frames_uploaded == 5
    assert result.frames_total == 5
    assert "Uploaded 5 frames" in result.message

    # Each frame went through the renderer
    assert renderer.open_image_calls == frames

    # Wire saw 1 first frame + 5 carousel frames = 6 SCSI writes
    assert len(platform.scsi.sent) == 6


def test_partial_upload_returns_ok_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, platform = _make_app(tmp_path, monkeypatch=monkeypatch)

    # Fail after the 3rd CDB (1 first + 2 carousel = 3 successes, frame 2 fails)
    real_send = platform.scsi.send_cdb
    counter = {"n": 0}

    def flaky(cdb: bytes, data: bytes, timeout_ms: int = 5000) -> bool:
        counter["n"] += 1
        return False if counter["n"] >= 4 else real_send(cdb, data, timeout_ms)

    monkeypatch.setattr(platform.scsi, "send_cdb", flaky)

    frames = _make_frames(tmp_path, 3)
    result = app.dispatch(UploadBootAnimation(
        key="0402:3922", frame_paths=frames, delays_ds=[5, 5, 5],
    ))

    assert result.ok is False
    assert result.frames_uploaded == 2
    assert result.frames_total == 3
    assert "Partial upload" in result.message


def test_load_error_returns_clear_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the renderer fails to open a frame, the Command surfaces the filename."""
    app, _ = _make_app(tmp_path, monkeypatch=monkeypatch)
    bad_frame = tmp_path / "missing.png"   # doesn't exist

    def open_image_raises(path: Path) -> Any:
        raise OSError(f"cannot open {path}")

    monkeypatch.setattr(app._renderer, "open_image", open_image_raises)

    result = app.dispatch(UploadBootAnimation(
        key="0402:3922", frame_paths=[bad_frame], delays_ds=[10],
    ))

    assert result.ok is False
    assert "missing.png" in result.message
