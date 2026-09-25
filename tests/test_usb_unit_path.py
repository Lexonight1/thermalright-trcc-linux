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


# ── step 3: two units, two Device objects ────────────────────────────────


def _twin_app(tmp_path):
    from trcc.adapters.infra.send_scheduler import SyncSendScheduler
    from trcc.app import App

    from .conftest import _CliRenderer
    from .mock_platform import MockPlatform

    spec = {"type": "lcd", "name": "HR10 2280 PRO", "vid": "87ad",
            "pid": "70db", "pm": 72, "resolution": "480x480"}
    return App(platform=MockPlatform([dict(spec), dict(spec)], tmp_path),
               send_scheduler=SyncSendScheduler(),
               renderer=_CliRenderer())      # type: ignore[arg-type]


def _twins():
    from trcc.adapters.system._base import disambiguate
    return disambiguate([_info(0x87AD, 0x70DB, "1-13"),
                         _info(0x87AD, 0x70DB, "1-11")])


def test_the_scan_cache_no_longer_overwrites_itself(tmp_path) -> None:
    """``remember_scan`` does ``_scanned[info.key] = info``.

    It never needed changing — distinct keys from step 2 fixed it for free.
    Pinned anyway, because the line is a silent overwrite: if a later change
    made twins share a key again, nothing else in the suite would notice.
    """
    app = _twin_app(tmp_path)
    app.remember_scan(_twins())
    assert len(app._scanned) == 2


def test_two_identical_units_become_two_devices(tmp_path) -> None:
    """THE fix for #287's "in use by another process", at the object level."""
    app = _twin_app(tmp_path)
    for info in _twins():
        app.attach(info.vid, info.pid, unit=info.unit)

    assert sorted(app.devices) == ["87ad:70db@1-11", "87ad:70db@1-13"]
    left = app.get("87ad:70db@1-13")
    right = app.get("87ad:70db@1-11")
    assert left is not right


def test_one_device_still_attaches_under_the_plain_key(tmp_path) -> None:
    """``unit`` is a keyword with an empty default precisely so the 31
    existing ``attach`` call sites keep meaning "the only one of this model".
    """
    from trcc.adapters.infra.send_scheduler import SyncSendScheduler
    from trcc.app import App

    from .conftest import _CliRenderer
    from .mock_platform import MockPlatform

    app = App(platform=MockPlatform([{"type": "lcd", "name": "one",
                                      "vid": "0402", "pid": "3922", "pm": 32,
                                      "fbl": 100, "resolution": "320x320"}],
                                    tmp_path),
              send_scheduler=SyncSendScheduler(),
              renderer=_CliRenderer())        # type: ignore[arg-type]
    app.attach(0x0402, 0x3922)
    assert list(app.devices) == ["0402:3922"]


def test_device_key_mirrors_DeviceInfo_key(tmp_path) -> None:
    """The two are COMPARED, so they must agree by construction.

    The scan produces the ``DeviceInfo``; the composition root builds the
    ``Device``; a user's settings are looked up under whichever string
    reaches them first.  Two formats that drifted apart would send one unit's
    configuration to the other.
    """
    app = _twin_app(tmp_path)
    for info in _twins():
        device = app.attach(info.vid, info.pid, unit=info.unit)
        assert device.key == info.key


# ── step 4: each Device opens ITS OWN unit ───────────────────────────────


def test_find_unit_never_falls_back_to_a_sibling(monkeypatch) -> None:
    """Asking for a named unit that is gone must return NOTHING.

    "Whichever one libusb lists first" is the whole bug: opening the sibling
    would drive the other panel while reporting success, which is worse than
    failing to open at all.
    """
    from trcc.adapters.device import _pyusb_find

    twins = [_Dev(bus=1, port_numbers=(13,), address=4),
             _Dev(bus=1, port_numbers=(11,), address=3)]
    monkeypatch.setattr(_pyusb_find, "find",
                        lambda **kw: twins if kw.get("find_all") else twins[0])

    assert _pyusb_find.find_unit(1, 2, "1-11") is twins[1]
    assert _pyusb_find.find_unit(1, 2, "1-13") is twins[0]
    assert _pyusb_find.find_unit(1, 2, "1-99") is None


def test_an_empty_unit_is_not_a_match_all() -> None:
    """``find_unit`` must never answer a "give me any" question.

    Callers that mean "the only one of this model" call ``find`` directly, so
    this cannot silently hand back an arbitrary device to a caller that asked
    for a named one.
    """
    from trcc.adapters.device._pyusb_find import find_unit

    assert find_unit(0x0402, 0x3922, "") is None


def test_the_bulk_transport_carries_the_unit() -> None:
    """And a transport built without one behaves exactly as it always has."""
    from trcc.adapters.device.transport import PyUsbBulkTransport

    assert PyUsbBulkTransport(1, 2, None, "1-13")._unit == "1-13"
    assert PyUsbBulkTransport(1, 2)._unit == ""


def test_open_transport_passes_the_unit_to_its_opener(tmp_path) -> None:
    """The Platform port threads it through; ``_open_bulk`` receives it.

    Driven on the REAL ``BaseOS.open_transport``, unbound: ``FakePlatform``
    overrides ``open_transport`` wholesale and never reaches an opener, so a
    test written against it would pass without exercising the table at all.
    """
    from trcc.adapters.system._base import BaseOS
    from trcc.core.models import Wire

    seen: dict = {}

    class _Host:
        def _open_bulk(self, vid, pid, serial=None, unit=""):
            seen.update(vid=vid, pid=pid, serial=serial, unit=unit)
            return object()

        def _transport_openers(self):
            return {}          # every unlisted wire falls through to bulk

    BaseOS.open_transport(_Host(), Wire.BULK, 0x87AD, 0x70DB, None, "1-13")
    assert seen == {"vid": 0x87AD, "pid": 0x70DB,
                    "serial": None, "unit": "1-13"}
    del tmp_path


def test_attach_threads_the_unit_all_the_way_to_the_transport(tmp_path) -> None:
    """THE joined-up test: ``App.attach(unit=…)`` reaches ``open_transport``.

    Every layer below is unit-tested on its own and none would notice the
    layer above it dropping the keyword.
    """
    seen: dict = {}
    app = _twin_app(tmp_path)
    real = app.platform.open_transport

    def spy(wire, vid, pid, serial=None, unit=""):
        seen.update(wire=wire, unit=unit)
        return real(wire, vid, pid, serial)

    app.platform.open_transport = spy          # type: ignore[assignment]
    app.attach(0x87AD, 0x70DB, unit="1-11")
    assert seen.get("unit") == "1-11"


def test_both_hid_bindings_declare_open_path() -> None:
    """MEASURED, not assumed: cython-hidapi has ``device().open_path`` and
    apmorton's constructor takes ``path=``.  The ABC is a real abstraction
    over two real APIs — if a future binding cannot do it, it fails loudly at
    subclass definition rather than silently opening the wrong panel."""
    from trcc.adapters.device.transport import (
        _ApmortonHidBinding,
        _CythonHidBinding,
        _HidBinding,
    )

    assert "open_path" in _HidBinding.__abstractmethods__ or hasattr(
        _HidBinding, "open_path")
    for child in (_CythonHidBinding, _ApmortonHidBinding):
        assert not getattr(child.open_path, "__isabstractmethod__", False), child


# ── #287 reached from the entry the user touches ───────────────────────────
#
# Every test above calls ``attach(unit=…)`` directly, and all of them passed
# while the fix was unreachable: ``ConnectDevice`` rejected ``vid:pid@unit``,
# coldplug attached each twin unit-less, and v9.10.3 shipped announcing it
# fixed.  These drive ``discover_and_connect`` and the Commands, which is what
# a user's launch actually runs.


def _opened_units(app) -> list[str]:
    """Wrap ``open_transport`` and return the list it appends each unit to."""
    units: list[str] = []
    real = app.platform.open_transport

    def spy(wire, vid, pid, serial=None, unit=""):
        units.append(unit)
        return real(wire, vid, pid, serial, unit)

    app.platform.open_transport = spy          # type: ignore[assignment]
    return units


def test_coldplug_connects_each_twin_on_its_own_unit(tmp_path) -> None:
    """THE #287 fix, end to end: two identical coolers at launch."""
    app = _twin_app(tmp_path)
    opened = _opened_units(app)

    app.discover_and_connect()

    assert sorted(app.devices) == ["87ad:70db@1-1", "87ad:70db@1-2"]
    assert all(d.is_connected for d in app.devices.values())
    assert sorted(opened) == ["1-1", "1-2"]


def test_coldplug_keeps_the_plain_key_for_a_single_cooler(tmp_path) -> None:
    """99% of users: one of each model, and the key they have always had."""
    from trcc.adapters.infra.send_scheduler import SyncSendScheduler
    from trcc.app import App

    from .conftest import _CliRenderer
    from .mock_platform import MockPlatform

    app = App(platform=MockPlatform([{"type": "lcd", "name": "one",
                                      "vid": "87ad", "pid": "70db", "pm": 72,
                                      "resolution": "480x480"}], tmp_path),
              send_scheduler=SyncSendScheduler(),
              renderer=_CliRenderer())      # type: ignore[arg-type]
    app.discover_and_connect()
    assert list(app.devices) == ["87ad:70db"]


def test_a_twin_key_is_accepted_by_connect_and_orientation(tmp_path) -> None:
    """Both Commands parsed ``vvvv:pppp`` only and called the twin invalid."""
    from trcc.core.commands import ConnectDevice, SetOrientation

    app = _twin_app(tmp_path)
    connect = app.dispatch(ConnectDevice(key="87ad:70db@1-2"))
    rotate = app.dispatch(SetOrientation(key="87ad:70db@1-2", degrees=90))

    assert (connect.ok, rotate.ok) == (True, True), (connect.message, rotate.message)
    assert "87ad:70db@1-2" in app.devices


def test_a_twin_resolves_its_quirks_from_its_own_scan_entry(tmp_path) -> None:
    """The scan caches a twin as ``vid:pid@unit``; a plain lookup missed it
    and every twin got bcdDevice 0 — the wrong firmware's quirks."""
    from trcc.adapters.infra.send_scheduler import SyncSendScheduler
    from trcc.app import App
    from trcc.core.models import quirks_for

    from .conftest import _CliRenderer
    from .mock_platform import MockPlatform

    spec = {"type": "lcd", "name": "Warframe SE", "vid": "0416", "pid": "5302",
            "pm": 58, "bcd": "0407"}
    app = App(platform=MockPlatform([dict(spec), dict(spec)], tmp_path),
              send_scheduler=SyncSendScheduler(),
              renderer=_CliRenderer())      # type: ignore[arg-type]
    app.remember_scan(app.platform.scan_devices())

    assert app._quirks_for(0x0416, 0x5302, "1-2") == quirks_for(0x0416, 0x5302, 0x0407)
    assert quirks_for(0x0416, 0x5302, 0x0407) != quirks_for(0x0416, 0x5302, 0)


def test_the_key_format_round_trips() -> None:
    from trcc.core.models import format_device_key, parse_device_key

    for vid, pid, unit in ((0x87AD, 0x70DB, ""), (0x87AD, 0x70DB, "1-2.3"),
                           (0x0416, 0x5302, "3@7")):
        assert parse_device_key(format_device_key(vid, pid, unit)) == (vid, pid, unit)


def test_a_malformed_key_is_still_rejected() -> None:
    import pytest

    from trcc.core.models import parse_device_key

    for bad in ("", "87ad", "87ad:zzzz", "87ad:70db:1", "@1-2"):
        with pytest.raises(ValueError):
            parse_device_key(bad)


def test_the_mock_warns_on_different_specs_but_not_on_twins(tmp_path, caplog) -> None:
    """Identical specs are two coolers; different ones under one vid:pid are a
    fleet mistake, because replies are scripted per vid:pid."""
    from .mock_platform import MockPlatform

    twin = {"type": "lcd", "name": "a", "vid": "87ad", "pid": "70db", "pm": 72}
    MockPlatform([dict(twin), dict(twin, name="b")], tmp_path).scan_devices()
    assert "DIFFERENT specs" not in caplog.text

    MockPlatform([dict(twin), dict(twin, pm=64)], tmp_path).scan_devices()
    assert "DIFFERENT specs" in caplog.text


# ── every face shows and targets the UNIT, not the model (#287) ──────────────
#
# ``product.key`` names the model, so each face that printed or connected it
# gave two identical coolers the same address.  ``DiscoverResult.units()`` is
# the one pairing of a unit's key with its product; these pin each face on it.

_TWIN_KEYS = ["87ad:70db@1-1", "87ad:70db@1-2"]


def test_discover_result_pairs_each_unit_with_its_product(tmp_path) -> None:
    from trcc.core.commands import DiscoverDevices

    result = _twin_app(tmp_path).dispatch(DiscoverDevices())
    units = list(result.units())
    assert [key for key, _ in units] == _TWIN_KEYS
    assert {p.key for _, p in units} == {"87ad:70db"}


def _cli_on_twins(tmp_path, cli_runner, args: list[str]) -> str:
    """Run a CLI command against two identical coolers; return its output."""
    from trcc.ui.cli import _ctx
    from trcc.ui.cli.main import app as cli

    from .conftest import _CliRenderer
    from .mock_platform import MockPlatform

    spec = {"type": "lcd", "name": "HR10", "vid": "87ad", "pid": "70db", "pm": 72}
    _ctx.set_platform(MockPlatform([dict(spec), dict(spec)], tmp_path))
    _ctx.set_renderer(_CliRenderer())  # type: ignore[arg-type]
    try:
        return cli_runner.invoke(cli, args).output
    finally:
        _ctx.get_app.cache_clear()
        _ctx._platform_override = None
        _ctx._renderer_override = None


def test_cli_device_list_prints_each_twin_key(tmp_path, cli_runner) -> None:
    out = _cli_on_twins(tmp_path, cli_runner, ["device", "list"])
    assert all(key in out for key in _TWIN_KEYS), out


def test_cli_display_resume_addresses_each_twin(tmp_path, cli_runner) -> None:
    """The autostart path.  It built ``vid:pid`` by hand, so at boot one of two
    identical coolers was resumed twice and the other left blank."""
    out = _cli_on_twins(tmp_path, cli_runner,
                        ["display", "resume", "--retries", "1"])
    assert all(f"[{key}]" in out for key in _TWIN_KEYS), out


def test_cli_status_snapshots_each_twin(tmp_path, cli_runner) -> None:
    import json

    out = _cli_on_twins(tmp_path, cli_runner, ["status", "--json"])
    keys = [snap["key"] for snap in json.loads(out)["lcd_devices"]]
    assert keys == _TWIN_KEYS


def test_api_lists_and_finds_each_twin(tmp_path) -> None:
    from fastapi.testclient import TestClient

    from trcc.ui.api.main import build_app

    with TestClient(build_app(trcc=_twin_app(tmp_path))) as client:
        listed = [p["key"] for p in client.get("/devices").json()["products"]]
        found = client.get("/devices/87ad:70db@1-2")
    assert listed == _TWIN_KEYS
    assert (found.status_code, found.json()["key"]) == (200, "87ad:70db@1-2")


def test_qtgui_device_panel_lists_each_twin_key(tmp_path, qtbot) -> None:
    from trcc.ui.bus_bridge import BusBridge
    from trcc.ui.qtgui.panels.device_panel import DevicePanel

    app = _twin_app(tmp_path)
    panel = DevicePanel(app, BusBridge(app.events))
    qtbot.addWidget(panel)
    panel._on_scan()
    keys = [panel._list.item(i).data(0x0100) for i in range(panel._list.count())]
    assert keys == _TWIN_KEYS


def test_the_report_probes_each_twin_on_its_own_unit(tmp_path) -> None:
    from trcc.adapters.diagnostics.debug_report import _collect_devices

    app = _twin_app(tmp_path)
    opened = _opened_units(app)
    powered: list[str] = []
    app.platform.usb_power_state = (                  # type: ignore[method-assign]
        lambda vid, pid, unit="": powered.append(unit))
    rows, error = _collect_devices(app.platform)
    assert (error, [r["key"] for r in rows]) == ("", _TWIN_KEYS)
    assert sorted(opened) == sorted(powered) == ["1-1", "1-2"]


# ── hotplug: a twin arriving or leaving while TRCC runs (#287) ─────────────
#
# Every monitor publishes the plain ``vid:pid``.  A twin's unplug never matched
# its ``@port`` entry (a corpse stayed attached) and a twin's plug connected
# unit-less beside the first, which was never re-keyed.  The App now
# reconciles the model from a fresh scan.  These drive the real handlers by
# publishing the events a monitor publishes.

_HR10 = {"type": "lcd", "name": "HR10", "vid": "87ad", "pid": "70db", "pm": 72}


def _hotplug(app, cls) -> None:
    from trcc.core.events import DeviceAttached, DeviceDetached

    event = {"attach": DeviceAttached, "detach": DeviceDetached}[cls]
    app.events.publish(event(key="87ad:70db", vid=0x87AD, pid=0x70DB))


def _plug(app, n: int) -> None:
    """Set the simulated fleet to ``n`` identical HR10s."""
    from .mock_platform import DeviceSpec
    app.platform._specs[:] = [DeviceSpec.parse(dict(_HR10)) for _ in range(n)]


def _connected(app) -> list[str]:
    return sorted(k for k, d in app.devices.items() if d.is_connected)


def test_a_second_twin_arriving_rekeys_the_first_and_connects_both(tmp_path) -> None:
    app = _twin_app(tmp_path)
    _plug(app, 1)
    app.discover_and_connect()
    assert _connected(app) == ["87ad:70db"]

    _plug(app, 2)
    _hotplug(app, "attach")
    assert sorted(app.devices) == _connected(app) == _TWIN_KEYS


def test_one_twin_leaving_leaves_the_survivor_connected(tmp_path) -> None:
    app = _twin_app(tmp_path)
    app.discover_and_connect()
    assert _connected(app) == _TWIN_KEYS

    _plug(app, 1)
    _hotplug(app, "detach")
    assert sorted(app.devices) == _connected(app) == ["87ad:70db"]


def test_a_single_cooler_unplug_and_replug_is_unchanged(tmp_path) -> None:
    """#246 / #254: release on unplug, reconnect on replug — no twin involved."""
    app = _twin_app(tmp_path)
    _plug(app, 1)
    app.discover_and_connect()

    _plug(app, 0)
    _hotplug(app, "detach")
    assert app.devices == {}

    _plug(app, 1)
    _hotplug(app, "attach")
    assert _connected(app) == ["87ad:70db"]


def test_an_arrival_that_outruns_the_scan_still_connects(tmp_path) -> None:
    """A lone cooler must not lose hotplug to a slow enumeration."""
    app = _twin_app(tmp_path)
    _plug(app, 0)
    _hotplug(app, "attach")
    assert _connected(app) == ["87ad:70db"]


def test_polling_publishes_a_twin_arriving_as_one_model_change() -> None:
    """A set of ``(vid, pid)`` hid the second twin entirely: no event at all."""
    from trcc.adapters.system._hotplug import PollingHotplugMonitor
    from trcc.core.events import DeviceAttached, DeviceDetached, EventBus

    snaps = [{(0x87AD, 0x70DB, "")},
             {(0x87AD, 0x70DB, "1-1"), (0x87AD, 0x70DB, "1-2")}]
    monitor = PollingHotplugMonitor(scan=lambda: snaps[0])
    bus, seen = EventBus(), []
    bus.subscribe(DeviceAttached, seen.append)
    bus.subscribe(DeviceDetached, seen.append)
    monitor._bus = bus
    monitor._last_seen = monitor._known_units()
    snaps.pop(0)
    monitor._tick()
    assert [type(e).__name__ for e in seen] == ["DeviceDetached", "DeviceAttached"]
