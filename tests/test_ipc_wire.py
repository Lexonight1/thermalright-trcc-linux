"""IPC wire-format round-trip tests.

Pure-data checks: encode + decode are inverses for Commands and
Results across primitive fields, Path, Enum, tuple, list, and nested
frozen dataclasses.  No socket / threads — the transport layer
gets its own coverage in test_ipc_server.py.
"""
from __future__ import annotations

from pathlib import Path

from trcc.core.commands import (
    LoadTheme,
    SendColor,
    SetBrightness,
    SetFitMode,
    SetOrientation,
    UploadBootAnimation,
)
from trcc.core.models import (
    DeviceInfo,
    HandshakeResult,
    Kind,
    PanelCutout,
    ProductInfo,
    Wire,
)
from trcc.core.results import (
    ConnectResult,
    DiscoverResult,
    Result,
    SendResult,
)
from trcc.ipc import (
    COMMAND_TYPES,
    RESULT_TYPES,
    decode_command,
    decode_result,
    encode_command,
    encode_result,
)

# ── Registries are populated at import ──────────────────────────────


def test_command_registry_collects_every_command_subclass() -> None:
    """Every Command subclass declared in commands.py is reachable by name."""
    expected = {
        "DiscoverDevices", "ConnectDevice", "DisconnectDevice", "SendFrame",
        "SendColor", "RenderAndSend", "LoadTheme", "SaveTheme",
        "PlayVideo", "StopVideo",
        "SetOrientation", "SetBrightness", "SetFitMode", "EnableOverlay",
        "SetSplitMode", "ApplyMask", "SetMaskPosition", "SetMaskVisible",
        "SetLedColors", "RenderLed", "UploadBootAnimation",
    }
    missing = expected - set(COMMAND_TYPES)
    assert not missing, f"Missing from registry: {missing}"


def test_result_registry_collects_every_result_subclass() -> None:
    expected = {"DiscoverResult", "ConnectResult", "SendResult",
                "RenderResult", "ThemeResult", "OrientationResult",
                "BrightnessResult", "BootAnimationResult"}
    missing = expected - set(RESULT_TYPES)
    assert not missing, f"Missing from registry: {missing}"


# ── Command round-trip — primitives ────────────────────────────────


def test_send_color_roundtrip() -> None:
    cmd = SendColor(key="0402:3922", r=255, g=128, b=0)
    envelope = encode_command(cmd)

    assert envelope == {
        "command": "SendColor",
        "kwargs": {"key": "0402:3922", "r": 255, "g": 128, "b": 0},
    }

    rebuilt = decode_command(envelope)
    assert isinstance(rebuilt, SendColor)
    assert rebuilt == cmd


def test_set_brightness_roundtrip() -> None:
    cmd = SetBrightness(key="0402:3922", percent=42)
    rebuilt = decode_command(encode_command(cmd))
    assert isinstance(rebuilt, SetBrightness)
    assert rebuilt == cmd


def test_set_orientation_roundtrip() -> None:
    cmd = SetOrientation(key="0402:3922", degrees=90)
    rebuilt = decode_command(encode_command(cmd))
    assert isinstance(rebuilt, SetOrientation)
    assert rebuilt == cmd


def test_set_fit_mode_roundtrip_string_field() -> None:
    cmd = SetFitMode(key="0402:3922", mode="width")
    rebuilt = decode_command(encode_command(cmd))
    assert isinstance(rebuilt, SetFitMode)
    assert rebuilt == cmd


# ── Command round-trip — Path field ────────────────────────────────


def test_load_theme_serializes_path_as_string() -> None:
    cmd = LoadTheme(key="0402:3922", path=Path("/tmp/some/theme"))
    envelope = encode_command(cmd)

    # Path goes to the wire as a plain str (json-friendly)
    assert envelope["kwargs"]["path"] == "/tmp/some/theme"

    rebuilt = decode_command(envelope)
    assert isinstance(rebuilt, LoadTheme)
    assert rebuilt.path == Path("/tmp/some/theme")
    assert isinstance(rebuilt.path, Path)


# ── Command round-trip — list[Path] field ──────────────────────────


def test_upload_boot_animation_roundtrip_list_of_paths() -> None:
    frames = [Path("/tmp/f0.png"), Path("/tmp/f1.png"), Path("/tmp/f2.png")]
    cmd = UploadBootAnimation(
        key="0402:3922",
        frame_paths=frames,
        delays_ds=[10, 10, 10],
    )
    envelope = encode_command(cmd)

    # Paths serialize as strings on the wire
    assert envelope["kwargs"]["frame_paths"] == [
        "/tmp/f0.png", "/tmp/f1.png", "/tmp/f2.png",
    ]
    assert envelope["kwargs"]["delays_ds"] == [10, 10, 10]

    rebuilt = decode_command(envelope)
    assert isinstance(rebuilt, UploadBootAnimation)
    assert rebuilt.frame_paths == frames
    assert all(isinstance(p, Path) for p in rebuilt.frame_paths)
    assert rebuilt.delays_ds == [10, 10, 10]


# ── Result round-trip — flat ──────────────────────────────────────


def test_send_result_roundtrip() -> None:
    result = SendResult(ok=True, message="Sent 1024 bytes",
                        key="0402:3922", bytes_sent=1024)
    envelope = encode_result(result)
    assert envelope["type"] == "SendResult"
    assert envelope["ok"] is True
    assert envelope["bytes_sent"] == 1024

    rebuilt = decode_result(envelope)
    assert isinstance(rebuilt, SendResult)
    assert rebuilt == result


# ── Result round-trip — nested dataclass + tuple ──────────────────


def test_connect_result_roundtrip_with_handshake() -> None:
    """HandshakeResult survives nested-dataclass coercion (resolution tuple)."""
    handshake = HandshakeResult(
        resolution=(320, 320),
        model_id=100,
        pm_byte=100,
        sub_byte=0,
        fbl=100,
        raw_response=b"\x64" + b"\x00" * 63,
    )
    result = ConnectResult(
        ok=True, message="Connected: (320, 320)",
        key="0402:3922", handshake=handshake,
    )

    envelope = encode_result(result)
    rebuilt = decode_result(envelope)

    assert isinstance(rebuilt, ConnectResult)
    assert rebuilt.handshake is not None
    assert rebuilt.handshake.resolution == (320, 320)
    assert isinstance(rebuilt.handshake.resolution, tuple)
    assert rebuilt.handshake.fbl == 100
    assert rebuilt.handshake.raw_response == b"\x64" + b"\x00" * 63


# ── Discover result with list[ProductInfo] ─────────────────────────


def test_discover_result_with_empty_lists_roundtrips() -> None:
    result = DiscoverResult(ok=True, message="No devices", products=[], devices=[])
    rebuilt = decode_result(encode_result(result))
    assert isinstance(rebuilt, DiscoverResult)
    assert rebuilt.products == []
    assert rebuilt.devices == []


def test_discover_result_with_products_roundtrips() -> None:
    """Regression guard (859634af): a NON-EMPTY ProductInfo must survive the
    wire.  The empty-lists case above never builds a ProductInfo, so it missed
    the bug where ``decode_result`` → ``get_type_hints(ProductInfo)`` raised
    ``NameError`` on the TYPE_CHECKING-only ``PanelCutout`` annotation.  Any
    non-empty products list triggers it; the explicit PanelCutout also asserts
    the nested value survives."""
    product = ProductInfo(
        vid=0x0402, pid=0x3922, vendor="Acme", product="Test LCD",
        wire=Wire.SCSI, kind=Kind.LCD, native_resolution=(320, 320),
        panel_cutout=PanelCutout(x=10, y=20, w=30, h=40),
    )
    result = DiscoverResult(
        ok=True, message="1 device",
        products=[product], devices=[DeviceInfo(vid=0x0402, pid=0x3922)],
    )

    rebuilt = decode_result(encode_result(result))

    assert isinstance(rebuilt, DiscoverResult)
    assert len(rebuilt.products) == 1
    p = rebuilt.products[0]
    assert (p.vid, p.pid, p.product) == (0x0402, 0x3922, "Test LCD")
    assert p.wire is Wire.SCSI and p.kind is Kind.LCD
    assert p.native_resolution == (320, 320)
    assert p.panel_cutout == PanelCutout(x=10, y=20, w=30, h=40)
    assert len(rebuilt.devices) == 1
    assert (rebuilt.devices[0].vid, rebuilt.devices[0].pid) == (0x0402, 0x3922)


# ── Falls back to base Result for unknown types ────────────────────


def test_unknown_result_type_falls_back_to_base() -> None:
    rebuilt = decode_result({"type": "NotAResult", "ok": False,
                             "message": "garbage"})
    assert isinstance(rebuilt, Result)
    assert rebuilt.ok is False
    assert rebuilt.message == "garbage"


def test_unknown_command_decode_raises_clear_error() -> None:
    import pytest

    with pytest.raises(ValueError, match="Unknown command"):
        decode_command({"command": "NotACommand", "kwargs": {}})


def test_missing_command_key_decode_raises() -> None:
    import pytest

    with pytest.raises(ValueError, match="missing 'command'"):
        decode_command({"kwargs": {}})
