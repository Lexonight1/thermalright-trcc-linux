"""Commands — UI contract dispatched through App.dispatch."""
from __future__ import annotations

from pathlib import Path

import pytest

from trcc.app import App
from trcc.core.commands import (
    DisableAutostart,
    EnableAutostart,
    GetAutostartStatus,
    GetPlatformInfo,
    ListMemorySlots,
    ReadSensors,
    RefreshAutostart,
    SetDiskDevice,
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


def test_an_autostart_choice_is_recorded_and_survives_a_restart(
    fake_platform,
) -> None:
    """Enable AND Disable record that autostart was chosen — read back from
    disk by a second App, because a write nobody reads back proves nothing."""
    app = App(fake_platform)
    assert app.dispatch(GetAutostartStatus()).configured is False

    app.dispatch(DisableAutostart())

    assert App(fake_platform).dispatch(GetAutostartStatus()).configured is True


def test_enable_records_the_choice(fake_platform) -> None:
    app = App(fake_platform)
    assert app.dispatch(EnableAutostart()).configured is True
    assert App(fake_platform).dispatch(GetAutostartStatus()).configured is True


def test_a_rejected_enable_records_nothing(fake_platform) -> None:
    app = App(fake_platform)
    assert app.dispatch(EnableAutostart(target="nonsense")).ok is False
    assert app.dispatch(GetAutostartStatus()).configured is False


def test_autostart_refresh_keeps_an_existing_entry(fake_platform) -> None:
    """The other half: refreshing an installed entry leaves it installed."""
    app = App(fake_platform)
    app.dispatch(EnableAutostart())

    r = app.dispatch(RefreshAutostart())

    assert r.enabled is True
    assert app.dispatch(GetAutostartStatus()).enabled is True


def test_autostart_refresh_rewrites_a_stale_entry(fake_platform) -> None:
    """The repair itself — an entry written by a moved install goes current.

    Every other refresh test observes only ``enabled``, which a refresh that
    does NOTHING satisfies just as well as one that works.  Measured: with
    ``mgr.refresh()`` deleted from ``RefreshAutostart.execute``, 4266 tests
    passed — the 38 real-adapter ones included, because they call the adapter
    directly and never through the Command.  Nothing anywhere connected the
    Command to the port, and #201 is the whole reason the Command exists.
    """
    app = App(fake_platform)
    mgr = fake_platform.autostart()
    app.dispatch(EnableAutostart())

    mgr.command = "/somewhere/else/bin/trcc"        # the install moved
    assert mgr.installed_command != mgr.command, "fixture drift: not stale"

    app.dispatch(RefreshAutostart())

    assert mgr.installed_command == "/somewhere/else/bin/trcc"


def test_autostart_refresh_preserves_the_installed_target(fake_platform) -> None:
    """A repair must not change WHICH ui the user chose to start.

    ``refresh`` re-renders by re-enabling, so re-enabling with the default
    instead of the installed target would silently move every non-gui user
    back to gui.  All three adapters guard this by passing
    ``installed_target()``; this asserts the Command inherits the guarantee.
    """
    app = App(fake_platform)
    app.dispatch(EnableAutostart(target="daemon"))

    app.dispatch(RefreshAutostart())

    assert app.dispatch(GetAutostartStatus()).target == "daemon"


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
    # Held still: SetSlideshow starts the real driver, whose first tick
    # would take the "first advance" this test is about.
    from trcc.adapters.infra.send_scheduler import SyncSendScheduler
    from trcc.core.commands import (
        AdvanceSlideshow,
        ConfigureSlideshow,
        SetSlideshow,
    )
    app = App(fake_platform, send_scheduler=SyncSendScheduler())
    key = "0402:3922"
    # Two Commands, deliberately: ConfigureSlideshow owns the list + interval,
    # SetSlideshow owns the on/off -- and with it the driver that dispatches
    # this Command.
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
    # Held still: SetSlideshow starts the real driver, whose first tick
    # would take the "first advance" this test is about.
    from trcc.adapters.infra.send_scheduler import SyncSendScheduler
    from trcc.core.commands import (
        AdvanceSlideshow,
        ConfigureSlideshow,
        SetSlideshow,
    )
    app = App(fake_platform, send_scheduler=SyncSendScheduler())
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


def test_start_screencast_refuses_an_led_controller(tmp_path) -> None:
    """ok=False, and — the point — nothing persisted.

    A connectable LED: ``StartScreencast`` uses its device, so dispatch
    connects it first and the refusal comes after — as on real hardware.  A
    fake that cannot handshake would fail at the connect instead.
    """
    from tests.mock_platform import MockPlatform
    from trcc.core.commands import StartScreencast

    app = App(MockPlatform([{"type": "led", "vid": "0416", "pid": "8001",
                             "pm": 208}], tmp_path))
    key = "0416:8001"

    result = app.dispatch(StartScreencast(key=key, x=0, y=0, w=320, h=320))

    assert result.ok is False
    assert result.message == f"{key} (LED) has no frame_render capability"
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


# ── Frame Commands refuse a device that draws no frames ─────────────
#
# The LED Commands refused a non-LED in one voice (``_not_an_led``, #252); the
# frame Commands had no mirror.  Measured on the mock 2026-10-06: ``SendColor``
# at an LED rendered a 320x320 frame, had it refused at the wire, and left the
# LED in ``App.held`` — its animation and sensor refresh both stopped until an
# LED setting changed.


def _frame_commands(tmp_path):
    """One of every Command whose whole job is a frame, aimed at the LED."""
    from trcc.core.commands import (
        BuildPreview,
        PlayVideo,
        RenderAndSend,
        SendColor,
        SendFrame,
        SendImage,
        SendScreencastFrame,
        SetScreencastRegion,
    )
    from trcc.core.models import RawFrame

    key = _led_key()
    image = tmp_path / "x.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")
    video = tmp_path / "x.mp4"
    video.write_bytes(b"\0")
    return [
        SendFrame(key=key, data=b"\0" * 64),
        SendColor(key=key, r=255, g=0, b=0),
        SendImage(key=key, path=image),
        RenderAndSend(key=key),
        BuildPreview(key=key),
        PlayVideo(key=key, path=video),
        SetScreencastRegion(key=key, x=0, y=0, w=64, h=48),
        SendScreencastFrame(key=key, frame=RawFrame(data=b"\0" * 12,
                                                    width=2, height=2)),
    ]


@pytest.mark.parametrize("index", range(8))
def test_a_frame_command_refuses_an_led_and_leaves_it_running(
    tmp_path, index: int,
) -> None:
    """Refused by capability, and — the point — the LED is not held.

    MUTATION CHECK: delete the ``_lacks`` guard from any one of these Commands
    and its row fails.
    """
    from tests.mock_platform import MockPlatform
    from trcc.adapters.infra.send_scheduler import SyncSendScheduler
    from trcc.adapters.render.qt import QtRenderer
    from trcc.core.commands import ConnectDevice

    key = _led_key()
    # A real renderer and sender, as in the App: without them an unguarded
    # Command dies on "no renderer" before it can hold anything.
    app = App(MockPlatform([{"type": "led", "vid": key[:4], "pid": key[5:],
                             "pm": 16}], tmp_path),
              renderer=QtRenderer(), send_scheduler=SyncSendScheduler())
    try:
        assert app.dispatch(ConnectDevice(key=key)).ok
        cmd = _frame_commands(tmp_path)[index]

        result = app.dispatch(cmd)

        assert key not in app.held, (
            f"{type(cmd).__name__} held the LED — its animation and sensor "
            "refresh stop until an LED setting changes"
        )
        assert result.ok is False
        assert result.message == f"{key} (LED) has no frame_render capability"
    finally:
        app.close()


def test_lacks_answers_from_the_registry_when_nothing_is_attached(
    fake_platform,
) -> None:
    """The ternary's other two arms: registry, and "cannot tell" is allowed."""
    from trcc.core.commands._helpers import _lacks
    from trcc.core.models import Capability

    app = App(fake_platform)
    key = _led_key()
    assert key not in app.devices

    assert _lacks(app, key, Capability.FRAME_RENDER) == (
        f"{key} (LED) has no frame_render capability")
    assert _lacks(app, key, Capability.EFFECTS) is None
    assert _lacks(app, "ffff:ffff", Capability.FRAME_RENDER) is None
    assert _lacks(app, "not-a-key", Capability.FRAME_RENDER) is None


def test_a_device_that_draws_no_frames_is_neither_primed_nor_rendered(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The shape of a self-rendering device: not an LED, and no frames.

    ``_prime`` and the render observer used to read "not an LED" as "a frame
    LCD", so such a device would have been restored and rendered to.  An LCD
    with ``FRAME_RENDER`` taken out of its kind's set stands in for it; the
    control half proves the same App DOES prime and render with it in.

    MUTATION CHECK: drop the ``FRAME_RENDER`` test from ``_prime`` or from
    ``_DeviceRenderObserver``'s per-key loop and this fails.  The loop is the
    only gate the observer needs: the sensor sweep's keys go through it too.
    """
    from tests.mock_platform import MockPlatform
    from trcc.adapters.render.qt import QtRenderer
    from trcc.core.commands import (
        ConnectDevice,
        RenderAndSend,
        RestoreDeviceState,
    )
    from trcc.core.events import OverlayChanged, SensorsUpdated
    from trcc.core.models import (
        CAPABILITIES_BY_KIND,
        Capability,
        Kind,
        Theme,
    )

    key = "0402:3922"
    # A renderer, or the observer returns before it reads a single device.
    app = App(MockPlatform([{"type": "lcd", "vid": "0402", "pid": "3922",
                             "fbl": 100}], tmp_path), renderer=QtRenderer())
    try:
        assert app.dispatch(ConnectDevice(key=key)).ok
        rendered: list[str] = []
        restored: list[str] = []
        real_dispatch = app.dispatch

        def recording_dispatch(cmd):
            for kind, seen in ((RestoreDeviceState, restored),
                               (RenderAndSend, rendered)):
                if isinstance(cmd, kind):
                    seen.append(cmd.key)
                    return None
            return real_dispatch(cmd)

        monkeypatch.setattr(app, "dispatch", recording_dispatch)

        def drive() -> None:
            app.active_themes.pop(key, None)
            app._prime(key)
            app.active_themes[key] = Theme(name="T", path=Path("/nonexistent"),
                                           resolution=(320, 320))
            app.events.publish(SensorsUpdated(readings={}, metrics=None))
            app.events.publish(OverlayChanged(key=key, enabled=True))

        drive()
        assert (restored, rendered) == ([key], [key, key]), "control"

        restored.clear()
        rendered.clear()
        monkeypatch.setitem(
            CAPABILITIES_BY_KIND, Kind.LCD,
            CAPABILITIES_BY_KIND[Kind.LCD] - {Capability.FRAME_RENDER})
        drive()
        assert (restored, rendered) == ([], [])
    finally:
        app.close()


def test_keepalive_says_why_the_device_is_missing(fake_platform) -> None:
    """A failed connect leaves no cached frame either, and the cache was
    checked FIRST — so ``keepalive`` answered "No cached frame" and hid the
    reason, which for hardware we do not own is the whole diagnosis."""
    from trcc.core.commands import KeepAliveLoop

    fake_platform.scsi.open = lambda: False
    app = App(fake_platform)

    result = app.dispatch(KeepAliveLoop(key="0402:3922", count=1))

    assert result.ok is False
    assert result.message == ("Not attached: 0402:3922 — Failed to open scsi "
                              "transport for 0402:3922")


def _keepalive_app(fake_platform) -> App:
    from trcc.adapters.render.qt import QtRenderer
    return App(fake_platform, renderer=QtRenderer())


def test_keepalive_keeps_a_pushed_frame(fake_platform) -> None:
    """``trcc display keepalive`` after ``display color`` threw the colour away
    in daemon mode: the CLI restored the saved theme first, unconditionally
    (#267).  A panel with a frame to resend keeps it -- and its hold.
    MUTATION CHECK: restore in KeepAliveLoop whatever is cached → this fails.
    """
    from trcc.core.commands import KeepAliveLoop, SendColor

    app = _keepalive_app(fake_platform)
    assert app.dispatch(SendColor(key="0402:3922", r=255, g=0, b=0)).ok
    pushed = app.senders["0402:3922"].last()

    result = app.dispatch(KeepAliveLoop(key="0402:3922", count=1))

    assert result.ok, result.message
    assert "0402:3922" in app.held
    assert app.senders["0402:3922"].last() == pushed


def test_keepalive_shows_the_saved_display_when_nothing_is_cached(
        fake_platform) -> None:
    """A fresh process has nothing to resend: the saved display (here the
    Theme1 fallback) is shown once and becomes the frame.
    MUTATION CHECK: drop the restore from KeepAliveLoop → this fails."""
    from tests.conftest import renderable_theme
    from trcc.core.commands import KeepAliveLoop

    app = _keepalive_app(fake_platform)
    renderable_theme(app.platform.paths().theme_dir(320, 320), "Theme1")

    result = app.dispatch(KeepAliveLoop(key="0402:3922", count=1))

    assert result.ok, result.message
    assert app.active_themes["0402:3922"].name == "Theme1"
    assert app.senders["0402:3922"].last() is not None


def test_keepalive_with_nothing_installed_says_so(fake_platform) -> None:
    from trcc.core.commands import KeepAliveLoop

    result = _keepalive_app(fake_platform).dispatch(
        KeepAliveLoop(key="0402:3922", count=1))

    assert (result.ok, result.message) == (
        False, "No theme available for this device — install themes or load "
               "a theme first")


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


def test_set_disk_device_pushes_the_choice_into_the_live_enumerator(
    fake_platform,
) -> None:
    """The TWO-HOP: persist the choice AND re-route the metric.

    This is the whole bug ``disk_index`` had. It was written by the gui, the
    CLI and the API, saved to config.json, and reported back correctly by
    ``LedSnapshotResult`` — and the metric ignored it, because nothing ever
    told the live enumerator. ``SetGpuDevice``'s own comment names the same
    failure: "Without this the selection only persisted and primary_gpu()
    ignored it."

    Asserting BOTH halves is the point. Checking only ``settings`` would pass
    against the exact defect this Command exists to avoid.
    """
    app = App(fake_platform)
    sensors = app.platform.sensors()

    app.dispatch(SetDiskDevice(disk_key="hwmon:nvme:SERIAL_AAA:temp1"))

    assert app.settings.app.active_disk == "hwmon:nvme:SERIAL_AAA:temp1"
    assert sensors._preferred_disk_key == "hwmon:nvme:SERIAL_AAA:temp1", (
        "the choice never reached the live enumerator — the metric will keep "
        "showing the hottest drive, which is the bug this Command replaces"
    )

    app.dispatch(SetDiskDevice(disk_key=""))

    assert app.settings.app.active_disk is None
    assert sensors._preferred_disk_key is None


def test_boot_seeds_the_disk_preference_from_settings(fake_platform) -> None:
    """A choice made last run must apply on the next launch.

    The composition root seeds the enumerator from ``settings.active_disk``,
    beside the identical GPU line. Without it the setting persists and does
    nothing until the user picks again.
    """
    app = App(fake_platform)
    app.dispatch(SetDiskDevice(disk_key="hwmon:nvme:SERIAL_AAA:temp1"))

    # The platform (and therefore its sensors) is a singleton the fixture
    # shares, so the live preference must be CLEARED first — otherwise this
    # passes on the value the dispatch above already pushed, and would keep
    # passing with the seed deleted.  Verified by mutation: without this line
    # the test is vacuous.
    fake_platform.sensors()._preferred_disk_key = None

    reborn = App(fake_platform)          # a fresh App over the same settings

    assert reborn.platform.sensors()._preferred_disk_key == (
        "hwmon:nvme:SERIAL_AAA:temp1"
    ), "the persisted choice was not re-applied at boot"


# =========================================================================
# EnableAutostart — WHICH ui starts with the computer
# =========================================================================


def test_enable_autostart_records_the_chosen_target(fake_platform) -> None:
    app = App(fake_platform)

    result = app.dispatch(EnableAutostart(target="daemon"))

    assert result.ok, result.message
    assert result.target == "daemon"
    assert app.dispatch(GetAutostartStatus()).target == "daemon", (
        "status did not report the target that was installed"
    )


def test_enable_autostart_defaults_when_no_target_is_given(fake_platform) -> None:
    app = App(fake_platform)

    result = app.dispatch(EnableAutostart())

    assert result.ok
    assert result.target == "gui"


def test_enable_autostart_refuses_an_unknown_target(fake_platform) -> None:
    """A KeyError from ``autostart_argv`` is not an answer; a Result is.

    Validation lives in the Command precisely because the adapter raises.
    """
    app = App(fake_platform)

    result = app.dispatch(EnableAutostart(target="nonesuch"))

    assert not result.ok
    assert "nonesuch" in result.message
    assert "daemon" in result.message, (
        "the refusal must name the valid set, or the user cannot act on it"
    )
    assert not app.dispatch(GetAutostartStatus()).enabled, (
        "a rejected target must not have enabled anything"
    )


def test_autostart_status_reports_where_the_entry_lives(fake_platform) -> None:
    """``entry_location`` is declared on the port, not duck-typed.

    ``_autostart_path`` used to do ``getattr(mgr, "path", "")`` — an attribute
    only the XDG adapter had — so the field a reporter pastes was EMPTY on
    Windows and macOS, the two platforms we cannot reproduce on and therefore
    depend on the reporter for.
    """
    assert App(fake_platform).dispatch(GetAutostartStatus()).path != ""


def test_autostart_result_round_trips_the_new_field() -> None:
    """A serializer is a PAIR — gate the round trip, not the write half."""
    from trcc import ipc
    from trcc.core.results import AutostartResult

    original = AutostartResult(ok=True, message="enabled (daemon)",
                               enabled=True, path="/x/trcc.desktop",
                               target="daemon")

    back = ipc.decode_result(ipc.encode_result(original))

    assert back == original
    assert back.target == "daemon", "target was dropped crossing the wire"


def test_render_dc_standalone_sees_only_readings_that_exist(
    fake_platform, monkeypatch, tmp_path,
) -> None:
    """The standalone render must source sensors the way every other one does.

    Five render sites (``RenderAndSend``, ``SendFrame``, ``LoadTheme``,
    ``MetricsLoop``, the LED effects) read from ``read_all()``, which OMITS a
    key with no reading so ``_draw_metric`` skips it and warns.  This one read
    from ``discover()``, whose ``SensorReading.value`` is a plain ``float`` and
    so coalesces a missing reading to ``0.0`` — the same theme on the same host
    drew ``0`` here and nothing there.
    """
    from PySide6.QtGui import QImage

    from trcc.core.commands import RenderDcStandalone
    from trcc.services.overlay import OverlayService

    captured: dict[str, dict[str, float]] = {}

    def _capture(*, renderer, dc_path, width, height, sensors=None, **kw):
        captured["sensors"] = dict(sensors or {})
        return QImage(width, height, QImage.Format.Format_RGB32), 0, {}

    monkeypatch.setattr(OverlayService, "render_dc_standalone", _capture)

    from trcc.adapters.render.qt import QtRenderer

    app = App(fake_platform)
    app.set_renderer(QtRenderer())
    enum = app.platform.sensors()
    coalesced = {r.sensor_id for r in enum.discover()} - set(enum.read_all())
    assert coalesced, (
        "fixture must advertise at least one key with no current reading, "
        "or this proves nothing"
    )

    dc = tmp_path / "config1.dc"
    dc.write_bytes(b"")
    app.dispatch(RenderDcStandalone(
        dc_path=dc, output_path=tmp_path / "out.png", width=320, height=320,
    ))

    assert not (coalesced & set(captured["sensors"])), (
        "a metric with no reading reached the renderer as 0.0 and will be "
        f"DRAWN as 0: {sorted(coalesced & set(captured['sensors']))}"
    )


def test_read_sensors_declares_the_unit_its_values_are_in(fake_platform) -> None:
    """°F values must arrive labelled °F -- and °C values labelled °C.

    ``personalize_readings`` converts the VALUE of every ``:temp`` key; the
    SYMBOL beside it is a separate decision, and it was expressed once, inline,
    with no test anywhere asserting it.  A consumer that renders ``unit``
    verbatim -- every sensor list in the app -- shows "122.0 °C" the moment the
    two disagree.

    Both directions on purpose: a one-directional rule is one nobody re-reads,
    and a catalog cached under °F has to come back to °C when the user does.

    MUTATION CHECK: make ``personalize_unit`` return ``unit`` unchanged and the
    Fahrenheit half fails, naming the reading that lied about its unit.
    """
    app = App(fake_platform)

    app.settings.set_global_temp_unit("F")
    by_id = {r.sensor_id: r for r in app.dispatch(ReadSensors()).readings}
    assert by_id["cpu:temp"].value == 107.6, "42°C is 107.6°F"
    assert by_id["cpu:temp"].unit == "°F"
    # A non-temperature reading keeps its own unit whatever the pref says.
    assert by_id["cpu:usage"].unit == "%"

    app.settings.set_global_temp_unit("C")
    by_id = {r.sensor_id: r for r in app.dispatch(ReadSensors()).readings}
    assert by_id["cpu:temp"].value == 42.0
    assert by_id["cpu:temp"].unit == "°C"



def test_split_mode_starts_at_style_2_and_a_chosen_off_survives_a_restart(
    fake_platform,
) -> None:
    """The C# starts at style 2 (``FormLCD.cs:232``).  The App used to start at
    0, and only the gui made it 2 -- by writing it on open.  0 stays "off"."""
    from trcc.core.commands import SetSplitMode

    key = "87ad:70db"
    assert App(fake_platform).settings.for_device(key).split_mode == 2

    App(fake_platform).dispatch(SetSplitMode(key=key, mode=0))

    assert App(fake_platform).settings.for_device(key).split_mode == 0


# ── App-wide settings announce themselves (2026-10-02) ──────────────────────

def test_each_app_wide_setting_announces_its_change(fake_platform) -> None:
    """Disk, dashboard and autostart changed in silence, so a window showed
    only what IT had last sent -- a change from the CLI, the API or the other
    window reached no open UI until it was reopened.

    MUTATION CHECK -- MEASURED 2026-10-02: drop any one publish → fails.
    """
    from trcc.core.commands import (
        DisableAutostart,
        EnableAutostart,
        SetDiskDevice,
        SetSensorDashboard,
    )
    from trcc.core.events import (
        AutostartChanged,
        DiskDeviceChanged,
        SensorDashboardChanged,
    )
    from trcc.core.models import PanelConfig

    app = App(platform=fake_platform)
    seen: list[object] = []
    for kind in (AutostartChanged, DiskDeviceChanged, SensorDashboardChanged):
        app.events.subscribe(kind, seen.append)  # type: ignore[arg-type]

    for command in (SetDiskDevice(disk_key="nvme1"), SetDiskDevice(disk_key=""),
                    SetSensorDashboard(panels=(PanelConfig.custom(),)),
                    SetSensorDashboard(panels=()),          # refused: no event
                    EnableAutostart(), DisableAutostart()):
        app.dispatch(command)

    assert seen == [
        DiskDeviceChanged(disk_key="nvme1"), DiskDeviceChanged(disk_key=None),
        SensorDashboardChanged(panels=1),
        AutostartChanged(enabled=True), AutostartChanged(enabled=False),
    ]


def test_run_upgrade_runs_nothing_and_says_what_to_run(fake_platform, monkeypatch) -> None:
    """It spawned a root package-manager upgrade (through the API too, from an
    HTTP POST); it now answers with the command for this install, and runs
    nothing.  MUTATION CHECK: subprocess.run again -> the sentinel raises."""
    import subprocess

    from trcc.app import App
    from trcc.core.commands import RunUpgrade

    def no_spawn(*a, **k):
        raise AssertionError("RunUpgrade spawned a process")

    monkeypatch.setattr(subprocess, "run", no_spawn)
    monkeypatch.setattr(fake_platform, "upgrade_hint", lambda: "pipx upgrade trcc-linux")

    result = App(fake_platform).dispatch(RunUpgrade())

    assert result.ok
    assert "pipx upgrade trcc-linux" in result.message
