"""Commands — UI contract dispatched through App.dispatch."""
from __future__ import annotations

from trcc.app import App
from trcc.core.commands import (
    DisableAutostart,
    EnableAutostart,
    GetAutostartStatus,
    GetPlatformInfo,
    ListMemorySlots,
    ReadSensors,
    RefreshAutostart,
)


def test_read_sensors_returns_merged_descriptors_and_live_values(fake_platform) -> None:
    """ReadSensors must enrich discover() with read_all() values."""
    app = App(fake_platform)
    result = app.dispatch(ReadSensors())

    assert result.ok is True
    assert result.readings, "at least some readings expected"

    by_id = {r.sensor_id: r for r in result.readings}
    # CPU temp from the Fake CPU source
    assert by_id["cpu:temp"].value == 42.0
    # GPU temp from the Fake NVIDIA GPU
    assert by_id["gpu:primary:temp"].value == 55.0


def test_get_platform_info_returns_fake_platform_fields(fake_platform) -> None:
    app = App(fake_platform)
    r = app.dispatch(GetPlatformInfo())

    assert r.ok is True
    assert r.distro_name == "Fake Linux"
    assert r.install_method == "test"
    # Paths all derive from the FakePaths root
    assert r.config_dir.endswith(str(fake_platform.paths().config_dir()))
    # The hint the gui shows when nothing is attached.  Asserted against the
    # platform's OWN answer, not a literal: the point is that the Query
    # CARRIES it, so the gui never has to reach ``app.platform`` — which is
    # an AttributeError under TRCC_DAEMON=1.
    assert r.no_devices_hint == fake_platform.no_devices_hint()
    assert r.no_devices_hint, "platform hint came back empty"


def test_autostart_enable_then_status_reports_enabled(fake_platform) -> None:
    app = App(fake_platform)
    # Baseline
    r = app.dispatch(GetAutostartStatus())
    assert r.enabled is False

    app.dispatch(EnableAutostart())
    r = app.dispatch(GetAutostartStatus())
    assert r.enabled is True


def test_autostart_refresh_never_enables(fake_platform) -> None:
    """``RefreshAutostart`` re-renders an EXISTING entry — it never installs one.

    That is the entire line between it and ``EnableAutostart``, and a
    happy-path-only test would let the line move.  ``refresh()`` exists so a
    moved install picks up a new ``Exec=`` (#201); if it could enable, every
    launch would silently opt the user into autostart.
    """
    app = App(fake_platform)

    r = app.dispatch(RefreshAutostart())

    assert r.ok is True
    assert r.enabled is False, "refresh installed an entry that was not there"
    assert app.dispatch(GetAutostartStatus()).enabled is False


def test_autostart_refresh_keeps_an_existing_entry(fake_platform) -> None:
    """The other half: refreshing an installed entry leaves it installed."""
    app = App(fake_platform)
    app.dispatch(EnableAutostart())

    r = app.dispatch(RefreshAutostart())

    assert r.enabled is True
    assert app.dispatch(GetAutostartStatus()).enabled is True


def test_autostart_disable_clears_state(fake_platform) -> None:
    app = App(fake_platform)
    app.dispatch(EnableAutostart())

    app.dispatch(DisableAutostart())

    r = app.dispatch(GetAutostartStatus())
    assert r.enabled is False


# =========================================================================
# GetPaths — the diagnostic that has to name the directory the app opens
#
# Users answer "where did my theme go?" from this output and paste it into
# issues, so a generic answer for a per-SKU cooler does not merely read
# imprecisely -- it points the reporter, and us, at a directory the app never
# opens.  ``key`` is what gives the resolution-addressed query a device to ask.
# =========================================================================


def _attach_sku_device(app: App, key: str, fbl: int, pm: int, sub: int) -> None:
    """Attach a device whose handshake selects a per-SKU artwork library."""
    from types import SimpleNamespace

    from trcc.core.protocol import get_profile

    app.devices[key] = SimpleNamespace(          # type: ignore[assignment]
        profile=get_profile(fbl, pm),
        handshake=SimpleNamespace(sub_byte=sub, pm_byte=pm),
        info=SimpleNamespace(key=key,
                             native_resolution=get_profile(fbl, pm).resolution),
        is_connected=True,
    )


def test_get_paths_without_a_key_stays_generic(fake_platform) -> None:
    """No key → exactly the answer this always gave.

    ``key`` is additive: the resolution-only contract is what every existing
    caller depends on, so it must not move.
    """
    from trcc.core.commands import GetPaths

    r = App(fake_platform).dispatch(GetPaths(resolution=(1600, 720)))

    assert r.ok is True
    assert r.theme_dir is not None
    assert r.theme_dir.endswith("theme1600720")
    assert r.cloud_theme_dir is not None
    assert r.cloud_theme_dir.endswith("/1600720")
    assert r.cloud_mask_dir is not None
    assert r.cloud_mask_dir.endswith("zt1600720")


def test_get_paths_with_a_key_names_that_devices_libraries(fake_platform) -> None:
    """A SUB-3 1600x720 cooler reads ``…l`` libraries — say so.

    1600x720 ships six theme libraries picked by SUB crossed with orientation
    (FormCZTV.cs:1290-1353).  The three LIBRARY dirs follow the device; the
    three USER dirs must NOT -- the user's own art has no per-SKU split.

    MUTATION CHECK -- put ``p.theme_dir`` back in place of ``libs.theme_dir``
    in ``GetPaths.execute`` and this fails.
    """
    from trcc.core.commands import GetPaths

    app = App(fake_platform)
    key = "0416:5408"
    _attach_sku_device(app, key, fbl=114, pm=64, sub=3)
    # The variant library must exist on disk or DeviceLibraries deliberately
    # falls back to the generic one (covered separately below).
    for variant in ("l",):
        fake_platform.paths().theme_dir(1600, 720, variant).mkdir(
            parents=True, exist_ok=True)
        fake_platform.paths().cloud_theme_dir(1600, 720, variant).mkdir(
            parents=True, exist_ok=True)
        fake_platform.paths().cloud_mask_dir(1600, 720, variant).mkdir(
            parents=True, exist_ok=True)

    r = app.dispatch(GetPaths(resolution=(1600, 720), key=key))

    assert r.ok is True
    assert r.theme_dir is not None and r.theme_dir.endswith("theme1600720l")
    assert (r.cloud_theme_dir is not None
            and r.cloud_theme_dir.endswith("/1600720l"))
    assert (r.cloud_mask_dir is not None
            and r.cloud_mask_dir.endswith("zt1600720l"))
    # User content is one directory per resolution, never per SKU.
    assert r.user_theme_dir is not None and r.user_theme_dir.endswith("1600720")
    assert not r.user_theme_dir.endswith("1600720l")


def test_get_paths_key_alone_supplies_the_resolution(fake_platform) -> None:
    """``key`` with no ``resolution`` answers for that device's canvas.

    The device knows its own size; making the caller retype it is how the two
    drift apart.  Nothing is scoped without either one.
    """
    from trcc.core.commands import GetPaths

    app = App(fake_platform)
    key = "0416:5408"
    _attach_sku_device(app, key, fbl=114, pm=64, sub=3)

    unscoped = app.dispatch(GetPaths())
    assert unscoped.theme_dir is None, "no device, no resolution → nothing scoped"

    r = app.dispatch(GetPaths(key=key))
    assert r.theme_dir is not None and "1600720" in r.theme_dir


def test_get_paths_falls_back_when_the_sku_library_is_absent(
    fake_platform,
) -> None:
    """Variant dir not on disk → the generic name, not a path nobody has.

    The suffixed libraries are a separate download.  A diagnostic that named
    ``theme1600720l`` on an install where it never landed would send a reporter
    looking for a directory that does not exist.
    """
    from trcc.core.commands import GetPaths

    app = App(fake_platform)
    key = "0416:5409"
    _attach_sku_device(app, key, fbl=114, pm=64, sub=3)

    r = app.dispatch(GetPaths(resolution=(1600, 720), key=key))

    assert r.theme_dir is not None and r.theme_dir.endswith("theme1600720")


# ── AdvanceSlideshow: rotation stops being the gui's private ability ──────
#
# ``slideshow.advance`` had exactly ONE caller — the gui's QTimer — so a
# slideshow configured over the CLI or the REST API persisted, reported back
# correctly, and never rotated.  The service module docstring claimed the
# RenderAndSend ticker drove it; RenderAndSend has no slideshow code at all.


def test_advance_slideshow_reads_the_persisted_config(fake_platform) -> None:
    """No config argument — the Command takes the key and reads settings.

    The gui used to build a SlideshowConfig from its own panel, making the
    panel a second source for a fact ``ConfigureSlideshow`` already persists.
    """
    from trcc.core.commands import (
        AdvanceSlideshow,
        ConfigureSlideshow,
        SetSlideshow,
    )

    app = App(fake_platform)
    key = "0402:3922"
    # Two Commands, deliberately: ConfigureSlideshow owns the list + interval,
    # SetSlideshow owns the on/off.  The gui dispatches both, and starts its
    # timer only when enabled — so reading ``slideshow_enabled`` here matches
    # exactly when that timer would have been ticking.
    app.dispatch(ConfigureSlideshow(
        key=key, themes=("a", "b", "c"), interval_s=1.0,
    ))
    app.dispatch(SetSlideshow(key=key, enabled=True))

    first = app.dispatch(AdvanceSlideshow(key=key))

    assert first.ok is True
    assert first.due is True
    assert first.theme_name in ("a", "b", "c")


def test_advance_slideshow_says_nothing_is_running(fake_platform) -> None:
    """Unconfigured is ``ok=True, due=False`` — a normal answer for a caller
    driving a timer, not a failure it has to special-case."""
    from trcc.core.commands import AdvanceSlideshow

    app = App(fake_platform)

    result = app.dispatch(AdvanceSlideshow(key="0402:3922"))

    assert result.ok is True
    assert result.running is False, (
        "'nothing configured' must be distinguishable from 'not due yet' — a "
        "timer-driver stops for one and keeps waiting for the other"
    )
    assert result.due is False
    assert result.theme_name is None


def test_advance_slideshow_holds_inside_the_interval(fake_platform) -> None:
    """A second tick inside the window does not rotate — ``due`` distinguishes
    "not yet" from "nothing configured", which a timer-driver needs."""
    from trcc.core.commands import (
        AdvanceSlideshow,
        ConfigureSlideshow,
        SetSlideshow,
    )

    app = App(fake_platform)
    key = "0402:3922"
    app.dispatch(ConfigureSlideshow(
        key=key, themes=("a", "b"), interval_s=3600.0,
    ))
    app.dispatch(SetSlideshow(key=key, enabled=True))
    assert app.dispatch(AdvanceSlideshow(key=key)).due is True

    again = app.dispatch(AdvanceSlideshow(key=key))

    assert again.ok is True
    assert again.running is True, "the slideshow IS running, just not due"
    assert again.due is False
    assert again.theme_name is None


# ── The capture source is reachable from core, not just from a Qt panel ───
#
# ``QtScreenCapture`` existed but was constructed inline by the qtgui panel,
# so no Command could reach a capture source at all — which is why the
# screencast driver had to live in a UI.


def test_platform_supplies_a_screen_capture(fake_platform) -> None:
    """A Command can reach a capture source through ``app.platform``."""
    app = App(fake_platform)

    capture = app.platform.screen_capture()
    frame = capture.grab_region(10, 20, 4, 3)

    assert frame.width == 4
    assert frame.height == 3
    assert len(frame.data) == 4 * 3 * 3, "RGB24 = 3 bytes per pixel"
    assert fake_platform.capture.regions == [(10, 20, 4, 3)], (
        "the requested rectangle must reach the capture source unchanged"
    )


def test_screen_capture_is_memoised_per_platform() -> None:
    """Built once, then cached — the same idiom as sensors / autostart.

    A portal-backed implementation holds a session, so handing out a fresh
    instance per call would mean a new consent dialog each time.
    """
    from trcc.adapters.system.linux import LinuxOS

    os_ = LinuxOS()

    assert os_.screen_capture() is os_.screen_capture()


# ── StartScreencast refuses a device with no panel ───────────────────────
#
# It used to answer ok=True and PERSIST screencast_region on an LED
# controller — a wrong-state write, and the capture driver reads exactly
# that field to decide what to grab for.


def _led_key() -> str:
    from trcc.core.models import Kind
    from trcc.core.registry import ALL_DEVICES

    p = next(x for x in ALL_DEVICES.values() if x.kind is Kind.LED)
    return f"{p.vid:04x}:{p.pid:04x}"


def test_start_screencast_refuses_an_led_controller(fake_platform) -> None:
    """ok=False, and — the point — nothing persisted."""
    from trcc.core.commands import StartScreencast

    app = App(fake_platform)
    key = _led_key()
    app.attach(int(key[:4], 16), int(key[5:], 16))

    result = app.dispatch(StartScreencast(key=key, x=0, y=0, w=320, h=320))

    assert result.ok is False
    assert "no panel" in result.message
    assert app.settings.for_device(key).screencast_region is None, (
        "refused, but the region was written anyway — the driver reads this"
    )


def test_start_screencast_still_accepts_an_lcd(fake_platform) -> None:
    """The guard must not swallow the working case."""
    from trcc.core.commands import StartScreencast
    from trcc.core.models import Kind
    from trcc.core.registry import ALL_DEVICES

    lcd = next(p for p in ALL_DEVICES.values() if p.kind is Kind.LCD)
    key = f"{lcd.vid:04x}:{lcd.pid:04x}"
    app = App(fake_platform)
    app.attach(lcd.vid, lcd.pid)

    result = app.dispatch(StartScreencast(key=key, x=1, y=2, w=64, h=48))

    assert result.ok is True, result.message
    assert app.settings.for_device(key).screencast_region == (1, 2, 64, 48, False)


def test_list_memory_slots_maps_absent_fields_to_empty(fake_platform) -> None:
    """A field the OS did not probe arrives as ``""`` — never a guess.

    The four probes are genuinely heterogeneous: only Linux enriches with
    SPD/IMC timings, so ``tcas`` is populated there and absent on Windows,
    macOS and BSD.  ``Platform.memory_info``'s docstring makes absence mean
    "measured nothing" rather than "never asked", and this Result keeps that
    per FIELD — a UI renders "NC" for an empty one, which is exactly what the
    LC1 memory panel does.

    Asserting the EMPTY case rather than the populated one is deliberate: the
    dev box is Linux and fills every timing, so a happy-path test would pass
    while a Windows reporter silently got a fabricated zero.
    """
    fake_platform.memory_slots = [
        {"size": "16 GiB", "type": "DDR5", "speed": "4800 MT/s"},
    ]
    app = App(fake_platform)

    r = app.dispatch(ListMemorySlots())

    assert r.ok
    assert len(r.slots) == 1
    slot = r.slots[0]
    assert slot.size == "16 GiB"
    assert slot.type == "DDR5"
    assert slot.tcas == "", "an unprobed timing must be empty, not 0"
    assert slot.manufacturer == ""
