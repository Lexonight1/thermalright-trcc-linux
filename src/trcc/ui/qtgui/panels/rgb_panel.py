"""RgbPanel -- RGB Lighting: the shared page in the native style, this skin's
Commands."""
from __future__ import annotations

import logging
from collections.abc import Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QVBoxLayout

from ....core.commands import (
    ListDevices,
    RgbFollow,
    RgbLights,
    ScanRgbLights,
    SetRamEffect,
    SetRgbFollow,
)
from ....core.events import RamLightingChanged, RgbFollowChanged, RgbLightsChanged
from ....core.models import RgbFollowMode
from ....core.results import DeviceEntry, Result, RgbFollowResult, RgbLightsResult
from ...presentation.rgb_page import (
    ApplyPlan,
    FindLights,
    FollowDevice,
    LeaveLights,
    SaveEffect,
)
from ...qt_ram_access import RamAccessRow
from ...qt_rgb_page import RgbPageView
from ..base import BasePanel
from .ram_access import RamAccessControl

log = logging.getLogger(__name__)

#: The events that change what the page shows, from any UI.
_RGB_EVENTS = (RgbLightsChanged, RgbFollowChanged, RamLightingChanged)


class QtRgbPage(RgbPageView):
    """The page body, sending this skin's Commands."""

    def _access_row(self) -> RamAccessRow:
        log.debug("QtRgbPage._access_row")
        return RamAccessControl(self._app)

    def _lights(self) -> RgbLightsResult:
        log.debug("QtRgbPage._lights")
        return self._app.dispatch(RgbLights())

    def _following(self) -> RgbFollowResult:
        log.debug("QtRgbPage._following")
        return self._app.dispatch(RgbFollow())

    def _devices(self) -> Sequence[DeviceEntry]:
        log.debug("QtRgbPage._devices")
        return self._app.dispatch(ListDevices()).devices

    def _find(self, plan: FindLights) -> Result:
        log.info("QtRgbPage._find: %s", plan)
        return self._app.dispatch(ScanRgbLights(host=plan.host, port=plan.port))

    def _send(self, plan: ApplyPlan) -> Result:
        log.info("QtRgbPage._send: %s", plan)
        match plan:
            case LeaveLights():
                return self._app.dispatch(SetRgbFollow(mode=RgbFollowMode.OFF))
            case SaveEffect(refs=refs, settings=s):
                return self._app.dispatch(SetRamEffect(
                    effect=s.effect, refs=refs, speed=s.speed,
                    direction=s.direction, colors=s.colors,
                    random_colors=s.random_colors, brightness=s.brightness))
            case FollowDevice():
                return self._app.dispatch(SetRgbFollow(
                    mode=plan.mode, host=plan.host, port=plan.port,
                    source=plan.source, mapping=plan.mapping,
                    colors=plan.colors, targets=plan.targets))


class RgbPanel(BasePanel):
    """RGB Lighting: the memory's own effects, and what follows a device."""

    def _setup_ui(self) -> None:
        log.debug("RgbPanel._setup_ui")
        self.page = QtRgbPage(self._app, self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.addWidget(self.page)
        self._bus.app_settings_changed.connect(
            self._on_app_settings_changed,
            type=Qt.ConnectionType.QueuedConnection)
        # A followed LCD's frames, a followed cooler's colours, and what
        # following sent the lights, for the live preview.
        self._bus.frame_sent.connect(self.page.on_frame,
                                     type=Qt.ConnectionType.QueuedConnection)
        self._bus.led_colors_changed.connect(
            self.page.on_led_colors, type=Qt.ConnectionType.QueuedConnection)
        self._bus.rgb_follow_sent.connect(
            self.page.on_follow_sent, type=Qt.ConnectionType.QueuedConnection)

    def _on_app_settings_changed(self, event: object) -> None:
        log.debug("RgbPanel._on_app_settings_changed: %s", type(event).__name__)
        if isinstance(event, _RGB_EVENTS):
            self.page.on_app_event(event)
