"""SystemPanel — host for the system boxes.

Stacks one :class:`~.system.SystemBox` per concern and owns two things no
box can own alone: the vertical layout, and the one live subscription.

Each box states its own ``TITLE`` and ``STRETCH``, so adding one is a line
in ``_setup_ui`` and nothing else.  The broadcast is wired to exactly ONE
named method — ``SensorsBox.on_sensors_updated`` — rather than looped over
every box, so ``ListMemorySlots`` (which shells out to ``dmidecode``) cannot
drift onto the live path and the log a reporter pastes gains one record per
broadcast rather than six.

**It is a subscription, not a timer.**  This panel held a hardcoded 2 s
``QTimer`` that ignored ``refresh_interval_s`` entirely, so it re-read sensors
ten times per broadcast at a 10 s interval and rendered half as often as data
arrived at 1 s.  ``MetricsLoop`` already publishes ``SensorsUpdated`` on the
user's cadence and ``ui/gui`` has observed it all along; this was the last
qtgui surface polling a datum the bus was already delivering.

Replaces the 28-method monolith this file used to hold: qtgui's only god
class, and a 65% outlier in its own skin.
"""
from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QVBoxLayout

from ....core.events import AutostartChanged, SensorDashboardChanged
from ..base import BasePanel
from .system import (
    DashboardBox,
    GpuBox,
    HealthBox,
    MaintenanceBox,
    PlatformBox,
    SensorsBox,
)

log = logging.getLogger(__name__)


class SystemPanel(BasePanel):
    """Live system readout + diagnostic actions."""

    def _setup_ui(self) -> None:
        log.debug("_setup_ui")
        self._platform = PlatformBox(self.app, self.bus, self)
        self._gpu = GpuBox(self.app, self.bus, self)
        self._maintenance = MaintenanceBox(self.app, self.bus, self)
        self._health = HealthBox(self.app, self.bus, self)
        self._sensors = SensorsBox(self.app, self.bus, self)
        self._dash = DashboardBox(self.app, self.bus, self)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(12)
        for box in (self._platform, self._gpu, self._maintenance,
                    self._health, self._sensors, self._dash):
            outer.addWidget(box, box.STRETCH)
        outer.addLayout(self._build_action_row())

        # Queued, like every other bridge connection: ``SensorsUpdated`` is
        # published from the MetricsLoop thread and Qt widgets are main-thread
        # only.
        self._bus.sensors_updated.connect(
            self._sensors.on_sensors_updated,
            type=Qt.ConnectionType.QueuedConnection,
        )
        # Autostart and the dashboard, changed in ANY UI.  Routed to the one
        # box each, by name -- SystemBox has no shared refresh on purpose.
        self._bus.app_settings_changed.connect(
            self._on_app_settings_changed,
            type=Qt.ConnectionType.QueuedConnection,
        )
        log.info("_setup_ui: six boxes built; sensors ride SensorsUpdated")

    def _on_app_settings_changed(self, event: object) -> None:
        log.debug("_on_app_settings_changed: %s", type(event).__name__)
        match event:
            case AutostartChanged():
                self._maintenance.refresh()
            case SensorDashboardChanged():
                self._dash.on_layout_saved()

    def _build_action_row(self) -> QHBoxLayout:
        """The two health actions, kept below the boxes they act on."""
        log.debug("_build_action_row")
        row = QHBoxLayout()
        refresh = QPushButton("Re-run health check", self)
        refresh.clicked.connect(self._health.refresh)
        row.addWidget(refresh)

        report = QPushButton("Save bug report…", self)
        report.clicked.connect(self._health.save_debug_report)
        row.addWidget(report)

        row.addStretch(1)
        return row
