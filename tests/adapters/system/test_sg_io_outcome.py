"""What a completed SG_IO means -- the panel can be GONE with status 0 (#254).

While a device is being removed, or once it is offlined, ``ioctl(SG_IO)``
returns 0 with ``host_status=DID_NO_CONNECT`` and ``status=0`` (scsi_lib.c,
usb-storage scsiglue.c, uas.c).  ``LinuxScsiTransport`` tested ``status``
alone, so every frame to a dead panel counted as sent and the panel stayed
"connected".  The ioctl is faked; the transport, its header and the recovery
policy are the real ones.
"""
from __future__ import annotations

import ctypes
import fcntl
import sys
from pathlib import Path

import pytest

from trcc.core.device_recovery import DISCONNECT_FAILURE_THRESHOLD, is_disconnect_error
from trcc.core.errors import DeviceDisconnectedError, TransportError

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="LinuxScsiTransport is Linux-only",
)

_DID_NO_CONNECT, _DID_ERROR, _CHECK_CONDITION = 0x01, 0x07, 0x02


@pytest.fixture
def transport(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """An open transport whose every SG_IO completes with ``outcome``."""
    from trcc.adapters.system.linux import LinuxScsiTransport, _SgIoHdr

    node = tmp_path / "sg0"
    node.write_bytes(b"")
    t = LinuxScsiTransport(str(node))
    assert t.open()
    t.outcome = {}                           # type: ignore[attr-defined]

    def fake_ioctl(_fd: int, _request: int, buf) -> int:
        hdr = _SgIoHdr.from_buffer_copy(buf.raw)
        for name, value in t.outcome.items():  # type: ignore[attr-defined]
            setattr(hdr, name, value)
        ctypes.memmove(buf, ctypes.addressof(hdr), ctypes.sizeof(hdr))
        return 0

    monkeypatch.setattr(fcntl, "ioctl", fake_ioctl)
    yield t
    t.close()


def _gone() -> dict[str, int]:
    return {"status": 0, "host_status": _DID_NO_CONNECT, "info": 1}


@pytest.mark.parametrize("op", ["send", "read"])
def test_a_gone_device_raises_disconnect_class(transport, op: str) -> None:
    transport.outcome = _gone()
    with pytest.raises(TransportError) as raised:
        if op == "send":
            transport.send_cdb(b"\x00" * 16, b"frame")
        else:
            transport.read_cdb(b"\x00" * 16, 8)
    assert is_disconnect_error(raised.value)


@pytest.mark.parametrize("outcome", [
    {"status": _CHECK_CONDITION, "masked_status": 1, "info": 1},
    {"host_status": _DID_ERROR, "info": 1},    # usb-storage: often transient
])
def test_any_other_error_is_a_soft_failure_to_send(transport, outcome) -> None:
    """The DID_ERROR row read as SUCCESS: only ``status`` was tested."""
    transport.outcome = outcome
    assert transport.send_cdb(b"\x00" * 16, b"frame") is False


def test_a_failed_scsi_status_reads_nothing(transport) -> None:
    transport.outcome = {"status": _CHECK_CONDITION, "masked_status": 1,
                         "info": 1, "resid": 0}
    assert transport.read_cdb(b"\x00" * 16, 8) == b""


def test_a_host_error_with_status_good_keeps_the_reply(transport) -> None:
    """#301: the 0402:3922 handshake poll completes host=7 (DID_ERROR),
    status 0, reply delivered -- on real glass, every time.  Dropping it read
    every such panel as FBL 100, "Frozen Warframe Pro"."""
    transport.outcome = {"status": 0, "host_status": _DID_ERROR, "info": 1,
                         "resid": 6}
    assert transport.read_cdb(b"\x00" * 16, 8) == b"\x00\x00"


def test_a_host_error_with_nothing_delivered_reads_nothing(transport) -> None:
    transport.outcome = {"status": 0, "host_status": _DID_ERROR, "info": 1,
                         "resid": 8}
    assert transport.read_cdb(b"\x00" * 16, 8) == b""


def test_a_clean_completion_is_a_send(transport) -> None:
    transport.outcome = {"status": 0, "host_status": 0, "info": 0}
    assert transport.send_cdb(b"\x00" * 16, b"frame") is True


def test_a_gone_panel_is_marked_lost_by_the_recovery_policy(transport) -> None:
    """End to end through ``Device._send_with_recovery``: it used to return
    True for every frame, so the threshold never fired."""
    from trcc.adapters.device.scsi_lcd import ScsiLcd
    from trcc.core.models import Kind, ProductInfo, Wire

    info = ProductInfo(vid=0x0402, pid=0x3922, vendor="ALi", product="SCSI LCD",
                       wire=Wire.SCSI, kind=Kind.LCD)
    device = ScsiLcd(info, transport)
    transport.outcome = _gone()

    def write() -> bool:
        return transport.send_cdb(b"\x00" * 16, b"frame")

    for _ in range(DISCONNECT_FAILURE_THRESHOLD - 1):
        assert device._send_with_recovery(write) is False
    with pytest.raises(DeviceDisconnectedError):
        device._send_with_recovery(write)


def test_the_panels_normal_poll_answer_is_not_a_warning(
        transport, caplog: pytest.LogCaptureFixture) -> None:
    """host=7 with status GOOD is how the panel answers EVERY poll, so a
    warning for it printed two WARNING lines on every connect, at the default
    verbosity -- measured on the dev box's own 0402:3922, 2026-10-09.
    MUTATION CHECK: route this case through ``_sg_failed`` again -> fails."""
    transport.outcome = {"status": 0, "host_status": _DID_ERROR, "info": 1,
                         "resid": 6}
    with caplog.at_level("INFO", logger="trcc.adapters.system.linux"):
        assert transport.read_cdb(b"\x00" * 16, 8) == b"\x00\x00"
    assert [r.getMessage() for r in caplog.records if r.levelname == "WARNING"] == []
    assert any("host=7" in r.getMessage() for r in caplog.records)
