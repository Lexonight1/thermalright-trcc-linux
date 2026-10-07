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
from collections.abc import Iterator
from pathlib import Path

import pytest

from trcc.app import App
from trcc.core.led_models import LED_STYLES

from .conftest import FakeMic, FakePlatform

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
    from trcc.core.events import EventBus
    from trcc.ui.bus_bridge import BusBridge

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
    from trcc.core.events import DeviceConnected, EventBus
    from trcc.ui.bus_bridge import BusBridge

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
    from trcc.ui.bus_bridge import BusBridge
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


def test_uc_device_overflow_scrolls_within_fixed_area(qapp: object) -> None:
    """Device sidebar overflow-scrolls INSIDE its fixed region.

    Many device buttons grow only the inner content (so it scrolls); the
    scroll area's own geometry never changes, so the sidebar stays in its
    allotted space and never pushes the sensor/about buttons.
    """
    from trcc.ui.gui.assets import _PKG_ASSETS_DIR, set_assets_dir
    from trcc.ui.gui.constants import Layout
    from trcc.ui.gui.uc_device import UCDevice
    set_assets_dir(_PKG_ASSETS_DIR)

    _, _, area_w, area_h = Layout.DEVICE_AREA
    panel = UCDevice()

    # Scroll area pinned to the allotted region.
    assert (panel.device_scroll.width(), panel.device_scroll.height()) == (
        area_w, area_h)

    # Short list → inner content within the viewport (no overflow).
    panel.update_devices([{"name": "A", "path": "/a"}])
    assert panel.device_area.minimumHeight() <= area_h

    # Long list → inner content exceeds the viewport (overflow → scroll)…
    panel.update_devices(
        [{"name": f"D{i}", "path": f"/d{i}"} for i in range(12)])
    assert panel.device_area.minimumHeight() > area_h
    # …yet the scroll area itself never grew — sidebar stays in its space.
    assert (panel.device_scroll.width(), panel.device_scroll.height()) == (
        area_w, area_h)


def test_list_gpus_feeds_about_panel_gpu_data(gui_app: App) -> None:
    """ListGpus surfaces the aggregator's GPUs — the data the About panel
    feeds its GPU label/dropdown.  Was empty via the dead get_gpu_list,
    so the panel wrongly showed 'No GPU detected'."""
    from trcc.core.commands import ListGpus

    result = gui_app.dispatch(ListGpus())
    assert result.ok
    assert len(result.gpus) >= 1
    assert result.gpus[0].key and result.gpus[0].name


def test_list_gpus_no_gpus_does_not_crash(tmp_home: Path) -> None:
    """ListGpus returns an empty list (not crash) when the enumerator finds
    no GPUs — e.g. pynvml absent, a supported optional state.  Regression:
    it did ``_gpus or sensors.gpus`` and iterated the uncalled ``.gpus``
    METHOD → "'method' object is not iterable", crashing GUI boot."""
    from trcc.adapters.sensors.aggregator import BaselineSensors
    from trcc.app import App
    from trcc.core.commands import ListGpus

    from .conftest import FakeCpu, FakeMemory, FakePlatform

    class _NoGpuPlatform(FakePlatform):
        def sensors(self):  # type: ignore[override]
            if self._sensors is None:
                self._sensors = BaselineSensors(
                    cpu=FakeCpu(), memory=FakeMemory(), gpus=[], fans=[])
            return self._sensors

    result = App(platform=_NoGpuPlatform(tmp_home)).dispatch(ListGpus())
    assert result.ok
    assert result.gpus == []


def test_uc_about_gpu_widget_label_and_dropdown(qapp: object) -> None:
    """UCAbout shows the GPU name (not 'No GPU detected') for one GPU and a
    list-select dropdown for multiple."""
    from trcc.ui.gui.assets import _PKG_ASSETS_DIR, set_assets_dir
    from trcc.ui.gui.uc_about import UCAbout
    set_assets_dir(_PKG_ASSETS_DIR)

    one = UCAbout(gpu_list=[("nvidia:0", "RTX 4090")])
    assert one._gpu_label.text() == "RTX 4090"

    two = UCAbout(gpu_list=[("nvidia:0", "RTX 4090"), ("intel:igpu", "UHD 770")])
    assert two._gpu_combo.count() == 2


def test_sensor_picker_renders_hardware_metrics(
    gui_app: App, qapp: object,
) -> None:
    """The metric chooser must render rows for the category-prefixed
    sensors (cpu/gpu/…), not only legacy sources — else it shows blank.
    Clock sources (time/date) are excluded, matching legacy.

    Takes the App, not a ``SensorEnumerator``: the dialog asks the bus
    (``ReadSensors``) for identities AND values in one call, so a daemon-mode
    client can open it.
    """
    from trcc.ui.gui.assets import _PKG_ASSETS_DIR, set_assets_dir
    from trcc.ui.gui.uc_sensor_picker import SensorPickerDialog
    set_assets_dir(_PKG_ASSETS_DIR)

    dlg = SensorPickerDialog(gui_app)
    try:
        ids = {r.sensor.id for r in dlg._rows}
        assert dlg._rows  # non-empty — was blank under the hardcoded list
        assert any(i.startswith("cpu") for i in ids)
        assert any(i.startswith("gpu") for i in ids)
        assert not any(i.startswith(("time", "date")) for i in ids)
    finally:
        dlg._timer.stop()
        dlg.deleteLater()


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


@pytest.mark.parametrize("style", list(LED_STYLES), ids=lambda s: s.name)
def test_qtgui_advanced_tab_sections_match_panel_model(gui_app: App, style) -> None:
    """qtgui AdvancedTab shows only the sub-sections led_panel_for(style)
    declares — the same C#-sourced composition the gui renders from."""
    from trcc.core.led_models import LEGACY_STYLE_ID
    from trcc.ui.presentation.led_panel import led_panel_for
    from trcc.ui.qtgui.panels.led.advanced_tab import AdvancedTab

    tab = AdvancedTab(gui_app, lambda: "")
    m = led_panel_for(LEGACY_STYLE_ID[style])
    tab.apply_panel(m)

    assert tab._sources_box.isVisibleTo(tab) == m.show_sensor_gauges, \
        f"{style.name} sensor linkage"
    assert tab._clock_box.isVisibleTo(tab) == m.show_clock_panel, \
        f"{style.name} clock"
    assert tab._misc_box.isVisibleTo(tab) == (
        m.show_memory_panel or m.show_disk_panel
    ), f"{style.name} memory/disk"


# 1x1 GIF -- Qt reads GIF but cannot write one.
_GIF = (b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff"
        b"!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01"
        b"\x00\x00\x02\x02D\x01\x00;")


def test_cloud_tile_reads_the_apps_gif_or_shows_the_png(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#264: the tile made its GIF itself with ffmpeg, on the UI thread,
    ~100 ms a tile on every visit.  It only reads now -- the App's GIF when
    there is one, else the preview PNG (all the C# ever shows).  With no GIF
    it used to show the placeholder, because a downloaded theme had no
    image path at all."""
    import subprocess

    from PySide6.QtGui import QColor, QImage

    from trcc.core.models import CloudThemeItem
    from trcc.ui.gui.uc_theme_web import CloudThemeThumbnail

    def refuse(*_a: object, **_k: object) -> None:
        raise AssertionError("the cloud tile ran a subprocess")

    monkeypatch.setattr(subprocess, "run", refuse)
    monkeypatch.setattr(subprocess, "Popen", refuse)
    png = tmp_path / "a001.png"
    still = QImage(60, 30, QImage.Format.Format_RGB32)
    still.fill(QColor(255, 0, 0))
    assert still.save(str(png))
    mp4 = tmp_path / "a001.mp4"
    mp4.write_bytes(b"\x00\x00\x00\x1cftypmp42")
    item = CloudThemeItem(name="a001", id="a001", video=str(mp4),
                          preview=str(png), is_local=True)

    tile = CloudThemeThumbnail(item)
    assert tile._movie is None
    assert tile.thumb_label.pixmap().toImage().pixelColor(60, 60) == QColor(255, 0, 0)

    (tmp_path / "a001.gif").write_bytes(_GIF)
    animated = CloudThemeThumbnail(item)
    assert animated._movie is not None
    assert animated._movie.fileName() == str(tmp_path / "a001.gif")


def test_about_panel_constructs(gui_app: App) -> None:
    from trcc.ui.qtgui.panels.about_panel import AboutPanel

    panel = AboutPanel(gui_app, _bus(gui_app))
    assert panel is not None
    assert panel.layout() is not None


def test_system_panel_constructs(gui_app: App, qtbot) -> None:
    """The host stacks six boxes and refreshes exactly ONE of them.

    The live wiring is the load-bearing half: ``ListMemorySlots`` shells out
    to ``dmidecode``, so a host that refreshed every box would run a root
    subprocess on every update.  Asserting the layout alone cannot see that,
    which is why what the live path dispatches is checked here.

    The TRIGGER changed on 2026-09-19 and the property did not.  The panel
    held a 2 s ``QTimer`` that ignored ``refresh_interval_s``; sensors now ride
    ``SensorsUpdated``, so the two live paths are a view-switch (identity, one
    ``ReadSensors``) and a broadcast (values, no dispatch at all).  Both are
    driven below — the second is the stronger statement the timer could never
    make.
    """
    from trcc.core.events import SensorsUpdated
    from trcc.ui.qtgui.panels.system_panel import SystemPanel

    panel = SystemPanel(gui_app, _bus(gui_app))
    qtbot.addWidget(panel)
    assert panel.layout() is not None
    assert panel.layout().count() == 7, "six boxes + the action row"

    boxes = (panel._platform, panel._gpu, panel._maintenance,
             panel._health, panel._sensors, panel._dash)
    ticked: list[str] = []
    for box in boxes:
        real = box.dispatch

        def spy(cmd, _real=real):
            ticked.append(type(cmd).__name__)
            return _real(cmd)

        box.dispatch = spy                # pyright: ignore[reportAttributeAccessIssue]

    panel.show()                          # a real view-switch
    qtbot.waitExposed(panel)

    assert set(ticked) == {"ReadSensors"}, (
        "opening the panel must dispatch ReadSensors and NOTHING else; "
        f"got {sorted(set(ticked))} — ListMemorySlots on this list is "
        "dmidecode running as root every time the user opens the panel"
    )

    ticked.clear()
    gui_app.events.publish(SensorsUpdated(
        reading_count=1, readings={"cpu:temp": 44.0}, temp_unit="C"))
    qtbot.wait(50)

    assert ticked == [], (
        "a broadcast must be RENDERED, not re-read: the panel was handed the "
        f"values and dispatched {sorted(set(ticked))} anyway"
    )


# =========================================================================
# MaintenanceBox — the autostart target picker
# =========================================================================
#
# The picker chooses WHICH ui login brings up.  Its rules were comments in
# the panel until these tests: changing it must never enable autostart the
# user did not ask for, and what it shows must be what is INSTALLED.
#
# These build the BOX, not the whole panel: a leaf gate on a leaf.  Reaching
# it through ``SystemPanel._maintenance`` would also work, but would drag in
# five unrelated boxes' Commands on every one of them.


def _maintenance_box(gui_app: App, qtbot):
    from trcc.ui.qtgui.panels.system import MaintenanceBox

    box = MaintenanceBox(gui_app, _bus(gui_app))
    qtbot.addWidget(box)
    return box


def test_autostart_picker_offers_every_target(gui_app: App, qtbot) -> None:
    """The picker's list IS the registry — never a second copy to drift."""
    from trcc.core.models import AUTOSTART_TARGETS

    box = _maintenance_box(gui_app, qtbot)
    combo = box._autostart_target
    offered = {combo.itemData(i) for i in range(combo.count())}
    assert offered == set(AUTOSTART_TARGETS)


def test_autostart_picker_does_not_install_while_disabled(
    gui_app: App, qtbot,
) -> None:
    """Choosing a target with autostart OFF must not turn it ON.

    The same invariant ``refresh`` holds: a control that repairs or re-points
    an entry never creates one.
    """
    box = _maintenance_box(gui_app, qtbot)
    assert not box._autostart_check.isChecked()
    box._autostart_target.setCurrentIndex(
        box._autostart_target.findData("daemon"),
    )
    assert not gui_app.platform.autostart().is_enabled()


def test_autostart_picker_reinstalls_for_the_new_target(
    gui_app: App, qtbot,
) -> None:
    """With autostart ON, choosing a target re-installs for THAT target."""
    box = _maintenance_box(gui_app, qtbot)
    box._autostart_check.setChecked(True)           # fires the toggle handler
    assert gui_app.platform.autostart().is_enabled()

    box._autostart_target.setCurrentIndex(
        box._autostart_target.findData("api"),
    )
    assert gui_app.platform.autostart().installed_target() == "api"


def test_autostart_picker_shows_the_installed_target(
    gui_app: App, qtbot,
) -> None:
    """A refresh reads the ENTRY, not whatever the widget last showed.

    Another surface (cli, api, a second window) may have changed it, and the
    installed entry is the only record of what login will actually launch.
    """
    box = _maintenance_box(gui_app, qtbot)
    gui_app.platform.autostart().enable("qtgui")    # changed behind its back
    box.refresh()
    assert box._autostart_target.currentData() == "qtgui"


@pytest.mark.parametrize(("fan_gpu", "readings", "text"), [
    (1000.0, {"fan:gpu": 1000.0}, "1000RPM"),
    (0.0, {"fan:gpu:percent": 30.0}, "30.0%"),     # never "0.0RPM" or "30RPM"
])
def test_gui_gpufan_row_shows_the_unit_its_reading_has(
        qtbot, fan_gpu: float, readings: dict[str, float], text: str) -> None:
    """The ``ui/gui`` activity sidebar's GPUFAN row -- the #145 screenshot."""
    from trcc.core.models import HardwareMetrics
    from trcc.ui.gui.uc_activity_sidebar import SensorItem

    item = SensorItem("fan", "gpu_fan", "GPUFAN", "RPM", "fan_gpu", "#ffffff")
    qtbot.addWidget(item)
    item.update_value(HardwareMetrics(fan_gpu=fan_gpu, readings=readings))
    assert item.value_label.text() == text


def test_activity_sidebar_emits_selection(gui_app: App) -> None:
    """Sidebar click → selected signal fires with the entry key.

    Clicks the real button rather than calling the slot with a key, because
    the key no longer travels as a call argument: it rides on the button as a
    Qt property so the slot can be a named method instead of a closure over
    the loop variable (``feedback_no_lambdas``).  Driving the button exercises
    the property, the connection and the slot together — calling the slot
    directly would prove none of them.
    """
    from trcc.ui.qtgui.panels.sidebar import ActivitySidebar

    sidebar = ActivitySidebar(gui_app, _bus(gui_app))
    captured: list[str] = []
    sidebar.selected.connect(captured.append)
    sidebar._buttons["system"].click()
    assert captured == ["system"]


def test_base_panel_requires_setup_ui() -> None:
    """A subclass that forgets _setup_ui() can't even be defined."""
    from trcc.ui.qtgui.base import BasePanel

    with pytest.raises(TypeError, match="must implement _setup_ui"):
        class _BrokenPanel(BasePanel):  # type: ignore[misc]
            pass


def test_assets_resolve_missing_returns_placeholder(qapp: object) -> None:
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
        size=20, font="DejaVu Sans", bold=True, italic=False,
        metric="cpu:temp", format="{value:.0f}°C", show_unit=False,
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
    assert out["font"] == "DejaVu Sans"     # the family control, not just bold
    assert out["show_unit"] is False        # button0 unit-switch round-trips


def test_overlay_editor_shows_the_layout_and_writes_nothing_on_open(
    gui_app: App, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Opening the editor, or switching it to a device, reads that device's
    layout and writes nothing.  It used to adopt the theme's layout into the
    user layer with a SetOverlayConfig on every refresh -- which, once the
    rail's selection reached it, would have fired on every device pick.
    ``LoadTheme`` adopts the layout itself, for every UI.  An edit then
    changes the listed element in place."""
    from pathlib import Path

    from trcc.core.commands import AddOverlayElement, Query, UpdateOverlayElement
    from trcc.core.models import Theme
    from trcc.ui.qtgui.panels.overlay_editor import OverlayEditorPanel

    key = "0402:3922"
    # A theme's layout with no user layer of its own: the state the editor
    # used to "adopt" by writing it.  It must show it, and write nothing.
    gui_app.active_themes[key] = Theme(
        path=Path("/nonexistent/t"), name="t", resolution=(320, 320),
        config={"elements": [{"type": "text", "text": "CPU", "x": 10, "y": 10}]},
    )
    writes: list[str] = []
    real = gui_app.dispatch

    def _record(command: object) -> object:
        if not isinstance(command, Query):
            writes.append(type(command).__name__)
        return real(command)

    monkeypatch.setattr(gui_app, "dispatch", _record)
    panel = OverlayEditorPanel(gui_app, _bus(gui_app))
    panel._picker.set_key(key)
    panel.refresh()

    assert writes == [], f"opening the editor wrote {writes}"
    assert panel._list.count() == 1
    assert gui_app.settings.for_device(key).user_overlay_elements is None

    assert gui_app.dispatch(AddOverlayElement(
        key=key, element_id="cpu", type="text", text="CPU", x=10, y=10)).ok
    assert gui_app.dispatch(
        UpdateOverlayElement(key=key, element_id="cpu", x=99, y=99)).ok
    after = gui_app.settings.for_device(key).user_overlay_elements
    assert after is not None and len(after) == 1, "edit must replace in place"
    assert (after[0].x, after[0].y) == (99, 99)


def test_overlay_grid_loads_metric_from_the_app_with_its_ids(
    gui_app: App, qapp: object,
) -> None:
    """Legacy-style grid: a metric element loads (not dropped), every cell
    keeps the App's id.

    Metrics were once dropped on load (mapped by legacy name, not ``cpu:temp``)
    so they could not be dragged; and the grid once re-minted every id
    positionally, so an id the CLI or API held stopped naming anything.
    """
    from trcc.core.commands import AddOverlayElement, ResolveOverlay
    from trcc.core.models import OverlayMode
    from trcc.ui.gui.overlay_grid import OverlayGridPanel
    from trcc.ui.presentation.overlay_serialization import entries_to_configs

    key = "0402:3922"
    assert gui_app.dispatch(AddOverlayElement(
        key=key, element_id="m1", type="metric", metric="cpu:temp",
        x=74, y=250, color="#112233")).ok
    assert gui_app.dispatch(AddOverlayElement(
        key=key, element_id="t1", type="text", text="HI")).ok

    grid = OverlayGridPanel()
    grid.load_configs(entries_to_configs(
        gui_app.dispatch(ResolveOverlay(key=key)).elements))

    cells = grid.get_all_configs()
    assert [c.id for c in cells] == ["m1", "t1"]
    metric = cells[0]
    assert metric.mode is OverlayMode.HARDWARE
    assert (metric.main_count, metric.sub_count) == (0, 1)
    assert metric.color == "#112233"


def test_color_change_sends_one_update_naming_the_element(
    gui_app: App, qapp: object,
) -> None:
    """A colour edit reaches the delegate as ONE ``UpdateOverlayElement`` for
    the selected element, carrying the colour and nothing else.

    It used to send the whole grid through ``SetOverlayConfig``, which
    rewrote every other element in the grid's reduced shape and put back what
    another UI had changed.  Drives the real UCThemeSetting → delegate hop.
    """
    del gui_app, qapp
    from trcc.core.commands import UpdateOverlayElement
    from trcc.core.models import OverlayElementConfig, OverlayMode
    from trcc.ui.gui.uc_theme_setting import UCThemeSetting

    panel = UCThemeSetting()
    captured: list[tuple[int, object]] = []
    panel.delegate.connect(lambda cmd, info, data: captured.append((cmd, info)))

    panel.set_overlay_enabled(True)
    panel.load_configs([
        OverlayElementConfig(id="t1", mode=OverlayMode.CUSTOM, text="HI",
                             x=5, y=5, color="#ffffff"),
    ])
    panel.overlay_grid.select_element(0)
    panel._on_color_changed(0x11, 0x22, 0x33)

    edits = [info for cmd, info in captured
             if cmd == UCThemeSetting.CMD_OVERLAY_CHANGED]
    assert len(edits) == 1, f"one colour change, {len(edits)} edits"
    command = edits[0](key="0402:3922")
    assert command == UpdateOverlayElement(
        key="0402:3922", element_id="t1", color="#112233",
    ), "only the changed field may be sent"


def test_configuration_panel_constructs(gui_app: App) -> None:
    from trcc.ui.qtgui.panels.configuration_panel import ConfigurationPanel

    panel = ConfigurationPanel(gui_app, _bus(gui_app))
    assert panel is not None
    assert panel.layout() is not None


def test_configuration_panel_load_without_key(gui_app: App) -> None:
    from trcc.ui.qtgui.panels.configuration_panel import ConfigurationPanel

    panel = ConfigurationPanel(gui_app, _bus(gui_app))
    panel._show_state()
    assert "pick a device" in panel._status.text().lower()


_KEY_Q3 = "0402:3922"


def _seed_non_defaults(app: App) -> None:
    """A value different from every control's default, so a reset shows."""
    from trcc.core.commands import (
        EnableOverlay,
        SetBackgroundMode,
        SetBrightness,
        SetFitMode,
        SetLanguage,
        SetOrientation,
        SetRefreshInterval,
        SetTempUnit,
    )
    for command in (SetFitMode(key=_KEY_Q3, mode="height"),
                    EnableOverlay(key=_KEY_Q3, enabled=False),
                    SetBackgroundMode(key=_KEY_Q3, mode="color"),
                    SetBrightness(key=_KEY_Q3, percent=30),
                    SetOrientation(key=_KEY_Q3, degrees=180),
                    SetTempUnit(unit="F"), SetLanguage(language="de"),
                    SetRefreshInterval(seconds=3.0)):
        assert app.dispatch(command).ok, command


def _on_device(panel) -> None:
    """Point a bare panel's picker at the Q3 device and show it."""
    panel._picker.current_key = lambda: _KEY_Q3   # pyright: ignore[reportAttributeAccessIssue]
    panel._show_state() if hasattr(panel, "_show_state") else panel._on_refresh()


def _writes(panel) -> list:
    """Every non-Query Command the panel sends from here on."""
    from trcc.core.commands import Query

    sent: list = []
    real = panel.dispatch

    def spy(cmd):
        if not isinstance(cmd, Query):
            sent.append(cmd)
        return real(cmd)

    panel.dispatch = spy                      # pyright: ignore[reportAttributeAccessIssue]
    return sent


def test_the_configuration_panel_shows_what_the_app_holds(
    gui_app: App, qtbot, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On open it showed 9 of 9 settings wrong (widget defaults, measured
    2026-09-30), and "Apply" wrote them back.  Recorded at the App, from
    BEFORE construction: the panel's first show runs in its constructor."""
    from trcc.core.commands import Query
    from trcc.ui.qtgui.panels.configuration_panel import ConfigurationPanel

    _seed_non_defaults(gui_app)
    sent: list[str] = []
    real = gui_app.dispatch

    def _record(command: object) -> object:
        if not isinstance(command, Query):
            sent.append(type(command).__name__)
        return real(command)

    monkeypatch.setattr(gui_app, "dispatch", _record)
    panel = ConfigurationPanel(gui_app, _bus(gui_app))
    qtbot.addWidget(panel)
    _on_device(panel)

    shown = (panel._fit.currentData(), panel._overlay.currentData(),
             panel._bg_mode.currentData(), panel._temp_unit.currentData(),
             panel._language.currentData(), panel._refresh.value())
    assert shown == ("height", False, "color", "F", "de", 3.0)
    assert sent == [], f"showing the App's state wrote {sent}"


def test_one_configuration_control_sends_its_one_command(
    gui_app: App, qtbot,
) -> None:
    from trcc.core.commands import SetFitMode, SetRefreshInterval
    from trcc.ui.qtgui.panels.configuration_panel import ConfigurationPanel

    _seed_non_defaults(gui_app)
    panel = ConfigurationPanel(gui_app, _bus(gui_app))
    qtbot.addWidget(panel)
    _on_device(panel)
    sent = _writes(panel)

    stretch = panel._fit.findData("stretch")
    panel._fit.setCurrentIndex(stretch)
    panel._fit.activated.emit(stretch)                    # the user's pick
    panel._refresh.setValue(4.0)                          # typed + finished

    assert sent == [SetFitMode(key=_KEY_Q3, mode="stretch"),
                    SetRefreshInterval(seconds=4.0)], sent


@pytest.mark.parametrize("panel_name", ["configuration", "display", "status"])
def test_a_panel_follows_another_uis_change(
    gui_app: App, qtbot, panel_name: str,
) -> None:
    """Another UI sets brightness / fit; the panel shows it, no click."""
    from trcc.core.commands import SetBrightness, SetFitMode
    from trcc.ui.qtgui.panels.configuration_panel import ConfigurationPanel
    from trcc.ui.qtgui.panels.display_panel import DisplayPanel
    from trcc.ui.qtgui.panels.status_panel import StatusPanel

    cls = {"configuration": ConfigurationPanel, "display": DisplayPanel,
           "status": StatusPanel}[panel_name]
    panel = cls(gui_app, _bus(gui_app))
    qtbot.addWidget(panel)
    _on_device(panel)

    gui_app.dispatch(SetFitMode(key=_KEY_Q3, mode="height"))
    gui_app.dispatch(SetBrightness(key=_KEY_Q3, percent=42))

    shows = {
        "configuration": lambda: panel._fit.currentData() == "height",
        "display": lambda: panel._brightness.value() == 42,
        "status": lambda: panel._brightness_label.text() == "42%",
    }[panel_name]
    qtbot.waitUntil(shows, timeout=2000)


def test_the_game_mode_controls_send_their_own_half(
    gui_app: App, qtbot,
) -> None:
    """The switch and the threshold are two controls, as in the C#; each
    sends only its own field."""
    from trcc.core.commands import SetGameMode
    from trcc.ui.qtgui.panels.configuration_panel import ConfigurationPanel

    panel = ConfigurationPanel(gui_app, _bus(gui_app))
    qtbot.addWidget(panel)
    _on_device(panel)
    sent = _writes(panel)

    on = panel._game.findData(True)
    panel._game.setCurrentIndex(on)
    panel._game.activated.emit(on)                         # the user's pick
    panel._game_threshold.setValue(60)                     # typed + finished

    assert sent == [SetGameMode(key=_KEY_Q3, enabled=True),
                    SetGameMode(key=_KEY_Q3, threshold=60)], sent
    assert panel._game_threshold.maximum() == 99


def test_a_game_mode_change_reaches_other_windows(gui_app: App, qtbot) -> None:
    """Changed ALONE, so only GameModeChanged can carry it."""
    from trcc.core.commands import SetGameMode
    from trcc.ui.qtgui.panels.configuration_panel import ConfigurationPanel

    panel = ConfigurationPanel(gui_app, _bus(gui_app))
    qtbot.addWidget(panel)
    _on_device(panel)
    sent = _writes(panel)

    gui_app.dispatch(SetGameMode(key=_KEY_Q3, enabled=True, threshold=40))

    qtbot.waitUntil(lambda: (panel._game.currentData(),
                             panel._game_threshold.value()) == (True, 40),
                    timeout=2000)
    assert sent == [], "showing it must not send it back"


def test_a_background_change_reaches_other_windows(gui_app: App, qtbot) -> None:
    """SetBackgroundMode / SetOverlayBackground published nothing, so no other
    window could show the change.  Changed ALONE: any other event would make
    the panel re-read everything and hide a missing publish."""
    from trcc.core.commands import SetBackgroundMode, SetOverlayBackground
    from trcc.ui.qtgui.panels.configuration_panel import ConfigurationPanel

    panel = ConfigurationPanel(gui_app, _bus(gui_app))
    qtbot.addWidget(panel)
    _on_device(panel)

    gui_app.dispatch(SetBackgroundMode(key=_KEY_Q3, mode="color"))
    qtbot.waitUntil(lambda: panel._bg_mode.currentData() == "color", timeout=2000)
    gui_app.dispatch(SetOverlayBackground(key=_KEY_Q3, color=(1, 2, 3)))
    qtbot.waitUntil(lambda: panel._bg_color_label.text() == "#010203",
                    timeout=2000)


def test_the_display_panel_shows_and_sends_one_setting_at_a_time(
    gui_app: App, qtbot,
) -> None:
    """Its Apply sent orientation AND brightness AND re-loaded the theme on
    every press: 30% -> 100% and 180° -> 0° with nothing touched."""
    from trcc.core.commands import SetBrightness
    from trcc.ui.qtgui.panels.display_panel import DisplayPanel

    _seed_non_defaults(gui_app)
    panel = DisplayPanel(gui_app, _bus(gui_app))
    qtbot.addWidget(panel)
    sent = _writes(panel)
    _on_device(panel)
    panel._require_key = lambda: _KEY_Q3          # pyright: ignore[reportAttributeAccessIssue]
    assert (panel._orientation.currentData(), panel._brightness.value()) == (180, 30)
    assert sent == [], f"showing the App's state wrote {sent}"

    panel._brightness.setValue(55)                # released at 55
    assert sent == [SetBrightness(key=_KEY_Q3, percent=55)], sent


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


def test_preview_surface_unknown_key_shows_placeholder(gui_app: App) -> None:
    """An unselectable device explains itself instead of going blank.

    Moved from ``PreviewPanel`` with the image: the rendered surface is now
    window chrome (``PreviewSurface``), and the panel beside it carries only
    the state read-out.  The behaviour is unchanged and still worth holding —
    a silent black rectangle is the one outcome that tells the user nothing.
    """
    from trcc.ui.qtgui.device_selection import DeviceSelection
    from trcc.ui.qtgui.preview_surface import PreviewSurface

    selection = DeviceSelection()
    surface = PreviewSurface(gui_app, _bus(gui_app), selection)
    selection.set_key("dead:beef")
    surface.refresh()
    text = surface._label.text()
    assert "load a theme" in text.lower() or "no data" in text.lower(), text


def test_sensor_picker_filters_by_search(gui_app: App) -> None:
    """Search text narrows the visible sensor list."""
    from trcc.ui.qtgui.sensor_picker import SensorPickerWidget

    picker = SensorPickerWidget(gui_app, _bus(gui_app))
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
    # Staged under the theme-dir convention name, NOT the source basename —
    # the background resolver only reads 00.png, so keeping "test_image.png"
    # produced a dir it could not see and the panel went black (#245).
    assert (staged / "00.png").is_file()
    assert not (staged / "test_image.png").exists()


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


def test_main_window_constructs_and_includes_panels(make_window, gui_app: App) -> None:
    """The top-level MainWindow constructs and embeds the three panels."""

    window = make_window(gui_app)
    assert window is not None
    assert window.windowTitle()           # non-empty title set
    # Status bar gets wired during init for platform info + events
    assert window.statusBar() is not None


# =========================================================================
# MainWindow — display-start restore (METHOD_UI.md's entry contract)
# =========================================================================


class _StubDevice:
    """A device whose ``info`` is a REAL ``ProductInfo`` from the registry.

    ``ListDevices`` reads ``info.wire`` / ``info.kind`` — fields on
    ``ProductInfo``, not on ``DeviceInfo`` (which has neither).  Taking the
    entry from ``products_by_wire`` rather than hand-building one keeps the
    wire/kind pairing true to the registry, per CLAUDE.md's rule that
    ``ALL_DEVICES`` is the single source of truth for device-shaped tests.
    """

    def __init__(self, wire, connected: bool = True) -> None:
        from trcc.core.registry import products_by_wire

        self.info = products_by_wire(wire)[0]
        self.is_connected = connected
        # The Device port declares these; a stub that omits one makes the
        # window look broken when it reads what every real device has.
        self.profile = None
        self.handshake = None
        self.needs_keepalive = False

    @property
    def is_led(self) -> bool:
        from trcc.core.models import Wire
        return self.info.wire is Wire.LED

    @property
    def key(self) -> str:
        return f"{self.info.vid:04x}:{self.info.pid:04x}"


def _spy_on_dispatch(app: App) -> list[str]:
    """Record ``CommandName:key`` for everything dispatched."""
    seen: list[str] = []
    real = app.dispatch

    def _spy(command):
        seen.append(f"{type(command).__name__}:{getattr(command, 'key', '')}")
        return real(command)

    app.dispatch = _spy                       # type: ignore[method-assign]
    return seen


def test_the_window_restores_nothing_itself(make_window, gui_app: App) -> None:
    """Loading the saved display is the SESSION's (``App._prime``), for every
    UI.  qtgui restored its coldplug fleet in ``__init__`` and each hotplug in
    ``_on_connected`` — a second loader, which on the hotplug thread raced the
    session's for the same panel."""
    from trcc.core.events import DeviceConnected
    from trcc.core.models import Wire

    lcd = _StubDevice(Wire.SCSI)
    gui_app.devices[lcd.key] = lcd            # type: ignore[assignment]
    seen = _spy_on_dispatch(gui_app)

    window = make_window(gui_app)
    window._on_connected(DeviceConnected(key=lcd.key, resolution=(320, 320)))

    assert not [s for s in seen if s.startswith("RestoreDeviceState:")]


def _record_dispatches(app: App, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every Command name *app* is asked to run from here on, in order."""
    sent: list[str] = []
    real = app.dispatch

    def _record(command: object) -> object:
        sent.append(type(command).__name__)
        return real(command)

    monkeypatch.setattr(app, "dispatch", _record)
    return sent


def _with_a_theme(app: App) -> None:
    """A connected panel showing a theme -- what the window's ticker drove."""
    from pathlib import Path

    from trcc.core.commands import SetRefreshInterval
    from trcc.core.models import Theme, Wire

    lcd = _StubDevice(Wire.SCSI)
    app.devices[lcd.key] = lcd            # type: ignore[assignment]
    app.active_themes[lcd.key] = Theme(
        path=Path("/nonexistent/primed"), name="primed", resolution=(320, 320),
        config={"elements": []},
    )
    assert app.dispatch(SetRefreshInterval(seconds=1.0)).ok   # the minimum


def test_the_window_sends_nothing_to_the_panel(
    make_window, gui_app: App, qtbot, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The App's MetricsLoop renders every panel; the window only shows.

    qtgui ran its own RenderAndSend ticker beside it, so every frame went to
    the panel twice (measured 2026-09-30: 5 -> 10 in 5 s at a 1 s refresh,
    shown or hidden).  No session runs here, so any render is the window's.
    """
    _with_a_theme(gui_app)
    sent = _record_dispatches(gui_app, monkeypatch)

    window = make_window(gui_app)
    window.show()
    qtbot.wait(2300)          # two ticks of the 1 s interval the ticker ran at

    assert not {"RenderAndSend", "TickDisplay"} & set(sent), sent


def test_the_preview_renders_only_while_the_window_is_on_screen(
    make_window, gui_app: App, qtbot, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Closing qtgui hides it to the tray; its preview kept rendering a full
    frame every second for nobody (3 in 3 s, measured), and a window never
    shown did the same -- which is what 8 leaked test windows did into later
    tests.  It waits for a show, and stops at a hide."""
    _with_a_theme(gui_app)
    sent = _record_dispatches(gui_app, monkeypatch)

    window = make_window(gui_app)
    qtbot.wait(1300)
    assert sent.count("BuildPreview") == 0, "rendered before it was ever shown"

    window.show()
    qtbot.waitUntil(lambda: sent.count("BuildPreview") >= 2, timeout=3000)

    window.hide()
    qtbot.wait(100)
    after_hide = sent.count("BuildPreview")
    qtbot.wait(1300)
    assert sent.count("BuildPreview") == after_hide, "rendered while hidden"


def test_main_window_repairs_a_stale_autostart_entry(make_window, gui_app: App) -> None:
    """qtgui was the ONE ui that could not reach ``RefreshAutostart``.

    Measured: cli, api and gui each had a dispatch site; qtgui had zero.  So a
    qtgui-only user whose install moved kept an entry pointing at the old path
    — and the panel still showed "enabled", because ``is_enabled()`` is
    ``path.is_file()``, which a stale entry satisfies.  gui repairs this from
    its own window's ``__init__``; this is that, the same Command.
    """
    from trcc.core.commands import EnableAutostart

    mgr = gui_app.platform.autostart()
    gui_app.dispatch(EnableAutostart())
    mgr.command = "/moved/bin/trcc"               # the install moved
    assert mgr.installed_command != mgr.command, "fixture drift: not stale"

    make_window(gui_app)

    assert mgr.installed_command == "/moved/bin/trcc"


def test_main_window_does_not_enable_autostart(make_window, gui_app: App) -> None:
    """...and must NOT pick up gui's first-launch auto-enable.

    That half is a documented product decision for gui alone —
    ``ensure_autostart``'s docstring names qtgui: it "reads and toggles
    autostart but has never auto-enabled it, and moving this would hand it a
    behaviour it does not have."  Refresh repairs what the user already chose;
    enable would choose FOR them.  Pinned so the distinction cannot erode.
    """

    make_window(gui_app)

    assert not gui_app.platform.autostart().is_enabled(), (
        "opening the qtgui window opted the user into autostart"
    )


def test_the_gui_enables_autostart_once_and_then_respects_off(
    fake_platform: FakePlatform,
) -> None:
    """The regression: keyed on ``enabled``, the gui turned a user's "off"
    back on at EVERY launch.  Each launch is a fresh App reading settings
    back from disk, as a real relaunch is."""
    from trcc.core.commands import DisableAutostart, GetAutostartStatus
    from trcc.ui.gui.uc_about import ensure_autostart

    assert ensure_autostart(App(fake_platform)) is True, "first launch enables"
    App(fake_platform).dispatch(DisableAutostart())          # the user: off

    relaunched = App(fake_platform)
    assert ensure_autostart(relaunched) is False
    assert relaunched.dispatch(GetAutostartStatus()).enabled is False, (
        "the gui turned autostart back on after the user turned it off"
    )


def test_the_gui_respects_a_choice_made_in_another_ui_first(
    fake_platform: FakePlatform,
) -> None:
    """Turned off from the CLI, API or qtgui before the gui ever ran."""
    from trcc.core.commands import DisableAutostart
    from trcc.ui.gui.uc_about import ensure_autostart

    App(fake_platform).dispatch(DisableAutostart())

    assert ensure_autostart(App(fake_platform)) is False


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
    """The offscreen platform is a GUARANTEE the suite depends on, so prove it.

    Five tests assert offscreen behaviour outright — this one, the three in
    ``test_screencast_capture_chain`` that require Qt to DECLINE a grab rather
    than hand the wire a black frame, and the video-cut close test, which needs
    ``QWidget.close()`` to deliver ``closeEvent`` synchronously (an xcb window
    does not).  Measured under ``QT_QPA_PLATFORM=xcb``: exactly those 5 of 5849
    fail.

    This gate existed before and passed anyway, because the six places that set
    the variable all used ``os.environ.setdefault`` — a no-op when the
    contributor's shell already exports it.  The guarantee is now one
    assignment at the top of ``tests/conftest.py``; this asserts it held.
    """
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
    del tmp_path


def test_splash_make_returns_widget_with_fallback(qapp: object) -> None:
    """SplashScreen falls back to _FrameSplash when SPLASH_BG asset is absent.

    The asset isn't bundled in next/ yet, so this exercises the fallback.
    """
    from trcc.ui.qtgui.splash import make_splash

    splash = make_splash()
    assert hasattr(splash, "show")
    assert hasattr(splash, "close")


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


# =========================================================================
# G3 — LED control sub-tabs
# =========================================================================


def _led_key() -> str:
    """LED device key used across the G3 tests — present in the registry."""
    return "0416:8001"


def _snap(**fields):
    """A ``LedSnapshotResult`` for the LED tab tests.

    The six tabs take the RESULT now, not a live ``LedDeviceSettings``: reaching
    ``app.settings.for_led`` raised under TRCC_DAEMON=1 and handed a UI a mutable
    domain object.  Mutating ``settings`` and passing it here would no longer
    exercise the path the panel takes.
    """
    from trcc.core.results import LedSnapshotResult
    return LedSnapshotResult(ok=True, key=_led_key(), **fields)


def test_led_color_tab_refresh(gui_app: App, qapp: object) -> None:
    """ColorTab.refresh_from updates RGB + brightness widgets in-place."""
    from trcc.ui.qtgui.panels.led import ColorTab

    tab = ColorTab(gui_app, _led_key)
    tab.refresh_from(_snap(color=(10, 200, 60), brightness=42))
    assert tab._r.value() == 10
    assert tab._g.value() == 200
    assert tab._b.value() == 60
    assert tab._brightness.value() == 42


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


def test_led_mode_tab_selects_radio_for_persisted_mode(
    gui_app: App, qapp: object,
) -> None:
    """ModeTab.refresh_from checks the radio matching settings.mode."""
    from trcc.core.led_models import LEDMode
    from trcc.ui.qtgui.panels.led import ModeTab

    tab = ModeTab(gui_app, _led_key)
    tab.refresh_from(_snap(mode=LEDMode.RAINBOW.name))
    assert tab._radios[LEDMode.RAINBOW].isChecked()


def test_led_zone_tab_hides_for_single_zone(
    gui_app: App, qapp: object,
) -> None:
    """ZoneTab shows its placeholder when settings.zones has ≤1 entries."""
    from trcc.ui.qtgui.panels.led import ZoneTab

    tab = ZoneTab(gui_app, _led_key)
    tab.refresh_from(_snap(zones=()))
    assert tab.has_visible_content() is False


def test_led_zone_tab_builds_rows_for_multi_zone(
    gui_app: App, qapp: object,
) -> None:
    """ZoneTab builds one row per zone when count > 1."""
    from trcc.core.results import LedZoneEntry
    from trcc.ui.qtgui.panels.led import ZoneTab

    tab = ZoneTab(gui_app, _led_key)
    tab.refresh_from(_snap(
        zones=(
            LedZoneEntry(color=(255, 0, 0)),
            LedZoneEntry(color=(0, 255, 0)),
            LedZoneEntry(color=(0, 0, 255)),
        ),
        selected_zone=1,
    ))
    assert tab.has_visible_content() is True
    assert len(tab._zone_widgets) == 3


def test_led_advanced_tab_refreshes_radio_state(
    gui_app: App, qapp: object,
) -> None:
    """AdvancedTab.refresh_from selects the right temp/load radio + checkboxes."""
    from trcc.ui.qtgui.panels.led import AdvancedTab

    tab = AdvancedTab(gui_app, _led_key)
    tab.refresh_from(_snap(
        temp_source="gpu", load_source="cpu", test_mode=True,
        clock_24h=False, week_sunday=True,
    ))
    assert tab._temp_gpu.isChecked()
    assert tab._load_cpu.isChecked()
    assert tab._test_check.isChecked()
    assert not tab._clock_24h.isChecked()
    assert tab._week_sunday.isChecked()


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


def test_device_picker_selected_item_yields_key_not_label(
    gui_app: App, qapp: object,
) -> None:
    """Selecting a device from the dropdown must yield its KEY, not the human
    label — #176, where qtgui sent the whole "vid:pid — Vendor Product" string
    as the key and broke every display command."""
    from types import SimpleNamespace

    from trcc.core.models import Kind, Wire
    from trcc.ui.qtgui.device_picker import DevicePickerWidget

    # ``wire`` / ``kind`` are ENUMS on the real ``ProductInfo``, and the picker
    # now reads them through ``ListDevices``, which takes ``.value``.  The fake
    # carried a bare string and no wire at all — faithful to what the widget
    # touched at the time, and silently wrong the moment anything else looked.
    gui_app.devices["87ad:70db"] = SimpleNamespace(  # type: ignore[assignment]
        info=SimpleNamespace(
            vendor="Thermalright", product="Mjolnir Vision",
            wire=Wire.BULK, kind=Kind.LCD),
        is_connected=True,
    )
    picker = DevicePickerWidget(gui_app, _bus(gui_app))
    picker._populate_from_app()
    picker._combo.setCurrentIndex(0)   # SELECT the dropdown item — the bug path
    assert picker.current_key() == "87ad:70db"   # the KEY, not the label


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


def test_screencast_panel_start_casts_the_stored_region(cast_app: App) -> None:
    """No region to choose first: the device has one (the theme's, or the
    C#'s default), and Start casts it -- as every other UI's Start does."""
    from trcc.core.commands import ConnectDevice, LcdSnapshot
    from trcc.ui.qtgui.panels.screencast_panel import ScreencastPanel

    resp = bytearray(0xE100)
    resp[0] = 100                       # FBL=100 -> 320x320
    cast_app.platform.scsi.read_script.append(bytes(resp))   # type: ignore[attr-defined]
    assert cast_app.dispatch(ConnectDevice(key="0402:3922")).ok
    panel = ScreencastPanel(cast_app, _bus(cast_app))
    panel._picker.set_key("0402:3922")

    panel._on_start()

    rect = cast_app.dispatch(LcdSnapshot(key="0402:3922")).screencast_rect
    assert panel._casting is True, panel._status.text()
    assert cast_app.settings.for_device("0402:3922").screencast_region == (*rect, False)


def test_screencast_panel_start_without_key_is_a_no_op(
    gui_app: App, qapp: object,
) -> None:
    """Start without a chosen device surfaces guidance, starts no cast."""
    from trcc.ui.qtgui.panels.screencast_panel import ScreencastPanel

    panel = ScreencastPanel(gui_app, _bus(gui_app))
    panel._on_start()
    assert panel._casting is False
    assert "device" in panel._status.text().lower()


def test_screencast_panel_stores_the_picked_region(cast_app: App) -> None:
    """A drag is the device's region now -- fitted to the canvas (320x320
    here) and stored in the App, where every UI and a save read it."""
    from trcc.core.commands import LcdSnapshot

    panel = _casting_panel(cast_app)
    panel._on_region_selected(40, 60, 300, 240)

    assert cast_app.dispatch(LcdSnapshot(key="0402:3922")).screencast_rect == (
        40, 60, 300, 300)
    assert tuple(panel._fields[a].value() for a in "xywh") == (40, 60, 300, 300)


def test_screencast_panel_follows_another_uis_region(cast_app: App, qtbot) -> None:
    from trcc.core.commands import SetScreencastRegion

    panel = _casting_panel(cast_app)
    qtbot.addWidget(panel)
    cast_app.dispatch(SetScreencastRegion(key="0402:3922", x=9, y=8, w=64, h=64,
                                          hide_border=False))

    qtbot.waitUntil(
        lambda: tuple(panel._fields[a].value() for a in "xywh") == (9, 8, 64, 64),
        timeout=2000)
    assert panel._hide_border.isChecked() is False


def test_screencast_panel_without_a_device_stores_nothing(
    gui_app: App, qapp: object,
) -> None:
    from trcc.ui.qtgui.panels.screencast_panel import ScreencastPanel

    panel = ScreencastPanel(gui_app, _bus(gui_app))
    sent = _writes(panel)
    panel._on_region_selected(40, 60, 320, 240)

    assert sent == []
    assert "device" in panel._status.text().lower()


def test_screencast_panel_hide_border_is_the_apps_flag(cast_app: App) -> None:
    from trcc.core.commands import LcdSnapshot

    panel = _casting_panel(cast_app)
    assert panel._hide_border.isChecked() is True       # the C#'s default
    panel._hide_border.setChecked(False)

    snap = cast_app.dispatch(LcdSnapshot(key="0402:3922"))
    assert (snap.screencast_rect, snap.screencast_hide_border) == ((0, 0, 200, 200), False)


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


@pytest.fixture
def cast_app(fake_platform: FakePlatform, qapp: object) -> App:
    """``gui_app`` with a SYNCHRONOUS send scheduler.

    Not a style preference.  ``ConnectDevice`` registers the device with the
    scheduler, and the default ``ThreadSendScheduler`` then publishes
    ``FrameSent`` from its own thread — after the test has returned and Qt has
    collected the ``BusBridge`` behind the subscription.  The bus prints
    "EventBus handler failed for FrameSent / Signal source has been deleted"
    for every late frame, which is noise that would sit on top of any REAL
    failure in this file.  Measured: 0 such lines from the panels that never
    connect, 1 from each that does.
    """
    del qapp                                # explicit dependency, no use
    from trcc.adapters.infra.send_scheduler import SyncSendScheduler
    from trcc.adapters.render.qt import QtRenderer

    app = App(platform=fake_platform, renderer=QtRenderer(),
              send_scheduler=SyncSendScheduler())
    # And NEVER a real microphone: ``StartScreencast(audio=True)`` would open
    # an ``sd.InputStream`` that outlives the test.  See ``FakeMic``.
    app.audio = FakeMic()                   # type: ignore[assignment]
    return app


def _casting_panel(app: App):
    """A ScreencastPanel over a CONNECTED device, region already chosen.

    The panel's own guards return before dispatching without these two, so a
    test that skips them asserts against a Command that was never built.
    """
    from trcc.core.commands import ConnectDevice
    from trcc.ui.qtgui.panels.screencast_panel import ScreencastPanel

    resp = bytearray(0xE100)
    resp[0] = 100                       # FBL=100 -> 320x320
    app.platform.scsi.read_script.append(bytes(resp))   # type: ignore[attr-defined]
    assert app.dispatch(ConnectDevice(key="0402:3922")).ok

    panel = ScreencastPanel(app, _bus(app))
    panel._picker.set_key("0402:3922")
    panel._on_region_selected(0, 0, 200, 100)
    return panel


def _persisted_audio(app: App) -> bool | None:
    """The audio flag as the CAPTURE PATH reads it — region[4], not a spy."""
    region = app.settings.for_device("0402:3922").screencast_region
    return None if region is None else region[4]


def test_screencast_audio_checkbox_reaches_the_command(cast_app: App) -> None:
    """qtgui was the last face that could not set ``StartScreencast.audio``.

    gui has a mic button, the CLI has ``--audio``, the API has ``body.audio``,
    and ``grep -ri audio src/trcc/ui/qtgui/`` returned ZERO lines — so the
    field took its ``False`` default forever.  Asserted on the PERSISTED
    truth, which is what the capture path actually reads, not on the Command
    object: a kwarg that reaches a dataclass and no further is the shape
    ``test_command_field_reach`` already covers structurally.
    """
    panel = _casting_panel(cast_app)
    panel._audio.setChecked(True)
    panel._on_start()

    assert panel._casting is True, panel._status.text()
    assert _persisted_audio(cast_app) is True


def test_screencast_audio_unchecked_stays_off(cast_app: App) -> None:
    """The default, pinned — so the checkbox proves it is the thing deciding."""
    panel = _casting_panel(cast_app)
    assert panel._audio.isChecked() is False
    panel._on_start()

    assert panel._casting is True, panel._status.text()
    assert _persisted_audio(cast_app) is False


def test_screencast_audio_toggles_mid_cast(cast_app: App) -> None:
    """Toggling mid-cast re-issues the session, exactly as the FPS slider
    re-registers the driver — the flag lives in ``screencast_region``, so
    there is no second piece of state to keep in step."""
    panel = _casting_panel(cast_app)
    panel._on_start()
    assert _persisted_audio(cast_app) is False

    panel._audio.setChecked(True)           # fires toggled -> re-issue
    assert _persisted_audio(cast_app) is True

    panel._audio.setChecked(False)
    assert _persisted_audio(cast_app) is False


def test_screencast_audio_off_session_does_not_dispatch(
    gui_app: App, qapp: object,
) -> None:
    """With no live cast the checkbox is just a checkbox — it must not
    persist a region for a device that is not casting."""
    from trcc.ui.qtgui.panels.screencast_panel import ScreencastPanel

    panel = ScreencastPanel(gui_app, _bus(gui_app))
    panel._audio.setChecked(True)
    assert panel._casting is False
    assert _persisted_audio(gui_app) is None


def test_region_overlay_constructs(qapp: object) -> None:
    """RegionSelectOverlay constructs without raising — no screen grab yet."""
    from trcc.ui.screen_overlay import RegionSelectOverlay

    overlay = RegionSelectOverlay()
    assert hasattr(overlay, "region_selected")
    assert hasattr(overlay, "cancelled")


# =========================================================================
# Drag-select overlays — ONE interaction, proven on BOTH skins
#
# The press-drag-release behaviour lives once, in ``DragSelectOverlay``; its
# one subclass now is the shared ``RegionSelectOverlay`` (gui's
# ``ScreenCaptureOverlay`` was reachable from no button and was deleted
# 2026-10-01).
# Before that it was written twice and NOTHING drove it, which is how the two
# copies came to disagree on their own constants (``_MIN_SELECTION`` vs
# ``_MIN_EDGE``, same value) and their control flow.  These tests are
# parametrized over both skins so one shared body is proven for both.
# =========================================================================


#: Skin name -> the drag-select overlay it ships.  Resolved INSIDE the test,
#: never at decorator time: importing a Qt widget module during collection
#: runs its class bodies before ``QApplication`` exists.
_DRAG_SKINS = {
    "shared": ("trcc.ui.screen_overlay", "RegionSelectOverlay"),
}


def _drag_overlay(skin: str) -> type:
    from importlib import import_module

    module, name = _DRAG_SKINS[skin]
    return getattr(import_module(module), name)


def _mouse_event(kind: str, x: int, y: int, button: str, held: str):
    """Build a QMouseEvent at (x, y) — the non-deprecated QPointF overload."""
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    at = QPointF(x, y)
    return QMouseEvent(
        getattr(QMouseEvent.Type, kind), at, at,
        getattr(Qt.MouseButton, button), getattr(Qt.MouseButton, held),
        Qt.KeyboardModifier.NoModifier,
    )


def _point(x: int, y: int):
    from PySide6.QtCore import QPoint

    return QPoint(x, y)


def _press(overlay: object, x: int, y: int, button: str = "LeftButton") -> None:
    overlay.mousePressEvent(  # type: ignore[attr-defined]
        _mouse_event("MouseButtonPress", x, y, button, button))


def _move_to(overlay: object, x: int, y: int) -> None:
    overlay.mouseMoveEvent(  # type: ignore[attr-defined]
        _mouse_event("MouseMove", x, y, "NoButton", "LeftButton"))


def _release(overlay: object, x: int, y: int) -> None:
    overlay.mouseReleaseEvent(  # type: ignore[attr-defined]
        _mouse_event("MouseButtonRelease", x, y, "LeftButton", "NoButton"))


def _confirmed_rects(overlay: object) -> list[tuple[int, int, int, int]]:
    """Subscribe to whichever signal this skin confirms with."""
    seen: list[tuple[int, int, int, int]] = []

    def _on_region(x: int, y: int, w: int, h: int) -> None:
        seen.append((x, y, w, h))

    def _on_captured(pixmap: object) -> None:
        seen.append((0, 0, -1, -1) if pixmap is None else (0, 0, 1, 1))

    if hasattr(overlay, "region_selected"):
        overlay.region_selected.connect(_on_region)  # type: ignore[attr-defined]
    else:
        overlay.captured.connect(_on_captured)  # type: ignore[attr-defined]
    return seen


@pytest.mark.parametrize("skin", sorted(_DRAG_SKINS))
def test_drag_select_confirms_a_real_drag(skin: str, qtbot) -> None:
    """Press, drag, release over the minimum edge confirms the rectangle."""
    overlay = _drag_overlay(skin)()
    qtbot.addWidget(overlay)
    seen = _confirmed_rects(overlay)

    _press(overlay, 100, 120)
    _move_to(overlay, 300, 260)
    _release(overlay, 300, 260)

    assert len(seen) == 1
    if hasattr(overlay, "region_selected"):
        assert seen[0] == (100, 120, 201, 141)


@pytest.mark.parametrize("skin", sorted(_DRAG_SKINS))
def test_drag_select_ignores_a_misclick(skin: str, qtbot) -> None:
    """A drag shorter than ``_MIN_EDGE`` on either axis confirms nothing."""
    overlay = _drag_overlay(skin)()
    qtbot.addWidget(overlay)
    seen = _confirmed_rects(overlay)

    _press(overlay, 100, 120)
    _move_to(overlay, 105, 200)          # 5px wide — under the minimum
    _release(overlay, 105, 200)

    assert seen == []
    assert overlay._MIN_EDGE == 10       # the shared constant, one spelling


#: The same rectangle, dragged from each of its four corners.
_DIAGONALS = {
    "down-right": ((100, 120), (300, 260)),
    "up-left": ((300, 260), (100, 120)),
    "down-left": ((300, 120), (100, 260)),
    "up-right": ((100, 260), (300, 120)),
}


@pytest.mark.parametrize("skin", sorted(_DRAG_SKINS))
@pytest.mark.parametrize("gesture", sorted(_DIAGONALS))
def test_drag_select_is_direction_independent(skin: str, gesture: str,
                                              qtbot) -> None:
    """The same rectangle, whichever corner the drag started from.

    Qt's two-point ``QRect`` is INCLUSIVE of both corners, but
    ``normalized()`` repairs a negative extent by moving both edges inward.
    Both skins used to call it, so an up-left drag measured 199x139 at
    (101, 121) where the identical down-right drag measured 201x141 at
    (100, 120) — 2px smaller and 1px offset, decided by which way the hand
    moved.  Nothing drove the interaction, so nothing caught it.
    """
    (x0, y0), (x1, y1) = _DIAGONALS[gesture]
    overlay = _drag_overlay(skin)()
    qtbot.addWidget(overlay)
    seen = _confirmed_rects(overlay)

    _press(overlay, x0, y0)
    _move_to(overlay, x1, y1)
    _release(overlay, x1, y1)

    assert len(seen) == 1
    if hasattr(overlay, "region_selected"):
        assert seen[0] == (100, 120, 201, 141)


@pytest.mark.parametrize("skin", sorted(_DRAG_SKINS))
def test_drag_select_keeps_both_edge_pixels(skin: str, qtbot) -> None:
    """A drag that starts and ends on one pixel selects that one pixel.

    Pins the inclusive convention the sizes above rest on: 100 to 300 is
    201 columns because both ends are inside the selection, not 200.
    """
    overlay = _drag_overlay(skin)()
    qtbot.addWidget(overlay)
    overlay._start = overlay._end = _point(50, 50)
    assert overlay._selection_rect().width() == 1
    assert overlay._selection_rect().height() == 1


@pytest.mark.parametrize("skin", sorted(_DRAG_SKINS))
def test_drag_select_right_click_cancels(skin: str, qtbot) -> None:
    """Right-click routes to the skin's own cancel signal, not a selection."""
    cancelled: list[bool] = []
    overlay = _drag_overlay(skin)()
    qtbot.addWidget(overlay)
    if hasattr(overlay, "cancelled"):
        overlay.cancelled.connect(lambda: cancelled.append(True))
    else:
        overlay.captured.connect(lambda px: cancelled.append(px is None))

    _press(overlay, 100, 120, button="RightButton")

    assert cancelled == [True]


def test_both_skins_share_one_drag_implementation() -> None:
    """Neither skin may re-implement the interaction it inherits.

    The gate, not the comment: if a future edit copies press/drag/release
    back down into a skin, this fails and names the method.
    """
    from trcc.ui.screen_overlay import DragSelectOverlay

    shared = ("mousePressEvent", "mouseMoveEvent", "mouseReleaseEvent",
              "_selection_rect", "paintEvent", "_draw_size_label", "__init__")
    for skin in sorted(_DRAG_SKINS):
        overlay_cls = _drag_overlay(skin)
        assert issubclass(overlay_cls, DragSelectOverlay)
        redefined = [m for m in shared if m in overlay_cls.__dict__]
        assert not redefined, (
            f"{overlay_cls.__name__} re-implements {redefined}, which "
            f"DragSelectOverlay already owns for every skin."
        )
        # ...and each skin DOES say what its own rectangle means.
        assert "_confirm" in overlay_cls.__dict__
        assert "_emit_cancel" in overlay_cls.__dict__


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


def test_brightness_slider_debounces_to_one_send(qapp: object) -> None:
    """A brightness drag must coalesce into ONE ``brightness_changed`` emit,
    not one per slider tick — the per-tick flood interleaved with the
    segment-number refresh and glitched the display (#202)."""
    from PySide6.QtCore import QEventLoop, QTimer

    from trcc.ui.gui.assets import _PKG_ASSETS_DIR, set_assets_dir
    from trcc.ui.gui.uc_led_control import _BRIGHTNESS_DEBOUNCE_MS, UCLedControl

    set_assets_dir(_PKG_ASSETS_DIR)
    panel = UCLedControl()

    emitted: list[int] = []
    panel.brightness_changed.connect(emitted.append)

    # Simulate a drag — many rapid value changes within the debounce window.
    for value in (90, 80, 70, 60, 50, 42):
        panel._brightness_slider.setValue(value)

    # Nothing sent to the device yet: the slider is still "moving".
    assert emitted == []
    # The label, however, tracks the slider live.
    assert panel._brightness_label.text() == "42%"

    # Run the real event loop past the single-shot debounce so it fires once.
    loop = QEventLoop()
    QTimer.singleShot(_BRIGHTNESS_DEBOUNCE_MS + 200, loop.quit)
    loop.exec()

    assert emitted == [42], emitted
    panel.deleteLater()


def test_uc_device_spec_matches_the_layout_table() -> None:
    """The sidebar is built from a PanelSpec — its rects must stay Layout's.

    ``UCDevice._setup_ui`` no longer writes coordinates: it renders
    ``_SPEC_CHROME`` / ``_SPEC_CONTENT`` (``ui/presentation/panel_spec.py``),
    the panel stated as data.  This pins the spec to ``Layout`` so an edit to
    either cannot silently move a control.

    Deliberately Qt-FREE — it asserts the spec, not a built widget.  An
    earlier version constructed a second ``UCDevice`` and segfaulted the
    xdist worker intermittently (PySide6 does not enjoy repeated panel
    instantiation across a shared QApplication).  The rendered geometry is
    covered by ``dev/tools/panel_snapshot.py``, which compares real pixels;
    this covers the invariant that tool cannot see — that the coordinates
    came from the table rather than being retyped.
    """
    from trcc.ui.gui.constants import Layout
    from trcc.ui.gui.uc_device import _SPEC_CHROME, _SPEC_CONTENT

    expected = {
        "sensor_btn": Layout.SENSOR_BTN,
        "about_btn": Layout.ABOUT_BTN,
        "no_devices_label": Layout.NO_DEVICES_LABEL,
        "hint_label": Layout.HINT_LABEL,
    }
    declared = {
        c.id: c for c in (*_SPEC_CHROME.controls, *_SPEC_CONTENT.controls)
    }
    assert set(declared) == set(expected), (
        f"spec declares {sorted(declared)}, test pins {sorted(expected)} — a "
        f"control was added to the spec without being pinned here"
    )
    for name, rect in expected.items():
        assert declared[name].rect == rect, (
            f"{name} is not at its Layout entry — the spec and the "
            f"coordinate table have diverged"
        )

    # The empty-state labels belong INSIDE the scroll content; parented to
    # the panel they would float over the device list.
    assert declared["no_devices_label"].parent == "device_area"
    assert declared["hint_label"].parent == "device_area"

    # The backdrop is named, not a path — the spec layer must stay Qt-free.
    assert _SPEC_CHROME.background is not None
    assert _SPEC_CHROME.background.asset == "SIDEBAR_BG"


def test_uc_image_cut_spec_declares_every_toolbar_button() -> None:
    """The crop toolbar's five icon buttons, stated as data.

    Same invariant as the sidebar, on the second converted panel: the rects
    come from the module's BTN_* constants, so a retyped coordinate fails
    here rather than surfacing as a button in the wrong place.
    """
    from trcc.ui.gui import uc_image_cut as mod

    expected = {
        "btn_height_fit": mod.BTN_HEIGHT_FIT,
        "btn_width_fit": mod.BTN_WIDTH_FIT,
        "btn_rotate": mod.BTN_ROTATE,
        "btn_ok": mod.BTN_OK,
        "btn_close": mod.BTN_CLOSE,
    }
    declared = {c.id: c for c in mod._SPEC.controls}
    assert set(declared) == set(expected)
    for name, rect in expected.items():
        assert declared[name].rect == rect, f"{name} moved off its constant"
    # Every toolbar button carries a text fallback for a missing asset.
    assert all(c.fallback for c in mod._SPEC.controls)


def test_overlay_editor_does_not_reseed_an_emptied_layer(gui_app: App) -> None:
    """Deleting the last element must not bring the theme's elements back.

    The editor adopts the active layout into the editable user layer when the
    device has none of its own.  It used to decide that with ``if not
    settings.user_overlay_elements`` — a TRUTHINESS test, which cannot tell
    "no layer" from "the user emptied it", so refreshing after deleting the
    last element re-seeded it from the theme.  That is #276's second symptom
    in the reporter's words: "whichever one I delete LAST still appears."

    The fix is STRUCTURAL, not a better guard, and this test documents the
    behaviour rather than gating the condition.  Measured: flipping the guard
    back to ``if not layout.elements`` does NOT reproduce the bug, because the
    theme elements are no longer reachable from here — the editor used to
    resolve the layout itself via ``resolve_overlay_elements(theme_config,
    [])``, whose own truthiness fell through to the theme.  ``ResolveOverlay``
    returns ``[]`` for an emptied layer, so there is nothing left to seed
    FROM.  The ``source != "user"`` condition is belt-and-braces on top.

    What DOES gate the underlying distinction is
    ``test_resolve_overlay.py::test_an_emptied_user_layer_reports_user_not_theme``.
    """
    from trcc.core.models import Theme
    from trcc.ui.qtgui.panels.overlay_editor import OverlayEditorPanel

    key = next(iter(gui_app.devices), None) or "0402:3922"
    gui_app.active_themes[key] = Theme(
        path=Path("/nonexistent"), name="Theme1", resolution=(320, 320),
        config={"elements": [{"type": "text", "text": "from-theme"}]},
    )
    # The user deleted every element: an EMPTY layer, not an absent one.
    gui_app.settings.for_device(key).user_overlay_elements = []

    panel = OverlayEditorPanel(gui_app, _bus(gui_app))
    panel._picker.set_key(key)
    panel.refresh()

    assert gui_app.settings.for_device(key).user_overlay_elements == [], (
        "the emptied user layer was re-seeded from the theme — the element "
        "the user deleted has come back"
    )
    assert panel._list.count() == 0


# =========================================================================
# Screencast aspect lock — a BEHAVIOUR gate, not a construction smoke
#
# This layer is verified to build, not to compute, and that is exactly how
# ``_on_coord_changed`` shipped with its multiply and divide the wrong way
# round: on an 854x480 panel a width of 201 locked the height to 357 where
# 113 is correct, and square panels were skipped outright by a
# ``ratio != 1.0`` guard so they never locked at all.  ruff, pyright, the
# logging ratchet and 4882 tests were all green on it, because none of them
# look at what a widget COMPUTES.
#
# Every panel geometry the device catalog can produce is covered, so a panel
# added to the catalog without a matching ratio cannot regress silently —
# which is the other half of the same bug: the ratio used to come from a
# hardcoded table that had drifted, missing 640x172 entirely and defaulting
# it to 0.75 where 0.2687 is correct.
# =========================================================================


def _catalog_geometries() -> list[tuple[int, int]]:
    """Every distinct panel resolution the app can meet, from the registry."""
    from trcc.core.protocol import FBL_PROFILES

    return sorted({(p.width, p.height) for p in FBL_PROFILES.values()})


@pytest.mark.parametrize("panel", _catalog_geometries(),
                         ids=lambda wh: f"{wh[0]}x{wh[1]}")
def test_screencast_aspect_lock_matches_the_panel(panel: tuple[int, int],
                                                  qtbot) -> None:
    """Typing a width locks the height to the panel's own aspect, both ways.

    The ratio is ``height / width``, so ``height = width * ratio`` and
    ``width = height / ratio``.  Asserted against the panel geometry itself,
    never a table — a table is what drifted.
    """
    from trcc.ui.gui.display_mode_panels import ScreenCastPanel

    width, height = panel
    ratio = height / width

    typed_w = ScreenCastPanel()
    qtbot.addWidget(typed_w)
    typed_w.set_resolution(width, height)
    typed_w.entry_w.setText("200")
    assert typed_w.entry_h.text() == str(round(200 * ratio))

    typed_h = ScreenCastPanel()
    qtbot.addWidget(typed_h)
    typed_h.set_resolution(width, height)
    typed_h.entry_h.setText("200")
    assert typed_h.entry_w.text() == str(round(200 / ratio))


@pytest.mark.parametrize("panel", _catalog_geometries(),
                         ids=lambda wh: f"{wh[0]}x{wh[1]}")
def test_screencast_plus_button_keeps_the_region_on_aspect(
    panel: tuple[int, int], qtbot,
) -> None:
    """The gesture a user actually makes: nudge the width with ``+``.

    Drives the button's own handler rather than setting text, because that is
    the path a click takes and it is where the emitted params come from.
    """
    from trcc.ui.gui.display_mode_panels import ScreenCastPanel

    width, height = panel
    emitted: list[tuple[int, int, int, int]] = []
    scp = ScreenCastPanel()
    qtbot.addWidget(scp)
    scp.set_resolution(width, height)
    scp.region_edited.connect(
        lambda x, y, w, h: emitted.append((x, y, w, h)),
    )
    scp.set_values(x=100, y=100, w=200, h=round(200 * height / width))

    scp._increment(scp.entry_w, +1)

    assert emitted, "nudging the width emitted nothing"
    _, _, got_w, got_h = emitted[-1]
    assert got_w == 201
    assert got_h == round(201 * height / width)


def test_screencast_does_not_lock_before_a_device_is_known(qtbot) -> None:
    """With no resolution set the lock stays out of the way.

    A width typed before any device is attached must not silently write a
    height computed from somebody else's panel.  The decision is
    ``lock_region_to_panel``'s (``None`` = nobody has told us yet), shared
    with qtgui; this panel used to keep its own ratio method for it.
    """
    from trcc.ui.gui.display_mode_panels import ScreenCastPanel

    scp = ScreenCastPanel()
    qtbot.addWidget(scp)
    scp.entry_w.setText("200")
    assert scp.entry_h.text() == "0"      # untouched, not invented

    # A device that answers nonsense is the THIRD state and is not the same
    # as never having asked: it warns, and still declines to lock.
    scp.set_resolution(0, 0)
    scp.entry_w.setText("300")
    assert scp.entry_h.text() == "0"


def test_screencast_region_is_sent_when_finished_not_per_keystroke(qtbot) -> None:
    """Typing "320" is not three regions; Enter or focus leaving sends one,
    and a focus change with nothing edited sends none."""
    from trcc.ui.gui.display_mode_panels import ScreenCastPanel

    emitted: list[tuple[int, int, int, int]] = []
    scp = ScreenCastPanel()
    qtbot.addWidget(scp)
    scp.set_values(x=0, y=0, w=320, h=240)
    scp.region_edited.connect(lambda *r: emitted.append(r))

    for typed in ("1", "12", "123"):
        scp.entry_x.setText(typed)
    assert emitted == []
    scp.entry_x.editingFinished.emit()
    scp.entry_x.editingFinished.emit()          # focus moved, no new edit

    assert emitted == [(123, 0, 320, 240)]


def test_showing_a_region_is_not_an_edit(qtbot) -> None:
    from trcc.ui.gui.display_mode_panels import ScreenCastPanel

    emitted: list[tuple[int, ...]] = []
    scp = ScreenCastPanel()
    qtbot.addWidget(scp)
    scp.region_edited.connect(lambda *r: emitted.append(r))

    scp.set_values(x=5, y=6, w=320, h=240)
    scp.entry_w.editingFinished.emit()

    assert emitted == []


def test_the_border_button_toggles_hide_and_showing_it_sends_nothing(qtbot) -> None:
    """The C#'s myYcbk starts True (hidden); a click flips it."""
    from trcc.ui.gui.display_mode_panels import ScreenCastPanel

    toggled: list[bool] = []
    scp = ScreenCastPanel()
    qtbot.addWidget(scp)
    scp.border_toggled.connect(toggled.append)

    scp.set_hide_border(False)
    scp.border_btn.click()

    assert toggled == [True]


# =========================================================================
# ConfigurationPanel — the slideshow has to actually ROTATE
# =========================================================================


def _slideshow_panel(gui_app: App, qtbot):
    from trcc.ui.qtgui.panels.configuration_panel import ConfigurationPanel

    panel = ConfigurationPanel(gui_app, _bus(gui_app))
    qtbot.addWidget(panel)                    # unparented top-levels leak
    panel._key = lambda: "0402:3922"          # pyright: ignore[reportAttributeAccessIssue]
    panel._picker.current_key = lambda: "0402:3922"   # pyright: ignore[reportAttributeAccessIssue]
    return panel


def test_the_slideshow_switch_is_one_command(gui_app: App, qtbot) -> None:
    """The switch sends ``SetSlideshow`` alone -- it is what rotates, so the
    panel pairs no driver Command by hand -- and Save sends the list alone."""
    panel = _slideshow_panel(gui_app, qtbot)
    sent: list[str] = []
    real = panel.dispatch

    def spy(cmd):
        sent.append(type(cmd).__name__)
        return real(cmd)

    panel.dispatch = spy                      # pyright: ignore[reportAttributeAccessIssue]
    on = panel._slideshow_enabled.findData(True)
    panel._slideshow_enabled.setCurrentIndex(on)
    panel._slideshow_enabled.activated.emit(on)          # the user's pick
    assert sent == ["SetSlideshow"], sent

    sent.clear()
    panel._slideshow_themes.setPlainText("A\nB")
    panel._slideshow_save.click()
    assert sent == ["ConfigureSlideshow"], sent


def test_the_slideshow_controls_follow_another_ui(gui_app: App, qtbot) -> None:
    """Switched on from the CLI, or off by the App when another source took
    the panel: the controls show it without a "Load" click.

    MUTATION CHECK: drop the ``slideshow_changed`` connection.
    """
    from trcc.core.events import SlideshowChanged

    panel = _slideshow_panel(gui_app, qtbot)
    gui_app.events.publish(SlideshowChanged(
        key="0402:3922", enabled=True, interval_s=42.0, themes=("A", "B")))
    qtbot.waitUntil(lambda: panel._slideshow_enabled.currentData() is True)

    assert panel._slideshow_interval.value() == 42.0
    assert panel._slideshow_themes.toPlainText() == "A\nB"


# =========================================================================
# SensorsBox / MaintenanceBox — the surfacings cli/api had and qtgui did not
# =========================================================================
#
# These build the BOX the behaviour belongs to.  Spying on ``SystemPanel``
# would observe nothing: each box dispatches through ITSELF, so a spy on the
# host silently stops intercepting.  The fix is to re-point at the box, never
# to add forwarding properties — that re-grows the god class by delegation.


def _sensors_box(gui_app: App, qtbot):
    from trcc.ui.qtgui.panels.system import SensorsBox

    box = SensorsBox(gui_app, _bus(gui_app))
    qtbot.addWidget(box)
    return box


def test_sensors_box_lists_memory_slots(gui_app: App, qtbot) -> None:
    """Building the box populates the DRAM list, or says nothing was found.

    An empty widget is not proof of anything -- the list must say WHICH, so a
    host with no SPD data reads as "reported nothing" rather than as a box
    that forgot to load.
    """
    box = _sensors_box(gui_app, qtbot)

    assert box._memory.count() >= 1, (
        "memory list was never populated — refresh_memory is not being called"
    )


def test_the_hdd_toggle_shows_the_persisted_flag(gui_app: App, qtbot) -> None:
    """And loading it must NOT write it back.

    ``setChecked`` emits ``toggled``; an unguarded load dispatches a write on
    every refresh, so the setting becomes whatever the UI rendered rather than
    what the user chose.
    """
    gui_app.settings.set_hdd_enabled(False)
    box = _sensors_box(gui_app, qtbot)
    assert box._hdd_check.isChecked() is False

    # Asserting the VALUE after a refresh cannot see this bug: the write-back
    # writes the value we just set, so it agrees with the expectation.  What
    # has to be observed is that a WRITE was attempted at all.
    gui_app.settings.set_hdd_enabled(True)
    sent: list[str] = []
    real = box.dispatch

    def spy(cmd):
        sent.append(type(cmd).__name__)
        return real(cmd)

    box.dispatch = spy                        # pyright: ignore[reportAttributeAccessIssue]
    box.refresh_hdd()

    assert box._hdd_check.isChecked() is True
    assert "SetHddEnabled" not in sent, (
        "refreshing the widget DISPATCHED a write — blockSignals is missing, "
        "so the setting becomes whatever the UI rendered"
    )


def test_toggling_hdd_persists_it(gui_app: App, qtbot) -> None:
    box = _sensors_box(gui_app, qtbot)

    box._hdd_check.setChecked(not box._hdd_check.isChecked())

    assert gui_app.settings.app.hdd_enabled is box._hdd_check.isChecked()


def _drive_upgrade(gui_app: App, qtbot, monkeypatch, *, confirm: bool):
    """Press Upgrade with the confirmation answered *confirm*.

    ``hasattr(box, "_upgrade_btn")`` was the first version of this and it is
    worthless: it proves a button exists, not that the button is safe.  The
    safety-critical behaviour is that NOTHING runs until the user agrees --
    ``RunUpgrade(dry_run=False)`` spawns a package-manager subprocess under
    sudo.
    """
    from PySide6.QtWidgets import QMessageBox

    box = _maintenance_box(gui_app, qtbot)
    # A host with no package manager makes the DRY RUN fail, and the box
    # correctly bails before confirming -- which is right, and would leave the
    # confirm path untested everywhere.  Give it a manager so the gate can
    # reach the branch it exists to guard.
    monkeypatch.setattr(gui_app.diagnostics, "package_manager",
                        lambda: "pacman")
    monkeypatch.setattr(gui_app.platform, "upgrade_command",
                        lambda: ["true", "-upgrade"])
    answer = (QMessageBox.StandardButton.Yes if confirm
              else QMessageBox.StandardButton.No)
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: answer))
    sent: list[bool] = []
    real = box.dispatch

    def spy(cmd):
        if type(cmd).__name__ == "RunUpgrade":
            sent.append(cmd.dry_run)
        return real(cmd)

    box.dispatch = spy                        # pyright: ignore[reportAttributeAccessIssue]
    box._on_upgrade()
    return sent


def test_declining_the_upgrade_runs_nothing(
    gui_app: App, qtbot, monkeypatch,
) -> None:
    sent = _drive_upgrade(gui_app, qtbot, monkeypatch, confirm=False)

    assert sent == [True], (
        "saying No must leave ONLY the dry run — a False in this list is a "
        f"sudo subprocess the user declined, got {sent}"
    )


def test_confirming_the_upgrade_runs_it(
    gui_app: App, qtbot, monkeypatch,
) -> None:
    sent = _drive_upgrade(gui_app, qtbot, monkeypatch, confirm=True)

    assert sent == [True, False], (
        f"expected a dry run, then the real one; got {sent}"
    )


def test_the_confirmation_quotes_the_command_that_will_run(
    gui_app: App, qtbot,
) -> None:
    """The dialog must show the REAL argv, not a UI's guess at it.

    ``RunUpgrade(dry_run=True)`` exists for exactly this: it returns
    ``message="Would run: ..."`` plus the ``command`` list, so the text the
    user approves is the text the Command would execute.
    """
    box = _maintenance_box(gui_app, qtbot)
    from trcc.core.commands import RunUpgrade

    preview = box.dispatch(RunUpgrade(dry_run=True))

    if preview.ok:
        assert preview.command, "dry run reported ok with no command to show"
        assert " ".join(preview.command) in preview.message
    else:
        assert preview.message, "a refusal must say why, it is shown to the user"


def test_the_date_format_reaches_the_bus(gui_app: App, qtbot) -> None:
    """qtgui could set the CLOCK format but not the DATE pattern.

    Editable on purpose: the vocabulary is four tokens (``yyyy`` ``yy`` ``MM``
    ``dd`` -- ``services/_clock._PATTERN_RULES``) with any separator passed
    through, so a fixed list would cap what the CLI already accepts.  The gate
    therefore drives a TYPED pattern, not a preset, because ``currentData()``
    is ``None`` for anything the user types.
    """
    from trcc.ui.qtgui.panels.configuration_panel import ConfigurationPanel

    panel = ConfigurationPanel(gui_app, _bus(gui_app))
    qtbot.addWidget(panel)
    panel._picker.current_key = lambda: "0402:3922"   # pyright: ignore[reportAttributeAccessIssue]

    sent: list[tuple[str, str, str | None]] = []
    real = panel.dispatch

    def spy(cmd):
        if type(cmd).__name__ == "SetDateFormat":
            sent.append((type(cmd).__name__, cmd.fmt, cmd.key))
        return real(cmd)

    panel.dispatch = spy                      # pyright: ignore[reportAttributeAccessIssue]
    panel._date.setCurrentText("dd.MM.yyyy")
    panel._date.lineEdit().editingFinished.emit()        # the user is done
    panel._date.lineEdit().editingFinished.emit()        # a focus change

    assert sent == [("SetDateFormat", "dd.MM.yyyy", "0402:3922")], (
        f"the typed pattern did not reach the bus intact: {sent}"
    )


def _zone_tab_with(gui_app: App, qtbot, *, zones: int, mask=()):
    from trcc.core.results import LedZoneEntry
    from trcc.ui.qtgui.panels.led import ZoneTab

    tab = ZoneTab(gui_app, _led_key)
    qtbot.addWidget(tab)
    tab.refresh_from(_snap(
        zones=tuple(LedZoneEntry(color=(255, 0, 0)) for _ in range(zones)),
        zone_sync_zones=mask,
    ))
    return tab


def test_the_carousel_says_which_zones_it_visits(gui_app: App, qtbot) -> None:
    """One checkbox per zone, reflecting the persisted mask.

    Without this mask the carousel stays empty and ``next_sync_zone`` is stuck
    on page 0 — it never advances however the user sets the enable switch, so
    the feature looks present and does nothing.
    """
    tab = _zone_tab_with(gui_app, qtbot, zones=3, mask=(True, False, True))

    assert len(tab._participation_checks) == 3
    assert [b.isChecked() for b in tab._participation_checks] == [True, False, True]


@pytest.mark.parametrize(("mask", "shown"), [
    ((), [True, False, False]),                 # never configured
    ((False, True), [False, True, False]),      # shorter than the zones
    ((False, False, False), [True, False, False]),
])
def test_the_participation_boxes_show_the_zones_the_app_uses(
    gui_app: App, qtbot, mask: tuple, shown: list,
) -> None:
    """A missing entry is off, and with none on the App uses zone 0 -- for
    edits (``_edit_zones``) and for the carousel (``next_sync_zone`` returns 0
    for an empty mask, so it never cycles).  The C# starts the same way
    (``LunBo1`` only); the gui shows the same.

    This used to show an empty mask as EVERY zone participating, promising a
    carousel the App does not run.

    MUTATION CHECK -- MEASURED 2026-10-02: restore the old line (a missing
    entry shows ON, an all-off mask shows nothing) → all three cases fail.
    """
    tab = _zone_tab_with(gui_app, qtbot, zones=3, mask=mask)

    assert [b.isChecked() for b in tab._participation_checks] == shown


def test_toggling_participation_sends_the_whole_mask(gui_app: App, qtbot) -> None:
    """``SetLedZoneSyncZones`` replaces the mask wholesale, so send all of it."""
    tab = _zone_tab_with(gui_app, qtbot, zones=3, mask=(True, True, True))
    sent: list[tuple] = []
    real = tab._dispatch

    def spy(cmd):
        if type(cmd).__name__ == "SetLedZoneSyncZones":
            sent.append(cmd.zones)
        return real(cmd)

    tab._dispatch = spy                       # pyright: ignore[reportAttributeAccessIssue]
    tab._participation_checks[1].setChecked(False)

    assert sent == [(True, False, True)], f"got {sent}"


# =========================================================================
# DisplayPanel — video position, which Play/Pause/Stop never showed
# =========================================================================


def _display_panel(gui_app: App, qtbot):
    from trcc.ui.qtgui.panels.display_panel import DisplayPanel

    panel = DisplayPanel(gui_app, _bus(gui_app))
    qtbot.addWidget(panel)
    panel._require_key = lambda: "0402:3922"   # pyright: ignore[reportAttributeAccessIssue]
    return panel


def _stub_status(panel, *, cursor, frame_count, fps=30):
    from trcc.core.results import VideoStatusResult

    real = panel.dispatch
    sent: list = []

    def spy(cmd):
        name = type(cmd).__name__
        sent.append(cmd)
        if name == "VideoStatus":
            return VideoStatusResult(
                ok=True, key="0402:3922", playing=True,
                cursor=cursor, frame_count=frame_count, fps=fps,
            )
        return real(cmd)

    panel.dispatch = spy                       # pyright: ignore[reportAttributeAccessIssue]
    return sent


def test_a_video_stopped_elsewhere_disables_the_scrubber(gui_app: App, qtbot) -> None:
    """The slider followed frame advances only, so a video stopped from any
    UI left it live at its last frame.  Start and stop are followed now, for
    the selected device and no other."""
    from trcc.core.events import VideoStarted, VideoStopped

    panel = _display_panel(gui_app, qtbot)
    panel._picker.current_key = lambda: "0402:3922"  # pyright: ignore[reportAttributeAccessIssue]
    _stub_status(panel, cursor=4, frame_count=30)
    gui_app.events.publish(VideoStarted(key="0402:3922", path="/v.mp4",
                                        interval_ms=33, frame_count=30))
    qtbot.waitUntil(panel._seek.isEnabled, timeout=1000)

    _stub_status(panel, cursor=0, frame_count=0)       # no playback any more
    gui_app.events.publish(VideoStopped(key="87ad:70db"))   # another device
    qtbot.wait(100)
    assert panel._seek.isEnabled(), "another device's stop reached this one"

    gui_app.events.publish(VideoStopped(key="0402:3922"))
    qtbot.waitUntil(lambda: not panel._seek.isEnabled(), timeout=1000)


def test_no_playback_disables_the_scrubber(gui_app: App, qtbot) -> None:
    """Absence is a normal answer, not a failure.

    A device with no video answers ``ok=True, playing=False`` with the optional
    fields ``None``.  Showing that as a slider parked at 0 would claim there is
    a video sitting on its first frame.
    """
    panel = _display_panel(gui_app, qtbot)
    _stub_status(panel, cursor=None, frame_count=None)

    panel._refresh_video_status()

    assert panel._seek.isEnabled() is False
    assert "no video" in panel._seek_label.text()


def test_the_scrubber_shows_the_position(gui_app: App, qtbot) -> None:
    panel = _display_panel(gui_app, qtbot)
    _stub_status(panel, cursor=42, frame_count=300)

    panel._refresh_video_status()

    assert panel._seek.isEnabled() is True
    assert panel._seek.value() == 42
    assert panel._seek.maximum() == 299          # 0-based cursor
    assert panel._seek_label.text() == "00:00:01.400/00:00:10.000"   # 42 @ 30 fps


def test_dragging_the_scrubber_moves_only_the_label(gui_app: App, qtbot) -> None:
    """As the C# player's MouseMove: the label shows where a release lands."""
    panel = _display_panel(gui_app, qtbot)
    sent = _stub_status(panel, cursor=42, frame_count=300)
    panel._refresh_video_status()
    sent.clear()

    panel._seek.sliderMoved.emit(90)

    assert panel._seek_label.text() == "00:00:03.000/00:00:10.000"
    assert [type(c).__name__ for c in sent] == [], "a drag dispatched something"


def test_releasing_the_scrubber_seeks(gui_app: App, qtbot) -> None:
    """On RELEASE, not per drag step — each seek queues a rebuild."""
    panel = _display_panel(gui_app, qtbot)
    sent = _stub_status(panel, cursor=10, frame_count=300)
    panel._refresh_video_status()
    panel._seek.setValue(120)
    sent.clear()

    panel._on_seek_released()

    seeks = [c for c in sent if type(c).__name__ == "SeekVideo"]
    assert len(seeks) == 1, f"expected exactly one seek, got {len(seeks)}"
    assert seeks[0].frame == 120


# =========================================================================
# DisplayPanel — the still-image background qtgui could not set
# =========================================================================


def test_setting_a_background_dispatches_SetBackground_not_LoadImage(
    gui_app: App, qtbot, monkeypatch, tmp_path,
) -> None:
    """qtgui could override a background with VIDEO but not a STILL IMAGE.

    The distinction is the whole point and it is easy to get wrong:

    * ``SetBackground`` writes ``DeviceSettings.background_path``, which the
      renderer consults BEFORE the theme's own background — the theme and its
      overlays survive, only the picture changes.
    * ``LoadImage`` REPLACES the theme, materialising a one-file theme under
      ``user_content_dir/single-image/``.  The overlays go with it.

    So asserting "an image reached the bus" would pass on the wrong Command.
    This pins WHICH one, which is the bug that was fixed.
    """
    from PySide6.QtWidgets import QFileDialog

    from trcc.core.commands import LoadImage, SetBackground

    panel = _display_panel(gui_app, qtbot)
    picked = tmp_path / "wallpaper.png"
    picked.write_bytes(b"\x89PNG\r\n\x1a\n")
    monkeypatch.setattr(
        QFileDialog, "getOpenFileName",
        staticmethod(lambda *a, **k: (str(picked), "")),
    )

    sent: list[object] = []
    real = panel.dispatch
    monkeypatch.setattr(
        panel, "dispatch",
        lambda cmd: (sent.append(cmd), real(cmd))[1],
    )

    panel._background_btn.click()

    kinds = [type(c) for c in sent]
    assert SetBackground in kinds, (
        "the background button did not dispatch SetBackground"
    )
    assert LoadImage not in kinds, (
        "dispatched LoadImage — that REPLACES the theme and loses its overlays"
    )
    cmd = next(c for c in sent if isinstance(c, SetBackground))
    assert cmd.key == "0402:3922"
    assert cmd.path == picked


def test_the_background_dialog_offers_the_catalog_image_formats(
    gui_app: App, qtbot, monkeypatch, tmp_path,
) -> None:
    """The filter comes from ``MEDIA``, not a hand-typed glob.

    ``MediaCatalog`` exists because the answer to "is this a still image?" was
    spelled out in nine places and had already drifted — one dialog omitted
    ``.webp`` while the validator accepted it, so a loadable file could not be
    picked.  A dialog that re-types the list re-opens that gap.
    """
    from PySide6.QtWidgets import QFileDialog

    from trcc.core.models import MEDIA, MediaKind

    panel = _display_panel(gui_app, qtbot)
    seen: list[str] = []

    def _capture(*args, **_kwargs):
        seen.append(args[3] if len(args) > 3 else "")
        return ("", "")

    monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(_capture))
    panel._background_btn.click()

    assert seen, "the dialog was never opened"
    for pattern in MEDIA.patterns(MediaKind.IMAGE).split():
        assert pattern in seen[0], (
            f"{pattern} is in the catalog but not offered by the dialog"
        )


# =========================================================================
# DashboardBox — the sensor-dashboard editor
# =========================================================================


def _dashboard_box(gui_app: App, qtbot):
    from trcc.ui.qtgui.panels.system import DashboardBox

    box = DashboardBox(gui_app, _bus(gui_app))
    qtbot.addWidget(box)
    return box


def test_the_dashboard_shows_every_panel_and_row(gui_app: App, qtbot) -> None:
    """Rows carry their (panel, row) address, so a rebind knows what it edits."""
    box = _dashboard_box(gui_app, qtbot)

    assert box._tree.topLevelItemCount() == len(box._panels)
    if box._panels:
        top = box._tree.topLevelItem(0)
        assert top is not None
        assert top.childCount() == len(box._panels[0].sensors)


def test_an_unbound_row_says_so(gui_app: App, qtbot) -> None:
    """``sensor_id=""`` is how the user says "nothing here".

    Rendering it as an empty cell is indistinguishable from a column that
    failed to populate.
    """
    from trcc.core.models import PanelConfig, SensorBinding

    box = _dashboard_box(gui_app, qtbot)
    box._panels = [PanelConfig(
        category_id=0, name="Test",
        sensors=[SensorBinding(label="Row", sensor_id="", unit="")],
    )]
    box._redraw()        # draw the WORKING layout — no bus round-trip

    child = box._tree.topLevelItem(0).child(0)     # pyright: ignore[reportOptionalMemberAccess]
    assert child is not None
    assert "unbound" in child.text(1)


def _select_panel_header(box, index: int) -> None:
    box._tree.setCurrentItem(box._tree.topLevelItem(index))


def _select_row(box, panel: int, row: int) -> None:
    box._tree.setCurrentItem(box._tree.topLevelItem(panel).child(row))


def test_a_new_panel_is_the_models_definition_of_one(gui_app: App, qtbot) -> None:
    """The ONE policy, pinned — not gui's literal and not qtgui's.

    "A new panel is category 0, named Custom, with four unbound rows" is
    domain policy.  It lived as a literal in ``ui/gui/uc_system_info.py``
    while gui was the only face that could add one; this asserts the faces
    now agree because they call the same factory, not because two literals
    happen to match today.
    """
    from trcc.core.models import PanelConfig

    box = _dashboard_box(gui_app, qtbot)
    before = len(box._panels)
    box._on_add_panel()

    added = box._panels[-1]
    assert len(box._panels) == before + 1
    assert added == PanelConfig.custom()
    assert added.category_id == PanelConfig.CUSTOM_CATEGORY
    assert [b.label for b in added.sensors] == [
        "Sensor 1", "Sensor 2", "Sensor 3", "Sensor 4",
    ]
    assert all(b.sensor_id == "" for b in added.sensors)


def test_adding_a_panel_shows_it_without_persisting_it(
    gui_app: App, qtbot,
) -> None:
    """Visible immediately, saved only on Save — the same contract as a bind.

    This is why ``refresh`` was split: it re-reads the bus, so using it to
    show the new panel would discard the very edit being drawn.
    """
    from trcc.core.commands import GetSensorDashboard

    box = _dashboard_box(gui_app, qtbot)
    persisted_before = len(gui_app.dispatch(GetSensorDashboard()).panels)

    box._on_add_panel()

    assert box._tree.topLevelItemCount() == len(box._panels)
    assert box._tree.topLevelItem(len(box._panels) - 1).text(0) == "Custom"
    assert len(gui_app.dispatch(GetSensorDashboard()).panels) == persisted_before
    assert "unsaved" in box._status.text()

    box._on_save()
    assert len(gui_app.dispatch(GetSensorDashboard()).panels) == persisted_before + 1


def test_deleting_resolves_a_selected_ROW_to_its_panel(
    gui_app: App, qtbot,
) -> None:
    """Select a row, delete the panel it belongs to.

    Requiring the header to be selected would be a second rule to learn.
    Selecting a ROW is the case that distinguishes ``_selected_panel`` from
    ``_selected_binding``; a test that selected the header would pass against
    either.
    """
    box = _dashboard_box(gui_app, qtbot)
    box._on_add_panel()
    target = len(box._panels) - 1
    _select_row(box, target, 2)

    assert box._selected_binding() == (target, 2)
    assert box._selected_panel() == target

    box._on_delete_panel()
    assert len(box._panels) == target
    assert "Custom" not in [p.name for p in box._panels]


def test_deleting_the_last_panel_is_refused(gui_app: App, qtbot) -> None:
    """``SetSensorDashboard`` refuses an empty layout — say so before saving.

    An empty layout makes the next read fall back to defaults: a wipe dressed
    up as a write.  Surfacing it at delete time beats a refusal at save time,
    by which point the user has lost the panel from the tree.
    """
    box = _dashboard_box(gui_app, qtbot)
    box._panels = box._panels[:1]
    box._redraw()
    # _redraw CLEARS the selection, so without this the "nothing selected"
    # branch fires first and the guard goes untested — it did, once.
    _select_panel_header(box, 0)
    assert box._selected_panel() == 0

    box._on_delete_panel()

    assert len(box._panels) == 1
    assert "last panel" in box._status.text()


def test_renaming_a_panel_takes_the_dialogs_answer(
    gui_app: App, qtbot, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """And a cancel or a blank name changes nothing."""
    from trcc.ui.qtgui.panels.system import dashboard_box as mod

    box = _dashboard_box(gui_app, qtbot)

    def rename_with(value: str, ok: bool) -> None:
        """Answer the dialog with *value* and re-select FIRST.

        A successful rename calls ``_redraw``, which clears the tree and with
        it the selection — so without re-selecting, every attempt after the
        first returns early on "nothing selected" and the cancel/blank guards
        below assert nothing.  They did: mutation D (dropping ``not ok or``)
        passed against the first version of this test.
        """
        _select_panel_header(box, 0)
        assert box._selected_panel() == 0
        monkeypatch.setattr(mod.QInputDialog, "getText",
                            staticmethod(lambda *a, **k: (value, ok)))
        box._on_rename_panel()

    rename_with("  Water loop  ", True)     # accepted, and stripped
    assert box._panels[0].name == "Water loop"
    assert box._tree.topLevelItem(0).text(0) == "Water loop"

    rename_with("Discarded", False)         # cancelled — ok=False
    assert box._panels[0].name == "Water loop"

    rename_with("   ", True)                # accepted but blank
    assert box._panels[0].name == "Water loop"


def test_panel_verbs_refuse_politely_with_no_selection(
    gui_app: App, qtbot,
) -> None:
    """No selection is a message, never a crash or a silent no-op."""
    box = _dashboard_box(gui_app, qtbot)
    box._tree.clearSelection()
    box._tree.setCurrentItem(None)
    assert box._selected_panel() is None

    before = [p.name for p in box._panels]
    box._on_delete_panel()
    assert box._panels == box._panels and [p.name for p in box._panels] == before
    assert "Select a panel" in box._status.text()

    box._on_rename_panel()
    assert [p.name for p in box._panels] == before
    assert "Select a panel" in box._status.text()


def test_saving_sends_the_whole_layout(gui_app: App, qtbot) -> None:
    """One bulk verb, matching SetOverlayConfig — the UI holds the whole thing."""
    box = _dashboard_box(gui_app, qtbot)
    sent: list = []
    real = box.dispatch

    def spy(cmd):
        if type(cmd).__name__ == "SetSensorDashboard":
            sent.append(cmd.panels)
        return real(cmd)

    box.dispatch = spy                         # pyright: ignore[reportAttributeAccessIssue]
    box._on_save()

    assert len(sent) == 1
    assert len(sent[0]) == len(box._panels)


def test_binding_refuses_a_panel_heading(gui_app: App, qtbot) -> None:
    """A heading has no row address — binding to it would edit row 0 silently."""
    box = _dashboard_box(gui_app, qtbot)
    if box._tree.topLevelItemCount() == 0:
        pytest.skip("host reported no dashboard panels")
    box._tree.setCurrentItem(box._tree.topLevelItem(0))

    box._on_bind()

    assert "row" in box._status.text().lower()


# =========================================================================
# _TimelineBar — the video trimmer's geometry (TASK 1 leaf gate)
# =========================================================================
#
# video_crop.py was 524 lines with ZERO tests touching it — the only file in
# ui/qtgui with none.  These were written from a RECORDED run of the real
# widget, not from arithmetic: the clamp values below (19999 / 5001) are what
# it actually produced, which is how the 1 ms minimum clip was discovered
# rather than assumed.


def _bar(qtbot, duration_ms: int = 60_000):
    from trcc.ui.qtgui.video_crop import _TimelineBar

    bar = _TimelineBar()
    qtbot.addWidget(bar)
    bar.set_range(duration_ms)
    return bar


def test_the_in_handle_cannot_pass_the_out_handle(qtbot) -> None:
    """Dragging start past end must clamp, not invert the clip.

    An inverted range reaches ``ExportVideoClip`` as start > end, which is a
    clip nobody can encode — and the dialog would have looked fine.
    """
    bar = _bar(qtbot)
    bar.set_start(10_000)
    bar.set_end(20_000)

    bar.set_start(50_000)                      # shove it well past the out point

    start, end = bar.clip_ms()
    assert start < end, f"handles crossed: {start} >= {end}"
    assert (start, end) == (19_999, 20_000), (
        "the clamp is a 1 ms minimum clip against the out handle"
    )


def test_the_out_handle_cannot_pass_the_in_handle(qtbot) -> None:
    """The same invariant from the other side."""
    bar = _bar(qtbot)
    bar.set_start(5_000)

    bar.set_end(1_000)

    start, end = bar.clip_ms()
    assert start < end, f"handles crossed: {start} >= {end}"
    assert (start, end) == (5_000, 5_001)


def test_a_position_survives_the_trip_to_pixels_and_back(qtbot) -> None:
    """ms -> x -> ms may lose only what a pixel is worth.

    The timeline converts both ways on every drag; an error here walks the
    clip a little further each time the user touches it.
    """
    bar = _bar(qtbot, 60_000)
    ms_per_px = 60_000 / max(1, bar.width())

    for ms in (0, 15_000, 30_000, 60_000):
        back = bar._x_to_ms(bar._ms_to_x(ms))
        assert abs(back - ms) <= ms_per_px, (
            f"{ms}ms came back as {back}ms — more than one pixel of drift"
        )


def test_a_zero_length_video_does_not_divide_by_zero(qtbot) -> None:
    """``set_range(0)`` is reachable: ffprobe reports 0 for a broken file."""
    bar = _bar(qtbot, 0)

    assert bar._ms_to_x(0) == 0
    assert bar.clip_ms() == (0, 0)


# =========================================================================
# CloudThemeBrowser — the error a non-technical user actually reads
# =========================================================================
#
# Anchored to the strings ``adapters/repo/http.py`` REALLY raises
# (``f"GET {url} → HTTP {e.code}"``), not to invented ones.  Driving it with a
# plausible-looking "404 Not Found" suggested a bug that does not exist: the
# check is ``"http 4"``, and the real message contains "HTTP 404".


def _translate(message: str) -> str:
    from trcc.ui.qtgui.panels.cloud_theme_browser import _user_friendly_error

    return _user_friendly_error(message)


@pytest.mark.parametrize("raw", [
    "GET https://github.com/x/a001.zip → HTTP 404",
    "GET https://github.com/x/a001.zip → HTTP 503",
    "GET https://github.com/x/catalog.json → URL error: [Errno -3]",
    "GET https://x returned HTTP 500",
])
def test_transport_failures_are_translated_for_the_user(raw: str) -> None:
    """A user must never be shown ``HttpFetchError: GET ... → URL error``."""
    out = _translate(raw)

    assert out != raw, f"raw adapter text reached the user: {raw}"
    assert "http" not in out.lower(), f"HTTP jargon survived: {out}"
    assert "get " not in out.lower()


def test_a_domain_error_is_passed_through_unchanged() -> None:
    """Only TRANSPORT noise is translated.

    "Theme a001 is not in the catalog" is already plain language and already
    the most useful thing we can say — swallowing it into a generic network
    hint would be a downgrade.
    """
    msg = "Theme a001 is not in the catalog"

    assert _translate(msg) == msg


def test_the_network_hint_tells_the_user_what_to_do() -> None:
    """A message that only says what broke leaves the reader stuck."""
    out = _translate("GET https://x/catalog.json → URL error")

    assert "connection" in out.lower()
    assert "refresh" in out.lower(), "no next step offered"


# =========================================================================
# DevicePanel — a PM byte of 0 is DATA, not absence
# =========================================================================


def _inspector_text(gui_app: App, qtbot, *, pm, sub) -> str:
    from trcc.core.results import DeviceStateResult
    from trcc.ui.qtgui.panels.device_panel import DevicePanel

    panel = DevicePanel(gui_app, _bus(gui_app))
    qtbot.addWidget(panel)
    panel._current_key = lambda: "0402:3922"   # pyright: ignore[reportAttributeAccessIssue]
    panel.dispatch = lambda cmd: DeviceStateResult(   # pyright: ignore[reportAttributeAccessIssue]
        ok=True, key="0402:3922", wire="SCSI", catalog_resolution=(320, 320),
        catalog_product="Frozen Warframe 360", pm_byte=pm, sub_byte=sub,
        fbl=7, serial="ABC")
    panel._refresh_inspector()
    w = panel._inspector
    return w.toPlainText() if hasattr(w, "toPlainText") else w.text()


def test_a_pm_byte_of_zero_is_shown(gui_app: App, qtbot) -> None:
    """0 is a real handshake value; ``None`` is "not handshaken yet".

    ``if state.pm_byte:`` would collapse the two and hide a genuine device
    behind "not connected" — the Result keeps them apart precisely so an
    inspector can.
    """
    text = _inspector_text(gui_app, qtbot, pm=0, sub=0)

    assert "Handshake" in text, "a PM byte of 0 was treated as absence"
    assert "PM          0" in text


def test_an_unhandshaken_device_shows_no_handshake_block(
    gui_app: App, qtbot,
) -> None:
    text = _inspector_text(gui_app, qtbot, pm=None, sub=None)

    assert "Handshake" not in text, (
        "a device that never handshook reported handshake values"
    )


def test_the_inspector_labels_the_catalogs_answer(gui_app: App, qtbot) -> None:
    """It printed the catalog's size as "native res" -- 480x480 on a 320x240
    Mjolnir, the one size that panel is not."""
    text = _inspector_text(gui_app, qtbot, pm=None, sub=None)

    assert "  catalog     Frozen Warframe 360 320×320" in text.splitlines()
    assert "native res" not in text


def test_a_scan_lists_the_catalogs_guess_as_one(qtbot, tmp_path: Path) -> None:
    """A scan does not handshake: the name and size it lists are the
    catalog's for the USB id, and the line says so (#176)."""
    from trcc.adapters.render.qt import QtRenderer
    from trcc.ui.qtgui.panels.device_panel import DevicePanel

    from .mock_platform import MockPlatform

    app = App(MockPlatform([{"vid": "87ad", "pid": "70db", "pm": 5, "sub": 1}],
                           tmp_path), renderer=QtRenderer())
    panel = DevicePanel(app, _bus(app))
    qtbot.addWidget(panel)

    panel._on_scan()

    rows = [panel._list.item(i).text() for i in range(panel._list.count())]
    assert rows == ["87ad:70db  —  ChiZhu Tech GrandVision 360 AIO  "
                    "(bulk, 480×480, catalog)"]


# =========================================================================
# TASK 1 — qtgui leaf gates: colour wheel, crop zoom, greyscale
# =========================================================================


def test_the_hue_wheel_wraps_instead_of_clamping(qtbot) -> None:
    """Hue is circular, so -1 is 359 and 360 is 0.

    Clamping would stick the marker at an end of a wheel that has no ends —
    dragging anticlockwise past red would stop dead instead of continuing.
    """
    from trcc.ui.qtgui.color_wheel import ColorWheel

    wheel = ColorWheel()
    qtbot.addWidget(wheel)

    for given, expected in ((0, 0), (359, 359), (-1, 359), (360, 0), (720, 0)):
        wheel.set_hue(given)
        assert wheel.hue() == expected, f"set_hue({given}) gave {wheel.hue()}"


def test_the_wheel_ring_excludes_its_own_centre(qtbot) -> None:
    """The ring is an annulus — a click in the hole is not a hue pick."""
    from trcc.ui.qtgui.color_wheel import ColorWheel

    wheel = ColorWheel()
    qtbot.addWidget(wheel)
    wheel.resize(200, 200)
    cx, cy = wheel._center()

    assert wheel._on_ring(cx, cy) is False, "the hub answered as ring"
    assert wheel._on_ring(cx + wheel._outer_r() - 1, cy) is True
    assert wheel._inner_r() < wheel._outer_r()


def test_crop_zoom_is_monotonic_and_reaches_full_size(qtbot) -> None:
    """The slider must never zoom BACKWARDS as it is dragged right.

    Recorded from the real widget: 0 -> 0.25, 50 -> 0.40, 100 -> 1.00, i.e.
    geometric rather than linear — so asserting evenly-spaced steps would
    encode my expectation instead of its behaviour.
    """
    from trcc.ui.qtgui.image_crop import ImageCropDialog

    dialog = ImageCropDialog()
    qtbot.addWidget(dialog)

    zooms = [dialog._slider_to_zoom(v) for v in range(0, 101, 5)]
    assert zooms == sorted(zooms), "zoom went backwards as the slider advanced"
    assert zooms[0] == pytest.approx(0.25, abs=0.01)
    assert zooms[-1] == pytest.approx(1.0, abs=0.01)


def test_greyscale_desaturates_and_keeps_alpha(qtbot) -> None:
    """The contract is grey + alpha, NOT a particular luminance formula.

    Driving it first showed pure red -> 127, where the naive ``qGray`` says 87.
    Both are legitimate: Qt's ``Format_Grayscale8`` is gamma-correct in linear
    colourspace and ``qGray`` is a weighted average.  Asserting ``qGray``
    would have failed correct code — so this gates what the docstring actually
    promises.
    """
    from PySide6.QtGui import QColor, QPixmap

    from trcc.ui.qtgui.assets import _greyscale

    for rgba in ((255, 0, 0, 255), (0, 255, 0, 128), (100, 150, 200, 64)):
        src = QPixmap(4, 4)
        src.fill(QColor(*rgba))

        out = _greyscale(src).toImage().pixelColor(1, 1)

        assert out.red() == out.green() == out.blue(), f"{rgba} stayed coloured"
        assert out.alpha() == rgba[3], (
            f"{rgba} lost its alpha — transparent chrome would go opaque"
        )


def test_the_sensor_search_actually_narrows_the_list(gui_app: App, qtbot) -> None:
    """Typing filters by id, label OR category — it is the only way to find a
    sensor among 51 on a real host, and a search that silently matched
    nothing would look identical to a host with no sensors."""
    from trcc.ui.qtgui.sensor_picker import SensorPickerWidget

    picker = SensorPickerWidget(gui_app, _bus(gui_app))
    qtbot.addWidget(picker)
    picker._refresh()
    everything = picker._sensor_list.count()
    assert everything > 5, "host reported almost nothing — gate is meaningless"

    picker._search.setText("cpu")
    picker._rebuild_sensor_list()

    narrowed = picker._sensor_list.count()
    assert 0 < narrowed < everything, (
        f"search matched {narrowed} of {everything} — it did not narrow"
    )


def test_selecting_a_sensor_by_id_reports_it_back(gui_app: App, qtbot) -> None:
    """``select_sensor_id`` -> ``selected_sensor`` is the round-trip the
    dashboard editor relies on to show a row's current binding."""
    from trcc.ui.qtgui.sensor_picker import SensorPickerWidget

    picker = SensorPickerWidget(gui_app, _bus(gui_app))
    qtbot.addWidget(picker)
    picker._refresh()
    assert picker.selected_sensor() is None, "something was pre-selected"

    picker.select_sensor_id("cpu:temp")

    picked = picker.selected_sensor()
    assert picked is not None and picked[0] == "cpu:temp"


def test_a_led_tab_shows_itself_unless_it_says_otherwise() -> None:
    """The default must be VISIBLE.

    ``LedPanel`` asks only the optional tabs; a default of "hidden" would make
    a tab that forgot to manage its placeholder disappear silently, which is
    indistinguishable from a device that genuinely has no zones.
    """
    from trcc.ui.qtgui.panels.led._base import LedTabBase

    assert LedTabBase._placeholder_visible is False
    assert LedTabBase.has_visible_content(LedTabBase) is True  # type: ignore[arg-type]


def test_a_panel_without_setup_ui_is_refused_at_class_creation() -> None:
    """``BasePanel`` cannot use ``@abstractmethod``: ``QFrame`` + ``ABC`` is a
    metaclass conflict, so the contract is enforced in ``__init_subclass__``.

    Without it a panel missing ``_setup_ui`` constructs happily and renders an
    empty frame — a blank tab with no error anywhere.
    """
    from trcc.ui.qtgui.base import BasePanel

    with pytest.raises(TypeError, match="_setup_ui"):
        class _Missing(BasePanel):
            pass


def test_an_intermediate_base_may_skip_setup_ui() -> None:
    """``_abstract = True`` is how ``AssetBrowserPanel`` says "I have no layout
    of my own" — enforcement must not punish the shared bases."""
    from trcc.ui.qtgui.base import BasePanel

    class _Intermediate(BasePanel):
        _abstract = True

    assert _Intermediate._abstract is True


def test_the_shared_browser_guard_names_the_next_step(gui_app: App, qtbot) -> None:
    """``AssetBrowserPanel._device_key`` — pulled up from two browsers, and the
    sentence it writes is what the user reads when nothing is picked.

    It was duplicated VERBATIM in two panels; a guard that only said "no
    device" would leave a first-time user with nowhere to go.
    """
    from trcc.ui.qtgui.panels.local_theme_browser import LocalThemeBrowser

    panel = LocalThemeBrowser(gui_app, _bus(gui_app))
    qtbot.addWidget(panel)
    panel._picker.current_key = lambda: ""    # pyright: ignore[reportAttributeAccessIssue]

    assert panel._device_key() is None

    said = panel._status.text().lower()
    assert "pick a device" in said
    assert "devices panel" in said, "no next step offered to a new user"


# =========================================================================
# qtgui — ONE device selection per window
# =========================================================================

#: Two LCDs.  The bug this gates is invisible with one device, because every
#: panel independently defaults to index 0 and they agree by accident.
_TWO_LCDS = [
    {"type": "lcd", "vid": "87ad", "pid": "70db", "pm": 11, "sub": 5},
    {"type": "lcd", "vid": "0402", "pid": "3922", "fbl": 100},
]


#: Two LCDs + an LED controller — the fleet needed to prove KIND routing.
_MIXED_FLEET = [
    {"type": "lcd", "vid": "87ad", "pid": "70db", "pm": 11, "sub": 5},
    {"type": "lcd", "vid": "0402", "pid": "3922", "fbl": 100},
    {"type": "led", "vid": "0416", "pid": "8001", "pm": 208},
]


def _fleet_app(tmp_path: Path, specs: list[dict]):
    from trcc.adapters.render.qt import QtRenderer

    from .mock_platform import MockPlatform
    app = App(MockPlatform(specs, tmp_path), renderer=QtRenderer())
    for info in app.platform.scan_devices():
        app.attach(info.vid, info.pid)
    return app


def _two_device_app(tmp_path: Path):
    from trcc.adapters.render.qt import QtRenderer

    from .mock_platform import MockPlatform
    app = App(MockPlatform(_TWO_LCDS, tmp_path), renderer=QtRenderer())
    for info in app.platform.scan_devices():
        app.attach(info.vid, info.pid)
    return app


def test_every_panel_agrees_on_the_selected_device(make_window,
    qapp: object, tmp_path: Path, qtbot,
) -> None:
    """Picking a device in one panel must be what every other panel edits.

    qtgui gave each panel its OWN ``DevicePickerWidget`` — ten of them — and
    ``set_key`` (written so panels could sync) had zero production callers.
    Driven on a two-device fleet: both panels default to the first device, the
    user selects the second in Preview, and the overlay editor still edited the
    FIRST one.  Every element added then landed on the wrong screen.  One
    device hides it entirely, since each picker defaults to index 0 and they
    agree by accident.

    Built through the real ``MainWindow`` on purpose: sharing is a property of
    the WINDOW (it owns one ``DeviceSelection`` per kind), exactly as
    ``ui/gui`` owns a single ``TRCCApp._active_key``.  A panel constructed bare
    still gets a private selection, so asserting on bare panels would test
    nothing that ships.
    """
    del qapp
    from trcc.core.commands import ListDevices

    app = _two_device_app(tmp_path)
    try:
        keys = [d.key for d in app.dispatch(ListDevices()).devices]
        assert len(keys) == 2, f"fixture must offer two devices, got {keys}"

        window = make_window(app)
        watched = ["preview", "overlay", "themes", "masks", "display"]

        # The user picks the SECOND device.  Through the RAIL: it is the one
        # place a device is chosen, and the preview panel no longer carries a
        # picker of its own now that the preview is window chrome.
        window._sidebar.choose(keys[1])

        for name in watched:
            panel = window._panels[name]
            assert panel.device_key == keys[1], (
                f"{name} still edits {panel.device_key!r} after the user "
                f"selected {keys[1]!r} in the preview panel"
            )
            # The VISIBLE combo too, where the panel still has one — the
            # preview panel's went with its image.
            # Asserting ``device_key`` alone passed with the
            # selection -> picker propagation deleted, because every panel
            # reads the same selection object either way — while the panels
            # themselves still act through ``self._picker.current_key()``.
            # A gate that only proves the new accessor is a gate for the
            # wrong thing.
            picker = getattr(panel, "_picker", None)
            assert picker is None or picker.current_key() == keys[1], (
                f"{name}'s picker still shows {picker.current_key()!r} — "
                f"the user would see, and act on, the wrong device"
            )
    finally:
        app.close()


def test_the_led_panel_keeps_its_own_device_selection(make_window,
    qapp: object, tmp_path: Path, qtbot,
) -> None:
    """An LCD pick must not blank the LED panel.

    One selection for the whole window would be simpler and wrong: LED and LCD
    are different device kinds with different pickers (``kind_filter``), and
    ``ui/gui`` models them as separate top-level views.  Selecting an LCD would
    push a key the LED picker cannot show.
    """
    del qapp

    app = _two_device_app(tmp_path)
    try:
        window = make_window(app)
        assert (window._panels["led"].selection
                is not window._panels["preview"].selection)
    finally:
        app.close()


def test_the_sidebar_lists_every_attached_device(make_window,
    qapp: object, tmp_path: Path, qtbot,
) -> None:
    """The devices found are listed on the side, like ``ui/gui``.

    ``ui/gui``'s left rail IS the device list (``uc_device.py`` — "Shows
    connected LCD devices as clickable buttons", 180x800).  qtgui's rail is
    navigation, and its rail docstring claims to replace
    ``gui/uc_activity_sidebar.py`` — which is not a nav rail at all but the
    live-sensor list you click to add an overlay element.  The device list
    ended up demoted to one destination among thirteen.
    """
    del qapp
    from trcc.core.commands import ListDevices

    app = _two_device_app(tmp_path)
    try:
        keys = {d.key for d in app.dispatch(ListDevices()).devices}
        window = make_window(app)
        assert window._sidebar.device_keys() == keys, (
            f"rail lists {window._sidebar.device_keys()}, attached {keys}"
        )
    finally:
        app.close()


def test_choosing_a_device_in_the_rail_drives_every_panel(make_window,
    qapp: object, tmp_path: Path, qtbot,
) -> None:
    """Clicking the rail is what selects the device — as in ``ui/gui``.

    The rail is the ONE place a device is chosen; the per-panel pickers are
    views of that choice.  Routed by ``DeviceEntry.kind`` so picking an LCD
    cannot blank the LED panel.
    """
    del qapp
    from trcc.core.commands import ListDevices

    app = _two_device_app(tmp_path)
    try:
        keys = [d.key for d in app.dispatch(ListDevices()).devices]
        window = make_window(app)

        window._sidebar.choose(keys[1])

        for name in ("preview", "overlay", "themes", "display"):
            panel = window._panels[name]
            assert panel.device_key == keys[1], (
                f"{name} edits {panel.device_key!r} after the rail chose "
                f"{keys[1]!r}"
            )
            picker = getattr(panel, "_picker", None)
            assert picker is None or picker.current_key() == keys[1]
    finally:
        app.close()


def test_a_rail_pick_makes_every_device_panel_reload_for_that_device(
    make_window, qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pointing a panel at the device is not enough -- it has to LOAD it.

    ``set_key`` updated each panel's combo silently, so the panels that
    reload on their picker's ``key_changed`` never heard a rail pick: 9 of 11
    kept showing the previous device's state (measured 2026-09-30).  The
    Queries each panel sends for the NEW device are the proof it reloaded.
    """
    del qapp
    from trcc.core.commands import ListDevices

    app = _two_device_app(tmp_path)
    try:
        keys = [d.key for d in app.dispatch(ListDevices()).devices]
        window = make_window(app)
        asked: list[tuple[str, str]] = []
        real = app.dispatch

        def _record(command: object) -> object:
            asked.append((type(command).__name__, getattr(command, "key", "")))
            return real(command)

        monkeypatch.setattr(app, "dispatch", _record)
        # a cast running on the first device, as the App records it
        app.settings.set_screencast_region(keys[0], (0, 0, 10, 10, False))
        window._panels["screencast"]._show_state()
        window._sidebar.choose(keys[1])

        for_new = {name for name, key in asked if key == keys[1]}
        # the theme browser (its canvas), the mask browser, the overlay editor
        assert {"DeviceCanvas", "ListMasks", "ResolveOverlay"} <= for_new, (
            f"panels did not reload for {keys[1]}: asked {sorted(for_new)}")
        assert not {name for name, _ in asked} & {
            "StopScreencast", "SetOverlayConfig"}, "a device pick wrote"
    finally:
        app.close()


def test_choosing_an_lcd_on_the_rail_leaves_the_led_panel_alone(make_window,
    qapp: object, tmp_path: Path, qtbot,
) -> None:
    """Rail choices route by ``DeviceEntry.kind``, in BOTH directions.

    One selection for the window would be simpler and wrong: pushing an LCD
    key at the LED panel leaves it showing "no data" for a controller that is
    working fine, and vice versa.

    Asserting only that the two selections are distinct OBJECTS does not catch
    this — that assertion stayed green with the kind check deleted, because
    the objects are still two.  What has to be asserted is that a pick of one
    kind does not move the other.
    """
    del qapp
    from trcc.core.commands import ListDevices

    app = _fleet_app(tmp_path, _MIXED_FLEET)
    try:
        devices = app.dispatch(ListDevices()).devices
        lcds = [d.key for d in devices if d.kind != "led"]
        leds = [d.key for d in devices if d.kind == "led"]
        assert len(lcds) >= 2 and len(leds) == 1, (
            f"fleet must mix kinds: lcd={lcds} led={leds}"
        )

        window = make_window(app)
        led_panel = window._panels["led"]
        preview = window._panels["preview"]

        window._sidebar.choose(leds[0])
        assert led_panel.device_key == leds[0]
        lcd_before = preview.device_key

        window._sidebar.choose(lcds[1])
        assert preview.device_key == lcds[1], "the LCD pick did not land"
        assert led_panel.device_key == leds[0], (
            f"the LED panel followed an LCD pick — now {led_panel.device_key!r}"
        )

        window._sidebar.choose(leds[0])
        assert preview.device_key == lcds[1], (
            f"the LCD panels followed an LED pick — now {preview.device_key!r}"
        )
        del lcd_before
    finally:
        app.close()


def test_the_device_preview_is_always_on_screen(make_window,
    qapp: object, tmp_path: Path, qtbot,
) -> None:
    """The preview is chrome, not a destination.

    ``ui/gui`` keeps it at x 196-696 of a fixed window while the tool stack
    occupies x 712-1444, so changing a colour and seeing the result is ONE
    screen.  qtgui made it one page of a thirteen-page ``QStackedWidget``, so
    the edit->see loop required navigating away from the thing being edited —
    and drag-to-position an element had nowhere to live, because there was no
    preview on the editing screen to drag on.

    Asserted for EVERY destination, because "always" is the whole property.
    """
    del qapp

    app = _two_device_app(tmp_path)
    try:
        window = make_window(app)
        surface = window._preview_surface
        for name in window._panels:
            window._sidebar.selected.emit(name)
            assert not surface.isHidden(), (
                f"the preview is hidden while the {name!r} panel is shown"
            )
    finally:
        app.close()


def test_the_preview_is_not_rendered_twice(make_window,
    qapp: object, tmp_path: Path, qtbot,
) -> None:
    """One surface, not one per place that wants to show it.

    Extracting the surface into persistent chrome while leaving the old copy
    inside ``PreviewPanel`` would render every frame twice and put two
    ``BuildPreview`` dispatches on the tick — the duplication the vision
    explicitly rules out.
    """
    del qapp
    from trcc.ui.qtgui.preview_surface import PreviewSurface

    app = _two_device_app(tmp_path)
    try:
        window = make_window(app)
        surfaces = window.findChildren(PreviewSurface)
        assert len(surfaces) == 1, (
            f"{len(surfaces)} preview surfaces in one window — each one "
            "dispatches BuildPreview on its own tick"
        )

        # Counting the WIDGET is not enough: the old panel rendered with its
        # own inline QLabel, so a second copy would not be a PreviewSurface at
        # all and this gate would stay green while every frame rendered twice.
        # Count the actual work instead.
        from trcc.core.commands import BuildPreview
        seen: list = []
        real = App.dispatch

        def counting(self, cmd):
            if isinstance(cmd, BuildPreview):
                seen.append(cmd.key)
            return real(self, cmd)

        App.dispatch = counting           # type: ignore[method-assign]
        try:
            for widget in (surfaces[0], window._panels["preview"]):
                refresh = getattr(widget, "refresh", None) or widget._refresh
                refresh()
        finally:
            App.dispatch = real           # type: ignore[method-assign]
        assert len(seen) == 1, (
            f"{len(seen)} BuildPreview dispatches for one refresh round — "
            "the preview is being rendered more than once per tick"
        )
    finally:
        app.close()


def test_the_preview_wears_the_panel_s_bezel(qapp: object, tmp_path: Path) -> None:
    """The render sits inside its device's frame, at the frame's offset.

    ``ui/gui`` never showed a bare rectangle: it paints a 500x500 container
    with the frame image for that panel and insets the live render at an exact
    offset, so a 360x360 round cooler gets a ROUND bezel and an 854x480
    widescreen is scaled to fit.  The table is ``_PREVIEW_OFFSETS``
    (``ui/presentation/lcd_panel.py``) — toolkit-free, already tested, and
    imported by exactly ONE module in ``src/``: ``ui/gui/uc_preview``.

    qtgui painted the raw pixmap on a black QLabel.  Nothing was missing but
    the composition — the model is shared, and qtgui already reads gui's asset
    tree (``assets._default_assets_dir``), so all 57 ``preview_*.png`` were
    reachable the whole time.
    """
    del qapp
    from trcc.ui.presentation.lcd_panel import lcd_panel_for
    from trcc.ui.qtgui.device_selection import DeviceSelection
    from trcc.ui.qtgui.preview_surface import FRAME_EDGE, PreviewSurface

    app = _two_device_app(tmp_path)
    try:
        surface = PreviewSurface(app, _bus(app), DeviceSelection())
        assert surface._frame.size().toTuple() == (FRAME_EDGE, FRAME_EDGE)

        for resolution in ((320, 320), (360, 360), (854, 480)):
            surface._apply_panel(resolution)
            left, top, width, height, asset = (
                lcd_panel_for(resolution).offset_info
            )
            assert surface._label.pos().toTuple() == (left, top), (
                f"{resolution} render is at {surface._label.pos().toTuple()}, "
                f"not the bezel's cutout at {(left, top)}"
            )
            assert surface._label.size().toTuple() == (width, height)
            assert not surface._frame.pixmap().isNull(), (
                f"no bezel drawn for {resolution} (expected {asset})"
            )
    finally:
        app.close()


# =========================================================================
# qtgui follows the App: GPU, HDD, screencast, LED (Q4, 2026-09-30)
# =========================================================================


def test_the_gpu_box_shows_and_follows_the_active_gpu(
    gui_app: App, qtbot, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """It never selected the active GPU and sent its current row on a button
    press, so an untouched press switched to the first GPU listed."""
    from trcc.core.commands import ListGpus
    from trcc.core.events import GpuDeviceChanged
    from trcc.core.results import GpuDeviceResult, GpuEntry, GpusListResult
    from trcc.ui.qtgui.panels.system import GpuBox

    gui_app.settings.set_active_gpu("amd:0")
    real = gui_app.dispatch
    sent: list[str] = []

    def fake(cmd):
        if isinstance(cmd, ListGpus):
            return GpusListResult(ok=True, gpus=[
                GpuEntry(key="nvidia:0", name="N", is_discrete=True),
                GpuEntry(key="amd:0", name="A", is_discrete=True)])
        if type(cmd).__name__ == "SetGpuDevice":
            sent.append(cmd.gpu_key)
            return GpuDeviceResult(ok=True, message="ok")
        return real(cmd)

    monkeypatch.setattr(gui_app, "dispatch", fake)
    box = GpuBox(gui_app, _bus(gui_app))
    qtbot.addWidget(box)
    assert box._combo.currentData() == "amd:0", "opened on the wrong GPU"
    assert sent == [], "opening the box switched the GPU"

    gui_app.settings.set_active_gpu("nvidia:0")           # another UI
    gui_app.events.publish(GpuDeviceChanged(gpu_key="nvidia:0"))
    qtbot.waitUntil(lambda: box._combo.currentData() == "nvidia:0", timeout=2000)

    amd = box._combo.findData("amd:0")
    box._combo.setCurrentIndex(amd)
    box._combo.activated.emit(amd)                        # the user's pick
    assert sent == ["amd:0"]


def test_the_hdd_switch_follows_another_ui(gui_app: App, qtbot) -> None:
    from trcc.core.commands import SetHddEnabled
    from trcc.ui.qtgui.panels.system import SensorsBox

    assert gui_app.dispatch(SetHddEnabled(enabled=False)).ok
    box = SensorsBox(gui_app, _bus(gui_app))
    qtbot.addWidget(box)
    assert box._hdd_check.isChecked() is False
    assert gui_app.dispatch(SetHddEnabled(enabled=True)).ok
    qtbot.waitUntil(box._hdd_check.isChecked, timeout=2000)


def test_the_screencast_page_shows_the_selected_devices_cast(
    gui_app: App, qtbot, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Its buttons described a local ``_casting_key``: a cast started or
    stopped by another UI never showed, and after a device pick they still
    described the first device.  Stop now stops the SELECTED device's cast."""
    from trcc.core.events import ScreencastStarted
    from trcc.ui.qtgui.panels.screencast_panel import ScreencastPanel

    panel = ScreencastPanel(gui_app, _bus(gui_app))
    qtbot.addWidget(panel)
    _on_device(panel)
    assert (panel._start_btn.isEnabled(), panel._stop_btn.isEnabled()) == (True, False)

    gui_app.settings.set_screencast_region(_KEY_Q3, (10, 20, 300, 200, True))
    gui_app.events.publish(ScreencastStarted(key=_KEY_Q3, x=10, y=20, w=300,
                                             h=200, audio=True))
    qtbot.waitUntil(panel._stop_btn.isEnabled, timeout=2000)
    assert panel._audio.isChecked()

    sent = _writes(panel)
    panel._stop_btn.click()
    assert [type(c).__name__ for c in sent] == ["StopScreencast"]
    assert sent[0].key == _KEY_Q3
    qtbot.waitUntil(panel._start_btn.isEnabled, timeout=2000)


def test_the_led_panel_reloads_on_settings_not_on_every_render(
    gui_app: App, qtbot,
) -> None:
    """It reloaded on LedColorsChanged, which every render publishes (~6.7/s
    animated): three Queries a frame, and a pick pulled back mid-edit."""
    from trcc.core.events import LedColorsChanged, LedSettingsChanged
    from trcc.ui.qtgui.panels.led_panel import LedPanel

    panel = LedPanel(gui_app, _bus(gui_app))
    qtbot.addWidget(panel)
    panel._picker.current_key = lambda: "0416:8001"   # pyright: ignore[reportAttributeAccessIssue]
    asked: list[str] = []
    real = panel.dispatch

    def spy(cmd):
        asked.append(type(cmd).__name__)
        return real(cmd)

    panel.dispatch = spy                              # pyright: ignore[reportAttributeAccessIssue]
    for _ in range(5):
        gui_app.events.publish(LedColorsChanged(key="0416:8001", color_count=30))
    qtbot.wait(200)
    assert "LedSnapshot" not in asked, "a render reloaded the panel"

    gui_app.events.publish(LedSettingsChanged(key="0416:8001"))
    qtbot.waitUntil(lambda: "LedSnapshot" in asked, timeout=2000)


# =========================================================================
# Q5: fields load on open, and only a finished CHANGE writes (2026-09-30)
# =========================================================================


def test_the_mask_browser_opens_on_the_apps_position_and_writes_only_changes(
    gui_app: App, qtbot, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """It opened at 0,0 / visible (loaded only on a device change), and its
    spinboxes wrote on editingFinished -- a plain focus change sent the
    position unchanged."""
    from trcc.core.commands import Query, SetMaskPosition, SetMaskVisible
    from trcc.ui.qtgui import device_picker
    from trcc.ui.qtgui.panels.mask_browser import MaskBrowser

    gui_app.dispatch(SetMaskPosition(key=_KEY_Q3, x=12, y=34))
    gui_app.dispatch(SetMaskVisible(key=_KEY_Q3, visible=False))
    monkeypatch.setattr(device_picker.DevicePickerWidget, "current_key",
                        lambda self: _KEY_Q3)
    writes: list = []
    real = gui_app.dispatch

    def _record(command):
        if not isinstance(command, Query):
            writes.append(command)
        return real(command)

    monkeypatch.setattr(gui_app, "dispatch", _record)
    panel = MaskBrowser(gui_app, _bus(gui_app))
    qtbot.addWidget(panel)
    assert (panel._x.value(), panel._y.value(), panel._visible.isChecked()) == (
        12, 34, False)

    panel._x.editingFinished.emit()                   # focus moved, no change
    assert writes == [], f"opening or a focus change wrote {writes}"
    panel._x.setValue(40)                             # a finished change
    assert writes == [SetMaskPosition(key=_KEY_Q3, x=40, y=34)], writes


def test_the_zone_tab_interval_writes_only_a_change(gui_app: App, qtbot) -> None:
    tab = _zone_tab_with(gui_app, qtbot, zones=3)
    sent: list = []
    real = tab._dispatch

    def spy(command):
        sent.append(type(command).__name__)
        return real(command)

    tab._dispatch = spy                               # pyright: ignore[reportAttributeAccessIssue]
    tab._interval_spin.editingFinished.emit()         # focus moved, no change
    assert sent == []
    tab._interval_spin.setValue(tab._interval_spin.value() + 5)
    assert sent == ["SetLedZoneSyncInterval"], sent

    sent.clear()
    row = tab._zone_widgets[0]._brightness            # a zone's own brightness
    row.editingFinished.emit()
    assert sent == []
    row.setValue(row.value() - 10)
    assert sent == ["SetLedZoneBrightness"], sent


def test_the_device_panel_lists_devices_on_open_and_frames_cost_no_query(
    make_window, qapp: object, tmp_path: Path, qtbot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Its list was empty until Scan, and its inspector re-queried DeviceState
    on every sent frame -- a socket round trip per frame under a shared App."""
    del qapp, make_window
    from trcc.core.commands import ListDevices
    from trcc.core.events import FrameSent
    from trcc.ui.qtgui.panels.device_panel import DevicePanel

    app = _two_device_app(tmp_path)
    try:
        keys = [d.key for d in app.dispatch(ListDevices()).devices]
        panel = DevicePanel(app, _bus(app))
        qtbot.addWidget(panel)
        listed = [panel._list.item(i).data(0x0100) for i in range(panel._list.count())]
        assert listed == keys, f"opened listing {listed}"

        panel._list.setCurrentRow(0)
        asked: list[str] = []
        real = panel.dispatch

        def spy(cmd):
            asked.append(type(cmd).__name__)
            return real(cmd)

        panel.dispatch = spy                          # pyright: ignore[reportAttributeAccessIssue]
        for n in (100, 200, 300):
            app.events.publish(FrameSent(key=keys[0], bytes_sent=n))
        qtbot.waitUntil(lambda: "300 bytes" in panel._inspector.text(),
                        timeout=2000)
        assert asked == [], f"a sent frame dispatched {asked}"
    finally:
        app.close()


@pytest.mark.parametrize("user_moves_x", [False, True])
def test_the_qtgui_edit_dialog_sends_only_what_the_user_changed(
    gui_app: App, qtbot, monkeypatch: pytest.MonkeyPatch, user_moves_x: bool,
) -> None:
    """Every field used to go back on OK: a size the dialog clamped (256 ->
    200) or a colour another UI changed while it was open was overwritten
    without being touched.  Measured 2026-09-30: 3 of 9 element kinds
    changed on an untouched OK."""
    from PySide6.QtWidgets import QDialog

    from trcc.core.commands import AddOverlayElement, UpdateOverlayElement
    from trcc.ui.qtgui.panels import overlay_editor
    from trcc.ui.qtgui.panels.overlay_editor import OverlayEditorPanel

    key = "0402:3922"
    assert gui_app.dispatch(AddOverlayElement(
        key=key, element_id="e", type="text", text="HERO", size=256, x=5)).ok
    panel = OverlayEditorPanel(gui_app, _bus(gui_app))
    qtbot.addWidget(panel)
    panel._picker.set_key(key)
    panel.refresh()
    panel._list.setCurrentRow(0)

    def _exec(dialog):
        # another UI recolours the element while the dialog is open ...
        assert gui_app.dispatch(UpdateOverlayElement(
            key=key, element_id="e", color="#00ff00")).ok
        if user_moves_x:                          # ... and the user moves it
            dialog._x.setValue(40)
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(overlay_editor._ElementDialog, "exec", _exec)
    panel._on_edit()

    element = gui_app.settings.for_device(key).user_overlay_elements[0]
    assert (element.size, element.color) == (256, "#00ff00"), (
        "an untouched field was written back")
    assert element.x == (40 if user_moves_x else 5)


# =========================================================================
# qtgui follows the app-wide settings another UI changes (2026-10-02)
# =========================================================================


def test_qtgui_s_autostart_box_follows_another_ui(gui_app: App, qtbot) -> None:
    """MUTATION CHECK -- MEASURED 2026-10-02: drop the AutostartChanged case
    in ``SystemPanel._on_app_settings_changed`` → fails."""
    from trcc.core.commands import DisableAutostart, EnableAutostart
    from trcc.ui.qtgui.panels.system_panel import SystemPanel

    panel = SystemPanel(gui_app, _bus(gui_app))
    qtbot.addWidget(panel)
    check = panel._maintenance._autostart_check
    assert not check.isChecked()

    gui_app.dispatch(EnableAutostart())
    qtbot.waitUntil(check.isChecked, timeout=3000)
    gui_app.dispatch(DisableAutostart())
    qtbot.waitUntil(lambda: not check.isChecked(), timeout=3000)


def test_qtgui_s_dashboard_reloads_a_layout_saved_elsewhere(
    gui_app: App, qtbot,
) -> None:
    """MUTATION CHECK -- MEASURED 2026-10-02: drop the SensorDashboardChanged
    case → fails."""
    from trcc.core.commands import SetSensorDashboard
    from trcc.core.models import PanelConfig
    from trcc.ui.qtgui.panels.system_panel import SystemPanel

    panel = SystemPanel(gui_app, _bus(gui_app))
    qtbot.addWidget(panel)

    gui_app.dispatch(SetSensorDashboard(panels=(PanelConfig.custom("Elsewhere"),)))

    qtbot.waitUntil(lambda: [p.name for p in panel._dash._panels] == ["Elsewhere"],
                    timeout=3000)


def test_qtgui_s_dashboard_keeps_unsaved_edits_when_another_ui_saves(
    gui_app: App, qtbot,
) -> None:
    """Reloading would throw away edits the user has not saved -- with no
    warning, since they are only in this box.  It says so instead.

    MUTATION CHECK -- MEASURED 2026-10-02: reload unconditionally in
    ``on_layout_saved`` → fails.
    """
    from trcc.core.commands import SetSensorDashboard
    from trcc.core.models import PanelConfig
    from trcc.ui.qtgui.panels.system_panel import SystemPanel

    panel = SystemPanel(gui_app, _bus(gui_app))
    qtbot.addWidget(panel)
    box = panel._dash
    box._on_add_panel()                                     # unsaved edit
    mine = [p.name for p in box._panels]

    gui_app.dispatch(SetSensorDashboard(panels=(PanelConfig.custom("Elsewhere"),)))

    qtbot.waitUntil(lambda: "another window" in box._status.text(), timeout=3000)
    assert [p.name for p in box._panels] == mine


def test_qtgui_s_disk_picker_follows_another_ui(gui_app: App, qtbot) -> None:
    """MUTATION CHECK -- MEASURED 2026-10-02: drop ``LedPanel``'s
    app_settings_changed hookup → fails."""
    from trcc.adapters.sensors.aggregator import BaselineSensors
    from trcc.core.commands import SetDiskDevice
    from trcc.ui.qtgui.panels.led_panel import LedPanel

    from .conftest import FakeCpu, FakeMemory
    from .test_sensors import FakeDisk

    gui_app.platform._sensors = BaselineSensors(   # type: ignore[attr-defined]
        cpu=FakeCpu(), memory=FakeMemory(), gpus=[], fans=[],
        disks=[FakeDisk("nvme0", 41.0), FakeDisk("nvme1", 58.0)])
    panel = LedPanel(gui_app, _bus(gui_app))
    qtbot.addWidget(panel)
    picker = panel._advanced_tab._disk_selector

    gui_app.dispatch(SetDiskDevice(disk_key="nvme1"))

    qtbot.waitUntil(lambda: picker.currentData() == "nvme1", timeout=3000)


def test_qtgui_shows_the_sent_frame_without_rendering_another(gui_app: App) -> None:
    """qtgui re-rendered on every FrameSent, in-process too; now it shows the
    frame the panel got -- the live surface, or (across the daemon socket)
    the App's JPEG of it, which the shared BusBridge turns into a surface.

    MUTATION CHECK: call ``refresh()`` on every FrameSent again and a
    BuildPreview goes out per frame.
    """
    from PySide6.QtGui import QColor, QImage
    from PySide6.QtWidgets import QApplication

    from trcc.adapters.render.qt import QtRenderer
    from trcc.core.events import FrameSent
    from trcc.ui.qtgui.device_selection import DeviceSelection
    from trcc.ui.qtgui.preview_surface import PreviewSurface

    selection = DeviceSelection()
    surface = PreviewSurface(gui_app, _bus(gui_app), selection)
    selection.set_key("0402:3922")
    surface._label.setFixedSize(320, 320)
    frame = QImage(320, 320, QImage.Format.Format_RGB888)
    frame.fill(QColor(0, 64, 128))
    sizes: list[tuple[int, int]] = []
    surface.rendered.connect(lambda w, h: sizes.append((w, h)))
    asked: list[str] = []
    real = gui_app.dispatch
    gui_app.dispatch = lambda cmd: (asked.append(type(cmd).__name__), real(cmd))[1]  # type: ignore[method-assign]
    try:
        gui_app.events.publish(FrameSent(key="0402:3922", bytes_sent=1,
                                         image=QtRenderer().encode_jpeg(frame, 95)))
        gui_app.events.publish(FrameSent(key="0402:3922", bytes_sent=1,
                                         surface=frame))
        for _ in range(50):
            QApplication.processEvents()
            if len(sizes) == 2:
                break
    finally:
        gui_app.dispatch = real                # type: ignore[method-assign]

    assert asked == []
    assert sizes == [(320, 320), (320, 320)]
    assert not surface._label.pixmap().isNull()


def test_qtgui_welcomes_a_first_run_once(make_window, qapp: object,
                                         tmp_path: Path) -> None:
    """The welcome is for the FIRST launch.  Only the CLI and the API ever
    marked first run done, so a qtgui-only user opened on System with the
    welcome text at every launch.

    MUTATION CHECK: drop the MarkFirstRunDone dispatch and the second
    window opens on System again.
    """
    from trcc.core.commands import GetFirstRunStatus

    app = _two_device_app(tmp_path)
    assert app.dispatch(GetFirstRunStatus()).is_first_run, "precondition"

    first = make_window(app)
    second = make_window(app)

    assert first._content.currentWidget() is first._panels["system"]
    assert second._content.currentWidget() is second._panels["devices"]
    assert not app.dispatch(GetFirstRunStatus()).is_first_run


@pytest.mark.parametrize(("style_id", "title", "timer_shown"), [
    (2, "Select all", False),     # PA120: the switch selects every zone
    (1, "Carousel", True),        # AX120: a page style circulates
], ids=["PA120", "AX120"])
def test_the_qtgui_zone_tab_names_the_switch_as_the_csharp_does(
    gui_app: App, qapp: object, style_id: int, title: str, timer_shown: bool,
) -> None:
    """On PA120/LF10 nothing rotates: the switch makes an edit reach every
    zone, and the C#'s panel reads "Select all" with its timer hidden
    (FormLED.cs:1669).  The tab said "Carousel" with a rotation interval.

    MUTATION CHECK: always title it "Carousel" and PA120 fails.
    """
    from trcc.ui.qtgui.panels.led import ZoneTab

    tab = ZoneTab(gui_app, _led_key)
    tab.apply_style(style_id)

    assert tab._sync_box.title() == title
    assert tab._interval_spin.isHidden() is not timer_shown
    assert tab._participation.isHidden() is not timer_shown


@pytest.mark.parametrize(("style_id", "word"), [
    (2, "Select all"), (7, "Select all"), (1, "Circulate"),
], ids=["PA120", "LF10", "AX120"])
def test_the_gui_led_panel_labels_the_switch_as_the_csharp_does(
    qapp: object, style_id: int, word: str,
) -> None:
    """MUTATION CHECK: always say "Circulate" and PA120/LF10 fail."""
    from trcc.ui.gui.assets import _PKG_ASSETS_DIR, set_assets_dir
    from trcc.ui.gui.uc_led_control import UCLedControl

    set_assets_dir(_PKG_ASSETS_DIR)
    panel = UCLedControl()
    panel.initialize(style_id, zone_count=4)

    assert panel._circulate_label.text() == word
