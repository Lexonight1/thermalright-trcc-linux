"""Shared system-tray behaviour for the Qt UIs (gui + qtgui).

Both windows want the same tray: an icon with Show/Hide + Exit, click to toggle
visibility, and a window-close that hides (or minimises) to the tray instead of
quitting — so the LCD keeps running — unless the user explicitly Exits.  This
lived only in ``ui/gui/trcc_app.py``; extracted here so both skins share ONE
implementation.

Composition, not a mixin: ``QMainWindow`` / ``QFrame`` + ``ABC`` raises a
metaclass ``TypeError`` (see ``BasePanel``), and a plain controller keeps the
tray policy in one testable place.  Each window owns its OWN cleanup on a real
quit — the controller only decides *hide-to-tray vs. real-quit*.

Usage::

    self._tray = TrayController(self, minimize_on_close=..., icon=...)
    self._tray.install()

    def closeEvent(self, event):
        if self._tray.intercept_close(event):
            return                       # diverted to the tray
        ...skin-specific cleanup...
        event.accept()                   # a genuine quit
"""
from __future__ import annotations

import logging
from typing import Any

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon, QWidget

log = logging.getLogger(__name__)


class TrayController:
    """Owns a window's tray icon + its hide-to-tray close behaviour."""

    def __init__(
        self,
        window: QWidget,
        *,
        minimize_on_close: bool,
        icon: QIcon,
        tooltip: str = "TRCC Linux",
    ) -> None:
        self._window = window
        self._minimize_on_close = minimize_on_close
        self._icon = icon
        self._tooltip = tooltip
        self._force_quit = False
        self._minimized_to_taskbar = False
        self._tray: QSystemTrayIcon | None = None

    def install(self) -> None:
        """Create the tray icon + Show/Hide + Exit menu, and show it."""
        log.info("TrayController.install: minimize_on_close=%s",
                 self._minimize_on_close)
        self._window.setWindowIcon(self._icon)
        tray = QSystemTrayIcon(self._icon, self._window)
        tray.setToolTip(self._tooltip)
        menu = QMenu()
        if (show_action := menu.addAction("Show/Hide")):
            show_action.triggered.connect(self.toggle_visibility)
        menu.addSeparator()
        if (exit_action := menu.addAction("Exit")):
            exit_action.triggered.connect(self.request_quit)
        tray.setContextMenu(menu)
        tray.activated.connect(self._on_activated)
        tray.show()
        self._tray = tray

    # ── State ─────────────────────────────────────────────────────────

    @property
    def minimized_to_taskbar(self) -> bool:
        return self._minimized_to_taskbar

    def clear_minimized(self) -> None:
        """Reset the minimised flag — call when raising from tray/taskbar."""
        self._minimized_to_taskbar = False

    # ── Actions ───────────────────────────────────────────────────────

    def _on_activated(self, reason: Any) -> None:
        log.info("TrayController: activated reason=%s", reason)
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.toggle_visibility()

    def toggle_visibility(self) -> None:
        if self._window.isVisible():
            log.info("TrayController.toggle_visibility: hide")
            self._window.hide()
        else:
            self.raise_window()

    def raise_window(self) -> None:
        log.info("TrayController.raise_window")
        self._minimized_to_taskbar = False
        self._window.show()
        self._window.activateWindow()
        self._window.raise_()

    def request_quit(self) -> None:
        log.info("TrayController.request_quit")
        self._force_quit = True
        self._window.close()

    def notify(self, title: str, message: str, msecs: int = 8000) -> None:
        if self._tray is not None and QSystemTrayIcon.isSystemTrayAvailable():
            self._tray.showMessage(
                title, message, QSystemTrayIcon.MessageIcon.Warning, msecs,
            )

    # ── Close ─────────────────────────────────────────────────────────

    def intercept_close(self, event: Any) -> bool:
        """Divert a window-close to the tray, or let it proceed to a real quit.

        Returns ``True`` when the close was diverted (the caller must return
        early); ``False`` when this is a genuine quit (the caller does its own
        cleanup and accepts the event).  Mirrors gui's original ``closeEvent``.
        """
        if (
            not self._force_quit
            and self._tray is not None
            and self._tray.isSystemTrayAvailable()
            and self._tray.isVisible()
            and not (self._minimize_on_close and self._minimized_to_taskbar)
        ):
            event.ignore()
            if self._minimize_on_close:
                log.info("TrayController.intercept_close: minimise to tray")
                self._minimized_to_taskbar = True
                self._window.showMinimized()
            else:
                log.info("TrayController.intercept_close: hide to tray")
                self._window.hide()
            return True
        log.info("TrayController.intercept_close: real quit")
        self._minimized_to_taskbar = False
        if self._tray is not None:
            self._tray.hide()
        return False
