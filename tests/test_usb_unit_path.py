"""``usb_path`` — which PORT a unit is plugged into (#287).

Two identical coolers share a VID/PID and ship no serial number, so nothing
in the USB descriptor tells them apart.  What does is the topology: they are
in different ports.  ``usb_find(find_all=True)`` already yields one object per
physical unit and that object carries ``bus`` / ``port_numbers`` / ``address``
-- this tree read ``iSerialNumber`` and ``bcdDevice`` off it and dropped the
rest, so both units produced byte-identical ``DeviceInfo``.

This is step one of the fix and it is deliberately BEHAVIOUR-NEUTRAL: the
value is carried on ``DeviceInfo.path`` (a field that already existed,
documented as *"the path differs per enumeration"*, and populated at zero of
its construction sites) and nothing reads it yet.

MEASURED against the 9 real USB devices on the dev box: 9 distinct paths, 0
collisions -- including two identical ``05e3:0608`` hubs, which is #287's
exact shape, resolving to ``1-13`` and ``1-11``.
"""
from __future__ import annotations

from trcc.adapters.system._base import usb_path


class _Dev:
    """The attributes PyUSB sets in ``usb.core.Device.__init__``.

    ``None`` is what PyUSB itself stores when the backend does not supply a
    value -- not a hypothetical, which is why every one of these is optional.
    """

    def __init__(self, bus=None, port_numbers=None, address=None) -> None:
        self.bus = bus
        self.port_numbers = port_numbers
        self.address = address


def test_the_port_chain_is_preferred_and_reads_like_the_kernel() -> None:
    """``1-13.4`` is what ``lsusb -t`` and ``dmesg`` call the same port.

    A reporter has to be able to match the string in their log against the
    tools they already have, so the format is not ours to invent.
    """
    assert usb_path(_Dev(bus=1, port_numbers=(13, 4), address=7)) == "1-13.4"
    assert usb_path(_Dev(bus=1, port_numbers=(2,), address=2)) == "1-2"


def test_two_identical_units_differ_by_port() -> None:
    """THE point of #287 — same vid:pid, same (absent) serial, two ports."""
    left = usb_path(_Dev(bus=1, port_numbers=(13,), address=4))
    right = usb_path(_Dev(bus=1, port_numbers=(11,), address=3))
    assert left != right
    assert (left, right) == ("1-13", "1-11")


def test_address_is_the_fallback_and_is_SPELLED_differently() -> None:
    """``@`` marks "this one moves"; ``-`` marks "this one is stable".

    The port chain survives a replug and a reboot, so it can key persisted
    settings.  ``address`` is handed out by the host controller and is
    reassigned on every replug -- fine for telling two units apart right now,
    never for remembering which was which.  Two formats that could not be
    told apart on sight would let the unstable one silently key a config.
    """
    assert usb_path(_Dev(bus=2, port_numbers=None, address=1)) == "2@1"
    assert usb_path(_Dev(bus=1, port_numbers=(), address=9)) == "1@9"
    assert "@" not in usb_path(_Dev(bus=1, port_numbers=(3,), address=9))


def test_no_bus_means_this_host_cannot_tell_units_apart() -> None:
    """``None`` is a real answer, not a failure to handle.

    PyUSB stores ``None`` whenever the backend does not supply the field, so
    this is guarded on EVERY platform rather than only the ones we cannot
    test on.
    """
    assert usb_path(_Dev()) is None
    assert usb_path(_Dev(bus=None, port_numbers=(1,), address=2)) is None
    assert usb_path(_Dev(bus=1, port_numbers=None, address=None)) is None


def test_it_survives_an_object_with_none_of_the_attributes() -> None:
    """An older backend's Device may not define them at all."""
    assert usb_path(object()) is None


def test_the_scan_carries_the_path_onto_DeviceInfo(monkeypatch) -> None:
    """End to end through the real ``scan_devices``, with two twins.

    Asserted on ``path`` and NOT on ``key``: the key is still ``vid:pid`` for
    both at this step, deliberately.  A test that asserted distinct keys here
    would be asserting a later increment's behaviour and would fail for the
    right reason at the wrong time.
    """
    from trcc.adapters.system import _base

    twins = [_Dev(bus=1, port_numbers=(13,), address=4),
             _Dev(bus=1, port_numbers=(11,), address=3)]
    for d in twins:                      # what scan_devices reads off each
        d.iSerialNumber = 0
        d.bcdDevice = 0x0100

    pair = next(iter(_base.ALL_DEVICES))
    monkeypatch.setattr(
        _base, "usb_find",
        lambda find_all=False, idVendor=None, idProduct=None: (
            twins if (idVendor, idProduct) == pair else []),
    )

    # Called UNBOUND: ``scan_devices`` touches ``self`` only for
    # ``type(self).__name__`` in its log lines, and ``BaseOS`` has eleven
    # abstract methods that a subclass would have to stub for nothing.
    infos = _base.BaseOS.scan_devices(_Dev())

    assert len(infos) == 2, "both units must survive the scan"
    assert [i.path for i in infos] == ["1-13", "1-11"]
    assert len({i.path for i in infos}) == 2, "the units are distinguishable"
    assert len({i.key for i in infos}) == 1, (
        "key is still vid:pid at this step — by design, see the docstring"
    )
