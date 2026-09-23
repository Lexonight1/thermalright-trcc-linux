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

    ``scan_devices`` both CARRIES the port (step 1) and hands the list to
    ``disambiguate`` (step 2), so this is the one test that proves the two
    halves are actually joined up — each is unit-tested on its own above and
    below, and neither would notice if the scan stopped calling the other.
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
    assert sorted(i.key for i in infos) == [
        f"{pair[0]:04x}:{pair[1]:04x}@1-11",
        f"{pair[0]:04x}:{pair[1]:04x}@1-13",
    ], "the scan must hand its result to disambiguate"


# ── step 2: the key, and only where one is needed ────────────────────────


def _info(vid: int, pid: int, path: str | None):
    from trcc.core.models import DeviceInfo
    return DeviceInfo(vid=vid, pid=pid, path=path)


def test_one_unit_keeps_the_key_it_has_always_had() -> None:
    """The whole reason the suffix is conditional.

    ``key`` names a device in 98 Commands, 115 API routes and 87 CLI
    arguments, and it is what ``trcc.json`` persists settings under.  A user
    with one cooler must see the same string after this change as before, or
    their config orphans and every documented command breaks.
    """
    from trcc.adapters.system._base import disambiguate

    out = disambiguate([_info(0x0402, 0x3922, "1-13.4")])
    assert [i.key for i in out] == ["0402:3922"]
    assert out[0].unit == ""


def test_two_of_the_same_model_get_their_port(caplog) -> None:
    from trcc.adapters.system._base import disambiguate

    out = disambiguate([_info(0x87AD, 0x70DB, "1-13"),
                        _info(0x87AD, 0x70DB, "1-11")])
    assert [i.key for i in out] == ["87ad:70db@1-13", "87ad:70db@1-11"]
    assert len({i.key for i in out}) == 2


def test_a_collision_does_not_rename_the_BYSTANDERS() -> None:
    """Only the colliding pair is touched, not the rest of the fleet."""
    from trcc.adapters.system._base import disambiguate

    out = disambiguate([_info(0x87AD, 0x70DB, "1-13"),
                        _info(0x87AD, 0x70DB, "1-11"),
                        _info(0x0402, 0x3922, "1-13.4")])
    assert sorted(i.key for i in out) == [
        "0402:3922", "87ad:70db@1-11", "87ad:70db@1-13",
    ]


def test_a_host_with_no_port_info_collapses_LOUDLY(caplog) -> None:
    """Keying on enumeration ORDER would be worse than not keying at all.

    Two units that came up in a different order after a reboot would each
    inherit the other's settings.  Collapsing is a visible failure; silently
    swapping a user's configuration is not.
    """
    import logging

    from trcc.adapters.system._base import disambiguate

    with caplog.at_level(logging.WARNING, logger="trcc.adapters.system._base"):
        out = disambiguate([_info(1, 2, None), _info(1, 2, None)])

    assert [i.key for i in out] == ["0001:0002", "0001:0002"]
    assert any("#287" in r.getMessage() for r in caplog.records), (
        "a collapse the user cannot see is the bug, not the fix"
    )


# ── step 2: settings survive the rename ──────────────────────────────────


def _settings(tmp_path):
    from trcc.services.settings import Settings

    from .conftest import FakePaths
    return Settings(FakePaths(tmp_path))


def test_a_suffixed_key_inherits_the_plain_keys_settings(tmp_path) -> None:
    """THE upgrade path — plug in a second cooler, keep your configuration."""
    st = _settings(tmp_path)
    st.for_device("87ad:70db").brightness = 42
    st.for_device("87ad:70db").orientation = 270

    for key in ("87ad:70db@1-13", "87ad:70db@1-11"):
        seeded = st.for_device(key)
        assert (seeded.brightness, seeded.orientation) == (42, 270), key


def test_the_twins_then_DIVERGE(tmp_path) -> None:
    """A shallow copy would make them share their lists.

    ``DeviceSettings`` has 21 fields, two of them lists
    (``user_overlay_elements``, ``slideshow_themes``).  With
    ``dataclasses.replace`` both units point at the SAME list objects, so
    editing one unit's overlay silently edits the other's.  MUTATION CHECK:
    swap ``deepcopy`` for ``replace`` in ``Settings._seed_for`` and the list
    assertions below fail while the scalar ones still pass — which is exactly
    how this would have shipped unnoticed.
    """
    st = _settings(tmp_path)
    st.for_device("87ad:70db").brightness = 42

    a = st.for_device("87ad:70db@1-13")
    b = st.for_device("87ad:70db@1-11")

    a.brightness = 10
    assert (a.brightness, b.brightness) == (10, 42)

    a.slideshow_themes.append("only-on-A")
    assert a.slideshow_themes is not b.slideshow_themes
    assert b.slideshow_themes == []


def test_a_brand_new_key_still_seeds_from_the_global_formats(tmp_path) -> None:
    """No ancestor — the pre-existing behaviour, unchanged."""
    st = _settings(tmp_path)
    st.set_global_temp_unit("F")

    fresh = st.for_device("dead:beef@1-9")
    assert fresh.temp_unit == "F"
