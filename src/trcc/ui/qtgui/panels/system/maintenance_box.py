"""MaintenanceBox — autostart, update check, in-app upgrade.

Three things a user does to the *install* rather than to a device, which
is why they sit together and away from the live readouts.
"""
from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QLabel,
    QMessageBox,
    QPushButton,
)

from .....core.commands import (
    CheckForUpdate,
    DisableAutostart,
    EnableAutostart,
    GetAutostartStatus,
    RunUpgrade,
)
from .....core.models import AUTOSTART_TARGETS, DEFAULT_AUTOSTART_TARGET
from ._base import SystemBox

log = logging.getLogger(__name__)


class MaintenanceBox(SystemBox):
    """Start-on-login, "is there a newer release", and installing it."""

    TITLE = "Maintenance"

    def _build_ui(self) -> None:
        log.debug("_build_ui")
        form = QFormLayout(self)
        self._autostart_check = QCheckBox("Start TRCC on login", self)
        self._autostart_check.toggled.connect(self._on_autostart_toggled)
        # WHICH ui starts.  All four can; a boolean could only ever mean gui.
        # The list comes from the ONE registry — a UI that spelled the targets
        # itself would be a second source to drift from it.
        self._autostart_target = QComboBox(self)
        for name in sorted(AUTOSTART_TARGETS):
            self._autostart_target.addItem(name, name)
        self._autostart_target.currentIndexChanged.connect(
            self._on_autostart_target_changed,
        )
        self._update_btn = QPushButton("Check for updates", self)
        self._update_btn.clicked.connect(self._on_check_update)
        # Checking told the user a newer release exists and then offered no way
        # to get it -- cli has ``trcc system upgrade`` and api has the route,
        # so qtgui users were the only ones who had to leave the app.
        self._upgrade_btn = QPushButton("Upgrade now…", self)
        self._upgrade_btn.clicked.connect(self._on_upgrade)
        self._status = QLabel("", self)
        self._status.setWordWrap(True)
        self._status.setTextFormat(Qt.TextFormat.RichText)
        self._status.setOpenExternalLinks(True)
        form.addRow(self._autostart_check)
        form.addRow("Start:", self._autostart_target)
        form.addRow(self._update_btn)
        form.addRow(self._upgrade_btn)
        form.addRow(self._status)
        self.refresh()

    def refresh(self) -> None:
        """Show what is INSTALLED, not what the widget last showed.

        ``blockSignals`` because ``setChecked`` emits ``toggled``: an
        unguarded load would dispatch a write on every refresh, and the
        setting would become whatever the UI rendered rather than what the
        user chose.  Another surface (cli, api, a second window) may have
        changed the entry, and the entry is the only record of what login
        will actually launch.
        """
        log.debug("refresh")
        r = self.dispatch(GetAutostartStatus())
        log.info("refresh: enabled=%s target=%s", r.enabled, r.target)
        self._autostart_check.blockSignals(True)
        self._autostart_check.setChecked(r.enabled)
        self._autostart_check.blockSignals(False)
        index = self._autostart_target.findData(
            r.target or DEFAULT_AUTOSTART_TARGET,
        )
        if index >= 0:
            self._autostart_target.blockSignals(True)
            self._autostart_target.setCurrentIndex(index)
            self._autostart_target.blockSignals(False)

    def _on_autostart_toggled(self, checked: bool) -> None:
        target = self._autostart_target.currentData()
        log.info("_on_autostart_toggled: checked=%s target=%s", checked, target)
        r = self.dispatch(
            EnableAutostart(target=target) if checked else DisableAutostart(),
        )
        self._status.setText(r.message)
        if not r.ok:   # refused: no AutostartChanged follows to put it back
            self.refresh()

    def _on_autostart_target_changed(self, index: int) -> None:
        """Re-install for the newly chosen target, but only if it is ON.

        Changing the picker while autostart is disabled must not enable it —
        the same invariant every refresh holds.
        """
        target = self._autostart_target.itemData(index)
        log.info("_on_autostart_target_changed: target=%s", target)
        if not self._autostart_check.isChecked():
            log.debug("_on_autostart_target_changed: disabled — not installing")
            return
        r = self.dispatch(EnableAutostart(target=target))
        self._status.setText(r.message)

    def _on_check_update(self) -> None:
        log.info("_on_check_update")
        r = self.dispatch(CheckForUpdate())
        if not r.ok:
            log.warning("_on_check_update: failed — %s", r.message)
            self._status.setText(f"Update check failed: {r.message}")
            return
        if r.latest_version and r.latest_version != r.local_version:
            log.info("_on_check_update: %s available (have %s)",
                     r.latest_version, r.local_version)
            self._status.setText(
                f"Update available: {r.latest_version} "
                f"(you have {r.local_version}). "
                f'<a href="{r.release_url}">Release notes</a> — '
                'press "Upgrade now…" to install it.',
            )
        else:
            self._status.setText(f"Up to date ({r.local_version}).")

    def _on_upgrade(self) -> None:
        """Run the package-manager upgrade, after showing exactly what runs.

        ``RunUpgrade`` shells out through the system package manager under
        sudo, so it is confirmed first -- the CLI refuses the same Command
        without ``--yes`` for this reason.  ``dry_run`` asks the Command
        itself what it WOULD run, so the confirmation quotes the real command
        line instead of a UI's guess at it.
        """
        log.info("_on_upgrade: asking the Command what it would run")
        preview = self.dispatch(RunUpgrade(dry_run=True))
        if not preview.ok:
            log.warning("_on_upgrade: unavailable — %s", preview.message)
            self._status.setText(f"Upgrade unavailable: {preview.message}")
            return
        answer = QMessageBox.question(
            self, "Upgrade TRCC",
            f"{preview.message}\n\nThis runs as root. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer is not QMessageBox.StandardButton.Yes:
            log.info("_on_upgrade: declined by the user")
            self._status.setText("Upgrade cancelled.")
            return
        log.info("_on_upgrade: confirmed — running")
        r = self.dispatch(RunUpgrade(dry_run=False))
        self._status.setText(
            r.message if r.ok else f"Upgrade failed: {r.message}",
        )
