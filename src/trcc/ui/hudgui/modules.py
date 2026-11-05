"""hudgui's Modules panel: the gui's and qtgui's list (``ui.qt_modules``),
on the glass.

Every module with its On/Off, Unlock, Remove and "Guide ›"; "Get modules"
for the signed index; developer mode's install from a folder.  The window
names the Commands, as the gui's and qtgui's pages do, so each UI's reach is
its own to read (``dev/tools/ui_contract.py``).  A module switched in any UI
turns here too.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt

from ...core.commands import (
    BrowseModules,
    InstallModule,
    InstallModuleFolder,
    ListModules,
    ModuleIcon,
    ReadModuleFolder,
    RemoveModule,
    SetDeveloperMode,
    SetModuleEnabled,
    UnlockModuleDevices,
)
from ..qt_modules import ModulesView

if TYPE_CHECKING:
    from ...core.models import ModuleAsks
    from ...core.ports import CommandBus
    from ..bus_bridge import BusBridge

log = logging.getLogger(__name__)

#: The list on the glass: its tiles as the Sensors cards.
_STYLE = (
    "QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }"
    "QFrame#moduleTile { background: rgba(255, 255, 255, 14); "
    "border: 1px solid rgba(255, 255, 255, 30); border-radius: 8px; }"
    "QLabel, QCheckBox { color: #dfe5ea; background: transparent; font-size: 10pt; }"
    "QLabel#moduleTitle { color: #a9e6ff; font-size: 12pt; font-weight: bold; }"
    "QLabel#moduleProblem { color: #ffcf6e; }"
    "QPushButton#moduleGuide { border: none; background: transparent; color: #7fd4ff; }"
    "QPushButton:checked { background: #a9e6ff; color: #031520; }"
    # A styled QCheckBox drops its native box, so it is drawn here.
    "QCheckBox::indicator { width: 14px; height: 14px; border-radius: 3px; "
    "background: rgba(255, 255, 255, 25); border: 1px solid rgba(255, 255, 255, 90); }"
    "QCheckBox::indicator:checked { background: #a9e6ff; }")


def modules_panel(app: CommandBus, bus: BusBridge) -> ModulesView:
    """The Modules list, styled for the glass, following every UI."""
    log.info("modules_panel: built")
    view = ModulesView(app, ListModules, _switch, ModuleIcon, None,
                       browse=BrowseModules, install=_install,
                       remove=RemoveModule, developer=SetDeveloperMode,
                       read_folder=ReadModuleFolder,
                       install_folder=InstallModuleFolder,
                       unlock=UnlockModuleDevices)
    view.setStyleSheet(_STYLE)
    bus.app_settings_changed.connect(view.on_event,
                                     type=Qt.ConnectionType.QueuedConnection)
    return view


def _switch(key: str, enabled: bool) -> SetModuleEnabled:
    """The Command a tile's switch sends."""
    log.debug("hudgui _switch: %s -> %s", key, enabled)
    return SetModuleEnabled(key=key, enabled=enabled)


def _install(key: str, asks: ModuleAsks) -> InstallModule:
    """The Command an Install sends: what the user accepted."""
    log.debug("hudgui _install: %s %s", key, asks)
    return InstallModule(key=key, asks=asks)
