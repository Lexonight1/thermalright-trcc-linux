"""FreeBSDHotplugMonitor — devd message parsing + bus dispatch.

The seqpacket recv loop only spins on real FreeBSD; the pure parsing
logic (``_parse_devd_event`` + ``_dispatch``) is exercised here on the
Linux dev box.
"""
from __future__ import annotations

import pytest

from trcc.adapters.system._hotplug import (
    FreeBSDHotplugMonitor,
    _parse_devd_event,
)
from trcc.core.events import (
    DeviceAttached,
    DeviceDetached,
    EventBus,
)

# ── _parse_devd_event ───────────────────────────────────────────────


_ATTACH_MSG = (
    "!system=USB subsystem=DEVICE type=ATTACH ugen=ugen3.4 cdev=ugen3.4 "
    "vendor=0x0402 product=0x3922 devclass=0xff devsubclass=0xff "
    'sernum="ABC123" release=0x0100 mode=host port=4 parent=ugen3\n'
)
_DETACH_MSG = (
    "!system=USB subsystem=DEVICE type=DETACH ugen=ugen3.4 cdev=ugen3.4 "
    "vendor=0x0402 product=0x3922 devclass=0xff devsubclass=0xff "
    'sernum="ABC123" release=0x0100 mode=host port=4 parent=ugen3\n'
)


def test_parses_canonical_attach_event() -> None:
    assert _parse_devd_event(_ATTACH_MSG) == ("ATTACH", "0402", "3922")


def test_parses_detach_event() -> None:
    assert _parse_devd_event(_DETACH_MSG) == ("DETACH", "0402", "3922")


def test_uppercase_hex_normalized_to_lowercase() -> None:
    msg = (
        "!system=USB subsystem=DEVICE type=ATTACH "
        "vendor=0x0416 product=0x5302\n"
    )
    assert _parse_devd_event(msg) == ("ATTACH", "0416", "5302")


def test_short_hex_zero_padded() -> None:
    """devd sometimes drops leading zeros (vendor=0x416 instead of 0x0416)."""
    msg = (
        "!system=USB subsystem=DEVICE type=ATTACH "
        "vendor=0x416 product=0x5302\n"
    )
    assert _parse_devd_event(msg) == ("ATTACH", "0416", "5302")


def test_ignores_non_usb_event() -> None:
    """devd publishes events for other subsystems — we only care about USB."""
    msg = "!system=ETHERNET subsystem=NET type=ATTACH vendor=0x1234 product=0x5678\n"
    assert _parse_devd_event(msg) is None


def test_ignores_usb_interface_subsystem() -> None:
    """USB INTERFACE attaches fire per endpoint; only DEVICE is the parent."""
    msg = "!system=USB subsystem=INTERFACE type=ATTACH vendor=0x0402 product=0x3922\n"
    assert _parse_devd_event(msg) is None


def test_returns_none_when_vendor_missing() -> None:
    msg = "!system=USB subsystem=DEVICE type=ATTACH product=0x3922\n"
    assert _parse_devd_event(msg) is None


def test_returns_none_when_product_missing() -> None:
    msg = "!system=USB subsystem=DEVICE type=ATTACH vendor=0x0402\n"
    assert _parse_devd_event(msg) is None


def test_returns_none_on_empty_message() -> None:
    assert _parse_devd_event("") is None


# ── _dispatch — bus publication ─────────────────────────────────────


@pytest.fixture
def monitor_with_bus() -> tuple[FreeBSDHotplugMonitor, list[object]]:
    bus = EventBus()
    captured: list[object] = []
    bus.subscribe(DeviceAttached, captured.append)
    bus.subscribe(DeviceDetached, captured.append)
    monitor = FreeBSDHotplugMonitor()
    monitor._bus = bus
    return monitor, captured


def test_dispatch_publishes_attached_for_known_device(
    monitor_with_bus: tuple[FreeBSDHotplugMonitor, list[object]],
) -> None:
    monitor, captured = monitor_with_bus
    monitor._dispatch(_ATTACH_MSG)

    assert len(captured) == 1
    evt = captured[0]
    assert isinstance(evt, DeviceAttached)
    assert evt.key == "0402:3922"
    assert evt.vid == 0x0402
    assert evt.pid == 0x3922


def test_dispatch_publishes_detached_for_known_device(
    monitor_with_bus: tuple[FreeBSDHotplugMonitor, list[object]],
) -> None:
    monitor, captured = monitor_with_bus
    monitor._dispatch(_DETACH_MSG)

    assert len(captured) == 1
    assert isinstance(captured[0], DeviceDetached)
    assert captured[0].key == "0402:3922"


def test_dispatch_ignores_unknown_vid_pid(
    monitor_with_bus: tuple[FreeBSDHotplugMonitor, list[object]],
) -> None:
    monitor, captured = monitor_with_bus
    monitor._dispatch(
        "!system=USB subsystem=DEVICE type=ATTACH "
        "vendor=0xDEAD product=0xBEEF\n",
    )
    assert captured == []


def test_dispatch_ignores_malformed_message(
    monitor_with_bus: tuple[FreeBSDHotplugMonitor, list[object]],
) -> None:
    monitor, captured = monitor_with_bus
    monitor._dispatch("garbage line that isn't devd format at all")
    monitor._dispatch("!system=ETHERNET subsystem=NET type=ATTACH\n")
    assert captured == []


# ── start() degrades when socket is unavailable ─────────────────────


def test_start_no_op_when_socket_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No devd running → start() logs once and returns idle."""
    from trcc.adapters.system import _hotplug as hotplug_mod

    monkeypatch.setattr(hotplug_mod, "_open_devd_socket", lambda: None)

    monitor = FreeBSDHotplugMonitor()
    monitor.start(EventBus())

    assert monitor.is_running is False


def test_stop_is_idempotent_when_never_started() -> None:
    monitor = FreeBSDHotplugMonitor()
    monitor.stop()
    assert monitor.is_running is False
