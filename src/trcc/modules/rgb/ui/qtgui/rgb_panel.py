"""RgbPanel -- RGB Lighting: the shared page in the native style, this skin's
Commands."""
from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from PySide6.QtWidgets import QVBoxLayout

from trcc.core.commands import Command, ListDevices
from trcc.core.results import DeviceEntry
from trcc.modules.rgb.core.commands import (
    BlinkAuraHeader,
    OpenRgbPorts,
    RgbFollow,
    RgbLights,
    ScanRgbLights,
    SetAuraEffect,
    SetAuraLeds,
    SetLinkedColor,
    SetRamEffect,
    SetRgbFollow,
)
from trcc.modules.rgb.core.models import RgbFollowMode
from trcc.modules.rgb.core.results import OpenRgbPortsResult, RgbFollowResult, RgbLightsResult
from trcc.modules.rgb.ui.presentation.rgb_page import (
    ApplyPlan,
    FindLights,
    FollowDevice,
    HeaderLeds,
    LeaveLights,
    LinkColor,
    RunBoardEffect,
    SaveEffect,
)
from trcc.modules.rgb.ui.qt_ram_access import RamAccessRow
from trcc.modules.rgb.ui.qt_rgb_page import RgbPageView, RgbSkin, hear
from trcc.modules.rgb.ui.qtgui.ram_access import RamAccessControl
from trcc.ui.qtgui.base import BasePanel

log = logging.getLogger(__name__)

#: The events that change what the page shows, from any UI.


class QtRgbSkin(RgbSkin):
    """This skin's Queries and Commands, for the shared page."""

    def access_row(self) -> RamAccessRow:
        log.debug("QtRgbSkin.access_row")
        return RamAccessControl(self.app)

    def lights(self) -> RgbLightsResult:
        log.debug("QtRgbSkin.lights")
        return self.app.dispatch(RgbLights())

    def following(self) -> RgbFollowResult:
        log.debug("QtRgbSkin.following")
        return self.app.dispatch(RgbFollow())

    def devices(self) -> Sequence[DeviceEntry]:
        log.debug("QtRgbSkin.devices")
        return self.app.dispatch(ListDevices()).devices

    def ports(self) -> OpenRgbPortsResult:
        log.debug("QtRgbSkin.ports")
        return self.app.dispatch(OpenRgbPorts())

    def find(self, plan: FindLights) -> Command[Any]:
        log.info("QtRgbSkin.find: %s", plan)
        return ScanRgbLights(host=plan.host, port=plan.port)

    def leds(self, plan: HeaderLeds) -> Command[Any]:
        log.info("QtRgbSkin.leds: %s", plan)
        return SetAuraLeds(header=plan.header, leds=plan.leds)

    def blink(self, header: int) -> Command[Any]:
        log.info("QtRgbSkin.blink: header %d", header)
        return BlinkAuraHeader(header=header)

    def send(self, plan: ApplyPlan) -> Command[Any]:
        log.info("QtRgbSkin.send: %s", plan)
        match plan:
            case LeaveLights():
                return SetRgbFollow(mode=RgbFollowMode.OFF)
            case SaveEffect(refs=refs, settings=s):
                return SetRamEffect(
                    effect=s.effect, refs=refs, speed=s.speed,
                    direction=s.direction, colors=s.colors,
                    random_colors=s.random_colors, brightness=s.brightness)
            case LinkColor(refs=refs, effect=e):
                return SetLinkedColor(metric=e.metric, refs=refs, source=e.source)
            case RunBoardEffect(refs=refs, settings=s):
                return SetAuraEffect(
                    effect=s.effect, refs=refs, speed=s.speed,
                    direction=s.direction, colors=s.colors,
                    random_colors=s.random_colors, brightness=s.brightness)
            case FollowDevice():
                return SetRgbFollow(
                    mode=plan.mode, host=plan.host, port=plan.port,
                    source=plan.source, mapping=plan.mapping,
                    colors=plan.colors, targets=plan.targets)


class RgbPanel(BasePanel):
    """RGB Lighting: the memory's own effects, and what follows a device."""

    #: Its sidebar entry.
    LABEL = "RGB Lighting"

    def _setup_ui(self) -> None:
        log.debug("RgbPanel._setup_ui")
        self.page = RgbPageView(QtRgbSkin(self._app), self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.addWidget(self.page)
        hear(self.page, self._bus)
