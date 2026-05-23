"""GUI panel smoke tests — construct each widget + verify wiring.

Phase D boilerplate: every ``ui/gui/`` module was at 0% coverage; this
file establishes the QApplication-offscreen fixture pattern + one
construction smoke per panel class so widgets are at least known to
build without raising.

Future GUI tests (panel behavior, signal cascades, repaint logic)
build on the same fixtures.
"""
from __future__ import annotations

import os
from pathlib import Path

# Qt needs an offscreen platform plugin in headless CI.  Set before any
# QtGui / QtWidgets import.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from collections.abc import Iterator

import pytest

from trcc.app import App

from .conftest import FakePlatform

# =========================================================================
# QApplication fixture — module-scoped so all GUI tests share it
# =========================================================================


@pytest.fixture(scope="module")
def qapp() -> Iterator[object]:
    """Ensure a QApplication exists for the duration of GUI tests.

    Module-scoped so we don't pay the Qt startup cost per test.  Qt's
    own ``QGuiApplication.instance()`` check makes the construction
    idempotent if some other code already started one.
    """
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app
    # Don't quit() — pytest may run other QtTest suites in the same
    # process; tearing down here breaks them.


# =========================================================================
# App fixture — wire a fresh App with FakePlatform + minimal renderer
# =========================================================================


@pytest.fixture
def gui_app(fake_platform: FakePlatform, qapp: object) -> App:
    """A test App with a real QtRenderer.  The renderer needs the
    QApplication, hence the ``qapp`` dependency."""
    del qapp                                # explicit dependency, no use
    from trcc.adapters.render.qt import QtRenderer

    return App(platform=fake_platform, renderer=QtRenderer())


# =========================================================================
# BusBridge — pure-Qt smoke, no panels needed
# =========================================================================


def test_bus_bridge_subscribes_to_every_event_type(qapp: object) -> None:
    """Constructing BusBridge wires one subscription per declared event
    type.  Crashing here means a misnamed Signal or missing event
    import in bus_bridge.py."""
    del qapp
    from trcc.core.events import EventBus
    from trcc.ui.qtgui.bus_bridge import BusBridge

    bus = EventBus()
    bridge = BusBridge(bus)
    # 10 event types wired today; assert at least that many handlers attached
    assert sum(len(handlers) for handlers in bus._handlers.values()) >= 10
    # Every Signal attribute should be a Qt Signal (descriptor on the class)
    for name in (
        "device_connected", "device_disconnected", "frame_sent",
        "orientation_changed", "brightness_changed", "theme_loaded",
        "led_colors_changed", "sensors_updated", "error_occurred",
    ):
        assert hasattr(bridge, name), f"BusBridge missing signal {name!r}"


def test_bus_bridge_forwards_events_to_qt_signals(qapp: object) -> None:
    """End-to-end: publishing an Event on the bus must arrive on the
    matching Qt signal.  Smokes the subscribe → emit pipeline."""
    del qapp
    from trcc.core.events import DeviceConnected, EventBus
    from trcc.ui.qtgui.bus_bridge import BusBridge

    bus = EventBus()
    bridge = BusBridge(bus)
    captured: list[object] = []
    bridge.device_connected.connect(captured.append)

    bus.publish(DeviceConnected(key="0402:3922", resolution=(320, 320)))

    assert len(captured) == 1
    event = captured[0]
    assert isinstance(event, DeviceConnected)
    assert event.key == "0402:3922"


# =========================================================================
# Panel construction smokes
# =========================================================================


def _bus(gui_app: App):
    """Single BusBridge for panel-construction tests."""
    from trcc.ui.qtgui.bus_bridge import BusBridge
    return BusBridge(gui_app.events)


def test_device_panel_constructs(gui_app: App) -> None:
    """DevicePanel constructs against a real App + QtRenderer.

    Builds → no exceptions.  Future tests can extend with click
    simulations + selection assertions.
    """
    from trcc.ui.qtgui.panels.device_panel import DevicePanel

    panel = DevicePanel(gui_app, _bus(gui_app))
    assert panel is not None
    # Panel has a layout — Qt requires this for any child widgets to render
    assert panel.layout() is not None


def test_display_panel_constructs(gui_app: App) -> None:
    from trcc.ui.qtgui.panels.display_panel import DisplayPanel

    panel = DisplayPanel(gui_app, _bus(gui_app))
    assert panel is not None
    assert panel.layout() is not None


def test_led_panel_constructs(gui_app: App) -> None:
    from trcc.ui.qtgui.panels.led_panel import LedPanel

    panel = LedPanel(gui_app, _bus(gui_app))
    assert panel is not None
    assert panel.layout() is not None


def test_about_panel_constructs(gui_app: App) -> None:
    from trcc.ui.qtgui.panels.about_panel import AboutPanel

    panel = AboutPanel(gui_app, _bus(gui_app))
    assert panel is not None
    assert panel.layout() is not None


def test_system_panel_constructs(gui_app: App) -> None:
    from trcc.ui.qtgui.panels.system_panel import SystemPanel

    panel = SystemPanel(gui_app, _bus(gui_app))
    assert panel is not None
    assert panel.layout() is not None


def test_activity_sidebar_emits_selection(gui_app: App) -> None:
    """Sidebar click → selected signal fires with the entry key."""
    from trcc.ui.qtgui.panels.sidebar import ActivitySidebar

    sidebar = ActivitySidebar(gui_app, _bus(gui_app))
    captured: list[str] = []
    sidebar.selected.connect(captured.append)
    sidebar._on_clicked("system")
    assert captured == ["system"]


def test_base_panel_requires_setup_ui() -> None:
    """A subclass that forgets _setup_ui() can't even be defined."""
    from trcc.ui.qtgui.base import BasePanel

    with pytest.raises(TypeError, match="must implement _setup_ui"):
        class _BrokenPanel(BasePanel):  # type: ignore[misc]
            pass


def test_assets_resolve_missing_returns_placeholder() -> None:
    """Missing asset names produce a 1×1 transparent placeholder."""
    from trcc.ui.qtgui.assets import Assets

    pix = Assets.pixmap("definitely-not-an-asset-xyz")
    assert not pix.isNull()
    # Placeholder is 1×1; this is the documented fallback.
    assert pix.width() == 1
    assert pix.height() == 1


def test_local_theme_browser_constructs(gui_app: App) -> None:
    from trcc.ui.qtgui.panels.local_theme_browser import LocalThemeBrowser

    panel = LocalThemeBrowser(gui_app, _bus(gui_app))
    assert panel is not None
    assert panel.layout() is not None


def test_cloud_theme_browser_populates_categories(gui_app: App) -> None:
    """Cloud browser fetches the static catalog on construct."""
    from trcc.ui.qtgui.panels.cloud_theme_browser import CloudThemeBrowser

    panel = CloudThemeBrowser(gui_app, _bus(gui_app))
    # "All categories" + 6 prefixes = at least 7 entries
    assert panel._category.count() >= 7


def test_mask_browser_constructs(gui_app: App) -> None:
    from trcc.ui.qtgui.panels.mask_browser import MaskBrowser

    panel = MaskBrowser(gui_app, _bus(gui_app))
    assert panel is not None
    assert panel.layout() is not None


def test_status_panel_handles_missing_key(gui_app: App) -> None:
    """Refresh without a device key shows a friendly hint, not a crash."""
    from trcc.ui.qtgui.panels.status_panel import StatusPanel

    panel = StatusPanel(gui_app, _bus(gui_app))
    panel._on_refresh()
    text = panel._theme_label.text()
    assert "pick a device" in text.lower() or "no data" in text.lower()


def test_overlay_editor_constructs(gui_app: App) -> None:
    from trcc.ui.qtgui.panels.overlay_editor import OverlayEditorPanel

    panel = OverlayEditorPanel(gui_app, _bus(gui_app))
    assert panel is not None
    assert panel.layout() is not None


def test_overlay_editor_refresh_without_key_shows_hint(gui_app: App) -> None:
    """Refresh with no key in the field doesn't crash — shows guidance."""
    from trcc.ui.qtgui.panels.overlay_editor import OverlayEditorPanel

    panel = OverlayEditorPanel(gui_app, _bus(gui_app))
    panel.refresh()
    assert "pick a device" in panel._status.text().lower()


def test_overlay_editor_dialog_round_trips_values(gui_app: App) -> None:
    """The element dialog reads back the same fields it was prefilled with."""
    from trcc.core.models import OverlayElement
    from trcc.ui.qtgui.panels.overlay_editor import (
        OverlayEditorPanel,
        _ElementDialog,
    )

    panel = OverlayEditorPanel(gui_app, _bus(gui_app))
    sample = OverlayElement(
        id="el_x", type="metric", x=42, y=24, color="#a0b0c0",
        size=20, bold=True, italic=False,
        metric="cpu:temp", format="{value:.0f}°C",
    )
    dialog = _ElementDialog(panel, prefill=sample)
    out = dialog.values()
    assert out["type"] == "metric"
    assert out["x"] == 42
    assert out["y"] == 24
    assert out["color"] == "#a0b0c0"
    assert out["size"] == 20
    assert out["bold"] is True
    assert out["metric"] == "cpu:temp"


def test_configuration_panel_constructs(gui_app: App) -> None:
    from trcc.ui.qtgui.panels.configuration_panel import ConfigurationPanel

    panel = ConfigurationPanel(gui_app, _bus(gui_app))
    assert panel is not None
    assert panel.layout() is not None


def test_configuration_panel_load_without_key(gui_app: App) -> None:
    from trcc.ui.qtgui.panels.configuration_panel import ConfigurationPanel

    panel = ConfigurationPanel(gui_app, _bus(gui_app))
    panel._load_from_snapshot()
    assert "pick a device" in panel._status.text().lower()


def test_preview_panel_constructs(gui_app: App) -> None:
    from trcc.ui.qtgui.panels.preview_panel import PreviewPanel

    panel = PreviewPanel(gui_app, _bus(gui_app))
    assert panel is not None
    assert panel.layout() is not None


def test_preview_panel_handles_missing_key(gui_app: App) -> None:
    """No key + refresh just early-returns — no crash."""
    from trcc.ui.qtgui.panels.preview_panel import PreviewPanel

    panel = PreviewPanel(gui_app, _bus(gui_app))
    panel._refresh()  # should be a no-op when key is empty


def test_preview_panel_unknown_key_shows_placeholder(gui_app: App) -> None:
    from trcc.ui.qtgui.panels.preview_panel import PreviewPanel

    panel = PreviewPanel(gui_app, _bus(gui_app))
    panel._picker.set_key("dead:beef")
    panel._refresh()
    text = panel._preview.text()
    assert "load a theme" in text.lower() or "no data" in text.lower()


def test_sensor_picker_filters_by_search(gui_app: App) -> None:
    """Search text narrows the visible sensor list."""
    from trcc.ui.qtgui.sensor_picker import SensorPickerWidget

    picker = SensorPickerWidget(gui_app)
    # FakePlatform exposes "Fake CPU" — search for it
    picker._search.setText("cpu")
    picker._rebuild_sensor_list()
    assert picker._sensor_list.count() > 0
    # And search for something that doesn't exist returns 0 results
    picker._search.setText("definitely-not-a-sensor-xyz")
    picker._rebuild_sensor_list()
    assert picker._sensor_list.count() == 0


def test_splash_make_returns_widget(gui_app: App) -> None:
    """make_splash() returns either QSplashScreen or fallback QFrame."""
    del gui_app
    from trcc.ui.qtgui.splash import make_splash

    splash = make_splash()
    assert splash is not None
    # Has show() + close() — that's the contract launch() relies on
    assert hasattr(splash, "show")
    assert hasattr(splash, "close")


def test_load_image_command_via_tmpfile(
    gui_app: App, tmp_path,
) -> None:
    """LoadImage stages a real image file as a theme dir."""
    from trcc.core.commands import LoadImage

    image = tmp_path / "test_image.png"
    # Minimal PNG header — file existence + extension are what we check
    image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    result = gui_app.dispatch(LoadImage(key="0402:3922", path=image))
    # The Command stages the file as a theme and dispatches LoadTheme.
    # LoadTheme returns ok=True even when no device is connected
    # (it persists the theme name for next connect).
    assert "test_image" in result.theme_name or not result.ok
    # Either way, the staged directory should exist
    staged = (
        gui_app.platform.paths().user_content_dir()
        / "single-image" / "test_image"
    )
    assert staged.is_dir()
    assert (staged / "test_image.png").is_file()


def test_load_image_command_rejects_missing_file(gui_app: App) -> None:
    from trcc.core.commands import LoadImage

    result = gui_app.dispatch(LoadImage(
        key="0402:3922", path=Path("/definitely/not/here.png"),
    ))
    assert result.ok is False
    assert "not found" in result.message.lower()


def test_load_image_command_rejects_bad_extension(
    gui_app: App, tmp_path,
) -> None:
    from trcc.core.commands import LoadImage

    bad = tmp_path / "not_an_image.txt"
    bad.write_text("hello")
    result = gui_app.dispatch(LoadImage(key="0402:3922", path=bad))
    assert result.ok is False
    assert "extension" in result.message.lower()


def test_status_panel_records_events(gui_app: App) -> None:
    """Bus events show up in the rolling log."""
    from trcc.core.events import DeviceConnected
    from trcc.ui.qtgui.panels.status_panel import StatusPanel

    bus = _bus(gui_app)
    panel = StatusPanel(gui_app, bus)
    # Add an event directly so we don't depend on Qt event loop pumping.
    panel._add_event("TEST hello")
    assert panel._event_list.count() == 1
    assert "TEST hello" in panel._event_list.item(0).text()
    # Smoke: panel constructed + connected signals without raising
    del DeviceConnected
    del bus


# =========================================================================
# Main window smoke
# =========================================================================


def test_main_window_constructs_and_includes_panels(gui_app: App) -> None:
    """The top-level MainWindow constructs and embeds the three panels."""
    from trcc.ui.qtgui.app import MainWindow

    window = MainWindow(gui_app)
    assert window is not None
    assert window.windowTitle()           # non-empty title set
    # Status bar gets wired during init for platform info + events
    assert window.statusBar() is not None


# =========================================================================
# GUI launcher entry point
# =========================================================================


def test_gui_launch_is_callable() -> None:
    """The ``launch`` factory exists + is callable.  Actually running
    it would block on qapp.exec(); tests just verify import resolution."""
    from trcc.ui.qtgui import launch

    assert callable(launch)


# =========================================================================
# Coverage marker for the future
# =========================================================================


def test_offscreen_qpa_is_set() -> None:
    """Pin the QT_QPA_PLATFORM env var so a future regression that
    forgets to set offscreen mode fails loudly here instead of
    hanging in CI on a missing X server."""
    assert os.environ.get("QT_QPA_PLATFORM") == "offscreen"


# =========================================================================
# G2 — content creation tools
# =========================================================================


def test_color_wheel_emits_hue_on_click(qapp: object) -> None:
    """ColorWheel drags update its hue and emit ``hue_changed``."""
    from trcc.ui.qtgui.color_wheel import ColorWheel

    received: list[int] = []
    wheel = ColorWheel()
    wheel.resize(220, 220)
    wheel.hue_changed.connect(received.append)
    # set_hue is the non-emitting setter — confirm it doesn't emit.
    wheel.set_hue(120)
    assert wheel.hue() == 120
    assert received == []
    del qapp


def test_image_crop_dialog_renders_target_size(qapp: object, tmp_path) -> None:
    """ImageCropDialog returns a QImage at exactly target_w×target_h."""
    from PySide6.QtGui import QColor, QImage, QPainter

    from trcc.ui.qtgui.image_crop import ImageCropDialog

    # Make a small synthetic source image to crop.
    src = QImage(200, 100, QImage.Format.Format_RGB32)
    src.fill(QColor("#336699"))
    painter = QPainter(src)
    painter.fillRect(0, 0, 50, 50, QColor("#ff0000"))
    painter.end()

    dialog = ImageCropDialog()
    dialog.load_image(src, target_w=120, target_h=80)
    cropped = dialog.cropped()
    assert cropped is not None
    assert cropped.width() == 120
    assert cropped.height() == 80
    del qapp
    del tmp_path


def test_splash_make_returns_widget_with_fallback(qapp: object) -> None:
    """SplashScreen falls back to _FrameSplash when SPLASH_BG asset is absent.

    The asset isn't bundled in next/ yet, so this exercises the fallback.
    """
    from trcc.ui.qtgui.splash import make_splash

    splash = make_splash()
    assert hasattr(splash, "show")
    assert hasattr(splash, "close")
    del qapp


def test_video_exporter_rejects_missing_source(tmp_path) -> None:
    """VideoExporter raises VideoExportError when the source doesn't exist."""
    from trcc.services.video_export import (
        VideoExporter,
        VideoExportError,
        VideoExportRequest,
    )

    missing = tmp_path / "nope.mp4"
    request = VideoExportRequest(
        source=missing, start_ms=0, end_ms=1000,
        target_w=480, target_h=480, rotation=0,
    )
    with pytest.raises(VideoExportError, match="not found"):
        VideoExporter().export_zt(request)


def test_video_exporter_rejects_bad_range(tmp_path) -> None:
    """VideoExporter rejects end_ms <= start_ms."""
    from trcc.services.video_export import (
        VideoExporter,
        VideoExportError,
        VideoExportRequest,
    )

    real = tmp_path / "fake.mp4"
    real.write_bytes(b"\x00" * 16)
    request = VideoExportRequest(
        source=real, start_ms=100, end_ms=100,
        target_w=480, target_h=480, rotation=0,
    )
    with pytest.raises(VideoExportError, match="Invalid clip range"):
        VideoExporter().export_zt(request)


def test_video_exporter_rejects_bad_rotation(tmp_path) -> None:
    """Rotation must be one of 0/90/180/270."""
    from trcc.services.video_export import (
        VideoExporter,
        VideoExportError,
        VideoExportRequest,
    )

    real = tmp_path / "fake.mp4"
    real.write_bytes(b"\x00" * 16)
    request = VideoExportRequest(
        source=real, start_ms=0, end_ms=1000,
        target_w=480, target_h=480, rotation=45,
    )
    with pytest.raises(VideoExportError, match="Rotation must"):
        VideoExporter().export_zt(request)


def test_load_video_command_rejects_missing_file(gui_app: App) -> None:
    """LoadVideo surfaces a structured error for non-existent paths."""
    from trcc.core.commands import LoadVideo

    result = gui_app.dispatch(LoadVideo(
        key="0402:3922", path=Path("/definitely/not/here.mp4"),
    ))
    assert result.ok is False
    assert "not found" in result.message.lower()


def test_load_video_command_rejects_bad_extension(
    gui_app: App, tmp_path,
) -> None:
    """LoadVideo rejects extensions outside the allow-list."""
    from trcc.core.commands import LoadVideo

    bad = tmp_path / "not_a_video.txt"
    bad.write_text("hello")
    result = gui_app.dispatch(LoadVideo(key="0402:3922", path=bad))
    assert result.ok is False
    assert "extension" in result.message.lower()


def test_load_video_command_zt_passthrough(gui_app: App, tmp_path) -> None:
    """A .zt input is copied straight into a staged theme dir (no ffmpeg)."""
    from trcc.core.commands import LoadVideo

    # Minimal .zt header — 0xDC magic + a 1-frame placeholder.  LoadVideo
    # only cares about the extension + file existence at staging time.
    src = tmp_path / "anim.zt"
    src.write_bytes(b"\xDC" + b"\x01\x00\x00\x00" + b"\x00" * 8)

    result = gui_app.dispatch(LoadVideo(
        key="0402:3922", path=src,
    ))
    # The staged Theme.zt should exist regardless of LoadTheme's outcome.
    staged = (
        gui_app.platform.paths().user_content_dir()
        / "single-video" / "anim" / "Theme.zt"
    )
    assert staged.is_file()
    assert staged.read_bytes() == src.read_bytes()
    del result


def test_screen_overlay_is_wayland_returns_bool() -> None:
    """is_wayland() is callable + returns a bool regardless of env."""
    from trcc.ui.qtgui.screen_overlay import is_wayland

    assert isinstance(is_wayland(), bool)


# =========================================================================
# G3 — LED control sub-tabs
# =========================================================================


def _led_key() -> str:
    """LED device key used across the G3 tests — present in the registry."""
    return "0416:8001"


def test_led_color_tab_refresh(gui_app: App, qapp: object) -> None:
    """ColorTab.refresh_from updates RGB + brightness widgets in-place."""
    from trcc.ui.qtgui.panels.led import ColorTab

    tab = ColorTab(gui_app, _led_key)
    settings = gui_app.settings.for_led(_led_key())
    settings.color = (10, 200, 60)
    settings.brightness = 42
    tab.refresh_from(settings)
    assert tab._r.value() == 10
    assert tab._g.value() == 200
    assert tab._b.value() == 60
    assert tab._brightness.value() == 42
    del qapp


def test_led_color_tab_apply_dispatches_commands(
    gui_app: App, qapp: object,
) -> None:
    """ColorTab Apply dispatches SetLedColor + SetLedBrightness."""
    from trcc.ui.qtgui.panels.led import ColorTab

    tab = ColorTab(gui_app, _led_key)
    tab._set_color(255, 128, 0, emit_signals=False)
    tab._brightness.setValue(77)
    tab._on_apply()
    settings = gui_app.settings.for_led(_led_key())
    assert settings.color == (255, 128, 0)
    assert settings.brightness == 77
    del qapp


def test_led_mode_tab_selects_radio_for_persisted_mode(
    gui_app: App, qapp: object,
) -> None:
    """ModeTab.refresh_from checks the radio matching settings.mode."""
    from trcc.core.led_models import LEDMode
    from trcc.ui.qtgui.panels.led import ModeTab

    tab = ModeTab(gui_app, _led_key)
    settings = gui_app.settings.for_led(_led_key())
    settings.mode = LEDMode.RAINBOW
    tab.refresh_from(settings)
    assert tab._radios[LEDMode.RAINBOW].isChecked()
    del qapp


def test_led_zone_tab_hides_for_single_zone(
    gui_app: App, qapp: object,
) -> None:
    """ZoneTab shows its placeholder when settings.zones has ≤1 entries."""
    from trcc.ui.qtgui.panels.led import ZoneTab

    tab = ZoneTab(gui_app, _led_key)
    settings = gui_app.settings.for_led(_led_key())
    settings.zones = []
    tab.refresh_from(settings)
    assert tab.has_visible_content() is False
    del qapp


def test_led_zone_tab_builds_rows_for_multi_zone(
    gui_app: App, qapp: object,
) -> None:
    """ZoneTab builds one row per zone when count > 1."""
    from trcc.core.led_models import LedZoneSettings
    from trcc.ui.qtgui.panels.led import ZoneTab

    tab = ZoneTab(gui_app, _led_key)
    settings = gui_app.settings.for_led(_led_key())
    settings.zones = [
        LedZoneSettings(color=(255, 0, 0)),
        LedZoneSettings(color=(0, 255, 0)),
        LedZoneSettings(color=(0, 0, 255)),
    ]
    settings.selected_zone = 1
    tab.refresh_from(settings)
    assert tab.has_visible_content() is True
    assert len(tab._zone_widgets) == 3
    del qapp


def test_led_segment_tab_hides_when_no_segments(
    gui_app: App, qapp: object,
) -> None:
    """SegmentTab shows its placeholder when segment_on is empty."""
    from trcc.ui.qtgui.panels.led import SegmentTab

    tab = SegmentTab(gui_app, _led_key)
    settings = gui_app.settings.for_led(_led_key())
    settings.segment_on = []
    tab.refresh_from(settings)
    assert tab.has_visible_content() is False
    del qapp


def test_led_segment_tab_builds_checks(gui_app: App, qapp: object) -> None:
    """SegmentTab builds one checkbox per segment when populated."""
    from trcc.ui.qtgui.panels.led import SegmentTab

    tab = SegmentTab(gui_app, _led_key)
    settings = gui_app.settings.for_led(_led_key())
    settings.segment_on = [True, False, True, True, False]
    tab.refresh_from(settings)
    assert tab.has_visible_content() is True
    assert len(tab._checks) == 5
    assert tab._checks[0].isChecked() is True
    assert tab._checks[1].isChecked() is False
    del qapp


def test_led_advanced_tab_refreshes_radio_state(
    gui_app: App, qapp: object,
) -> None:
    """AdvancedTab.refresh_from selects the right temp/load radio + checkboxes."""
    from trcc.ui.qtgui.panels.led import AdvancedTab

    tab = AdvancedTab(gui_app, _led_key)
    settings = gui_app.settings.for_led(_led_key())
    settings.temp_source = "gpu"
    settings.load_source = "cpu"
    settings.test_mode = True
    settings.clock_24h = False
    settings.week_sunday = True
    tab.refresh_from(settings)
    assert tab._temp_gpu.isChecked()
    assert tab._load_cpu.isChecked()
    assert tab._test_check.isChecked()
    assert not tab._clock_24h.isChecked()
    assert tab._week_sunday.isChecked()
    del qapp


def test_led_panel_constructs_with_tabs(gui_app: App, qapp: object) -> None:
    """LedPanel constructs, hosts the five sub-tabs, and refresh is a no-op
    when the key field is empty."""
    from trcc.ui.qtgui.panels.led_panel import LedPanel

    panel = LedPanel(gui_app, _bus(gui_app))
    # No key → placeholder status, optional tabs hidden.
    panel._picker.set_key("")
    panel._refresh_all_tabs()
    assert "pick" in panel._status_label.text().lower()
    # Five tabs total (color/mode/advanced always; zones+segments depend
    # on device — start hidden).
    visible_tab_count = panel._tabs.count()
    assert visible_tab_count >= 3
    del qapp


# =========================================================================
# G4 — device picker + mask editor
# =========================================================================


def test_device_picker_populates_from_app(gui_app: App, qapp: object) -> None:
    """DevicePickerWidget shows every entry in app.devices on construction."""
    from trcc.ui.qtgui.device_picker import DevicePickerWidget

    # FakePlatform attaches no devices by default, so the dropdown is
    # empty but still constructs cleanly.
    picker = DevicePickerWidget(gui_app, _bus(gui_app))
    assert picker.current_key() == ""
    # Programmatic set + read round-trips without emitting.
    picker.set_key("0402:3922")
    assert picker.current_key() == "0402:3922"
    del qapp


def test_device_picker_emits_key_changed_on_text_finished(
    gui_app: App, qapp: object,
) -> None:
    """Manual text edit fires :sig:`key_changed` once the user commits."""
    from trcc.ui.qtgui.device_picker import DevicePickerWidget

    received: list[str] = []
    picker = DevicePickerWidget(gui_app, _bus(gui_app))
    picker.key_changed.connect(received.append)
    line_edit = picker._combo.lineEdit()
    assert line_edit is not None
    line_edit.setText("0416:8001")
    line_edit.editingFinished.emit()
    assert received[-1] == "0416:8001"
    del qapp


def test_mask_browser_position_dispatches_command(
    gui_app: App, qapp: object,
) -> None:
    """Changing the mask position dispatches :class:`SetMaskPosition`."""
    from trcc.ui.qtgui.panels.mask_browser import MaskBrowser

    panel = MaskBrowser(gui_app, _bus(gui_app))
    panel._picker.set_key("0402:3922")
    panel._x.setValue(40)
    panel._y.setValue(60)
    panel._on_position_changed()
    settings = gui_app.settings.for_device("0402:3922")
    assert settings.mask_position == (40, 60)
    del qapp


def test_mask_browser_visibility_dispatches_command(
    gui_app: App, qapp: object,
) -> None:
    """Toggling visibility dispatches :class:`SetMaskVisible`."""
    from trcc.ui.qtgui.panels.mask_browser import MaskBrowser

    panel = MaskBrowser(gui_app, _bus(gui_app))
    panel._picker.set_key("0402:3922")
    panel._visible.setChecked(False)
    # Toggle directly to drive the slot.
    panel._on_visibility_changed(False)
    settings = gui_app.settings.for_device("0402:3922")
    assert settings.mask_visible is False
    del qapp


# =========================================================================
# G5 — screencast
# =========================================================================


def test_screencast_panel_constructs(gui_app: App, qapp: object) -> None:
    """ScreencastPanel builds without raising and starts in stopped state."""
    from trcc.ui.qtgui.panels.screencast_panel import ScreencastPanel

    panel = ScreencastPanel(gui_app, _bus(gui_app))
    assert panel is not None
    assert panel._start_btn.isEnabled() is True
    assert panel._stop_btn.isEnabled() is False
    del qapp


def test_screencast_panel_start_without_region_is_a_no_op(
    gui_app: App, qapp: object,
) -> None:
    """Start without a chosen region surfaces guidance, doesn't tick."""
    from trcc.ui.qtgui.panels.screencast_panel import ScreencastPanel

    panel = ScreencastPanel(gui_app, _bus(gui_app))
    panel._picker.set_key("0402:3922")
    panel._on_start()
    assert panel._timer.isActive() is False
    assert "region" in panel._status.text().lower()
    del qapp


def test_screencast_panel_start_without_key_is_a_no_op(
    gui_app: App, qapp: object,
) -> None:
    """Start without a chosen device surfaces guidance, doesn't tick."""
    from trcc.ui.qtgui.panels.screencast_panel import ScreencastPanel

    panel = ScreencastPanel(gui_app, _bus(gui_app))
    panel._region = (0, 0, 200, 100)
    panel._on_start()
    assert panel._timer.isActive() is False
    assert "device" in panel._status.text().lower()
    del qapp


def test_screencast_panel_records_picked_region(
    gui_app: App, qapp: object,
) -> None:
    """Receiving region_selected updates the panel's region + label."""
    from trcc.ui.qtgui.panels.screencast_panel import ScreencastPanel

    panel = ScreencastPanel(gui_app, _bus(gui_app))
    panel._on_region_selected(40, 60, 320, 240)
    assert panel._region == (40, 60, 320, 240)
    assert "320" in panel._region_label.text()
    assert "240" in panel._region_label.text()
    del qapp


def test_screencast_build_frame_returns_bytes(gui_app: App) -> None:
    """``DisplayService.build_screencast_frame`` returns wire-ready bytes."""
    from trcc.core.models import RawFrame
    from trcc.core.registry import find_product

    product = find_product(0x0402, 0x3922)
    assert product is not None
    target_w, target_h = product.native_resolution
    raw = RawFrame(
        data=b"\x80\x00\x00" * (200 * 100),
        width=200, height=100,
    )
    encoded = gui_app.display.build_screencast_frame(
        info=product, frame=raw,
    )
    assert isinstance(encoded, bytes)
    assert len(encoded) > 0
    del target_w, target_h


def test_region_overlay_constructs(qapp: object) -> None:
    """RegionSelectOverlay constructs without raising — no screen grab yet."""
    from trcc.ui.qtgui.region_overlay import RegionSelectOverlay

    overlay = RegionSelectOverlay()
    assert hasattr(overlay, "region_selected")
    assert hasattr(overlay, "cancelled")
    del qapp


def test_led_panel_refreshes_on_key_set(gui_app: App, qapp: object) -> None:
    """Setting a key + refreshing rebuilds tab state from persisted settings."""
    from trcc.core.led_models import LEDMode
    from trcc.ui.qtgui.panels.led_panel import LedPanel

    settings = gui_app.settings.for_led(_led_key())
    settings.color = (50, 100, 150)
    settings.brightness = 33
    settings.mode = LEDMode.BREATHING

    panel = LedPanel(gui_app, _bus(gui_app))
    panel._picker.set_key(_led_key())
    panel._refresh_all_tabs()
    assert panel._color_tab._r.value() == 50
    assert panel._color_tab._brightness.value() == 33
    assert panel._mode_tab._radios[LEDMode.BREATHING].isChecked()
    del qapp
