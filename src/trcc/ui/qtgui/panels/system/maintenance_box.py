"""MaintenanceBox — autostart, update check, in-app upgrade.

Three things a user does to the *install* rather than to a device, which
is why they sit together and away from the live readouts.
"""
from __future__ import annotations

import html
import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QLabel,
    QPushButton,
)

from .....core.commands import (
    CheckForUpdate,
    DisableAutostart,
    EnableAutostart,
    GetAutostartStatus,
    RunUpgrade,
    UpdateStatus,
)
from .....core.models import AUTOSTART_TARGETS, DEFAULT_AUTOSTART_TARGET
from ....qt_background import dispatch_in_background
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
        # The App checks for updates (at session start, then hourly); this
        # shows each answer it publishes, as the classic window does.
        self._bus.update_checked.connect(
            self._show_update, type=Qt.ConnectionType.QueuedConnection)
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
        update = self.dispatch(UpdateStatus())      # the App's last answer
        if update.ok:
            self._show_update(update)

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
        """Ask now -- off the GUI thread, which used to freeze while GitHub
        answered."""
        log.info("_on_check_update")
        self._status.setText("Checking for updates…")
        dispatch_in_background(self._app, CheckForUpdate(), self._show_update)

    def _show_update(self, answer: object) -> None:
        """An answer -- the button's, the App's last, or ``UpdateChecked``:
        all carry ok / update_available / the versions / the release URL."""
        ok = bool(getattr(answer, "ok", False))
        local = str(getattr(answer, "local_version", ""))
        latest = str(getattr(answer, "latest_version", ""))
        if not ok:
            message = str(getattr(answer, "message", ""))
            log.warning("_show_update: failed — %s", message)
            self._status.setText(f"Update check failed: {html.escape(message)}")
            return
        if getattr(answer, "update_available", False) and latest:
            url = str(getattr(answer, "release_url", ""))
            log.info("_show_update: %s available (have %s)", latest, local)
            self._status.setText(
                f"Update available: {html.escape(latest)} "
                f"(you have {html.escape(local)}). "
                f'<a href="{html.escape(url)}">Release notes</a> — '
                'press "Upgrade now…" to install it.',
            )
        else:
            log.info("_show_update: up to date at %s", local)
            self._status.setText(f"Up to date ({html.escape(local)}).")

    def _on_upgrade(self) -> None:
        """Show the command that upgrades this install (nothing is run).

        It used to confirm, then run the package manager as root -- which
        upgraded everything but TRCC.  ``RunUpgrade`` now answers with the
        right command for how TRCC was installed.
        """
        r = self.dispatch(RunUpgrade())
        log.info("_on_upgrade: %s", r.message)
        self._status.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        # The label renders rich text (release links); the command has "&&".
        self._status.setText(html.escape(r.message).replace("\n", "<br>"))
