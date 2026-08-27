"""DevicePanel — discover / connect / disconnect + a read-only protocol inspector.

The inspector is the first developer-facing capability of the qtgui skin: it
surfaces the live device's handshake (PM / SUB / FBL), its resolved render +
encode profile, and the last wire-frame size — all from data the App already
computes (``device.handshake`` / ``device.profile`` + the ``FrameSent`` event).
Pure introspection, no new commands.
"""
from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

from ....core.commands import ConnectDevice, DeviceState, DisconnectDevice, DiscoverDevices
from ..base import BasePanel

log = logging.getLogger(__name__)

_MONO = "font-family: monospace; font-size: 11px;"
_USER_ROLE = 0x0100  # Qt.ItemDataRole.UserRole


class DevicePanel(BasePanel):
    """Lists detected devices, connect/disconnect, and inspects the live one."""

    def _setup_ui(self) -> None:
        self._last_bytes: dict[str, int] = {}

        self._list = QListWidget(self)
        self._list.setSelectionMode(
            QAbstractItemView.SelectionMode.SingleSelection,
        )
        self._list.currentItemChanged.connect(self._refresh_inspector)

        self._scan_btn = QPushButton("Scan", self)
        self._scan_btn.clicked.connect(self._on_scan)

        self._connect_btn = QPushButton("Connect", self)
        self._connect_btn.clicked.connect(self._on_connect)

        self._disconnect_btn = QPushButton("Disconnect", self)
        self._disconnect_btn.clicked.connect(self._on_disconnect)

        self._status = QLabel("No devices scanned yet.", self)

        # ── Inspector (developer read-out) ──────────────────────────
        self._inspector = QLabel("Select a device to inspect.", self)
        self._inspector.setStyleSheet(_MONO)
        self._inspector.setWordWrap(True)
        self._inspector.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse,
        )
        self._inspector.setAlignment(
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft,
        )
        inspector_box = QGroupBox("Inspector", self)
        box_layout = QVBoxLayout(inspector_box)
        box_layout.addWidget(self._inspector)

        buttons = QHBoxLayout()
        buttons.addWidget(self._scan_btn)
        buttons.addWidget(self._connect_btn)
        buttons.addWidget(self._disconnect_btn)
        buttons.addStretch(1)

        root = QVBoxLayout(self)
        root.addLayout(buttons)
        root.addWidget(self._list, stretch=1)
        root.addWidget(inspector_box, stretch=2)
        root.addWidget(self._status)

        # Live refresh: (re)connect + every wire frame the device sends.
        self._bus.device_connected.connect(self._refresh_inspector)
        self._bus.device_disconnected.connect(self._refresh_inspector)
        self._bus.frame_sent.connect(self._on_frame_sent)

    # ── Actions ───────────────────────────────────────────────────────

    def _on_scan(self) -> None:
        log.info("_on_scan")
        result = self.dispatch(DiscoverDevices())
        self._list.clear()
        for product in result.products:
            item = QListWidgetItem(
                f"{product.key}  —  {product.vendor} {product.product}  "
                f"({product.wire.value}, {product.native_resolution[0]}×"
                f"{product.native_resolution[1]})"
            )
            item.setData(_USER_ROLE, product.key)
            self._list.addItem(item)
        self._status.setText(result.message)

    def _selected_key(self) -> str | None:
        item = self._list.currentItem()
        if item is None:
            self._status.setText("Select a device first.")
            return None
        return str(item.data(_USER_ROLE))

    def _current_key(self) -> str | None:
        """The selected key without mutating the status line."""
        item = self._list.currentItem()
        return str(item.data(_USER_ROLE)) if item is not None else None

    def _on_connect(self) -> None:
        log.info("_on_connect")
        key = self._selected_key()
        if key is None:
            return
        result = self.dispatch(ConnectDevice(key=key))
        self._status.setText(result.message)
        self._refresh_inspector()

    def _on_disconnect(self) -> None:
        log.info("_on_disconnect")
        key = self._selected_key()
        if key is None:
            return
        result = self.dispatch(DisconnectDevice(key=key))
        self._status.setText(result.message)
        self._refresh_inspector()

    # ── Inspector ─────────────────────────────────────────────────────

    def _on_frame_sent(self, event: object) -> None:
        key = getattr(event, "key", None)
        n = getattr(event, "bytes_sent", None)
        if key is None or n is None:
            return
        self._last_bytes[str(key)] = int(n)
        if str(key) == self._current_key():
            self._refresh_inspector()

    def _refresh_inspector(self, *_args: object) -> None:
        key = self._current_key()
        if not key:
            self._inspector.setText("Select a device to inspect.")
            return
        state = self.dispatch(DeviceState(key=key))
        if not state.ok:
            self._inspector.setText(
                f"{key} — not connected.\n"
                "Connect it to read its handshake + profile.",
            )
            return

        lines = [
            f"Device        {state.key}",
            f"  wire        {state.wire}",
            f"  native res  {state.native_resolution[0]}×{state.native_resolution[1]}",
        ]

        # ``None`` means "not handshaken yet" and is distinct from a real 0 —
        # the Result keeps them apart precisely so an inspector can.
        if state.pm_byte is not None and state.sub_byte is not None:
            lines += [
                "",
                "Handshake",
                f"  PM          {state.pm_byte}",
                f"  SUB         {state.sub_byte}",
                f"  FBL         {state.fbl}",
                f"  serial      {state.serial or '—'}",
            ]

        if state.resolution is not None:
            enc = "JPEG" if state.jpeg else f"RGB565 ({state.byte_order})"
            lines += [
                "",
                "Profile (render / encode)",
                f"  resolution  {state.resolution[0]}×{state.resolution[1]}",
                f"  encoding    {enc}",
                f"  rotate      {state.rotate}",
                f"  widescreen  {state.widescreen}",
                f"  encode_base {state.encode_base}°  invert={state.encode_invert}",
                f"  baseline    {state.encode_baseline}°",
            ]

        last = self._last_bytes.get(key)
        if last is not None:
            lines += ["", "Live", f"  last frame  {last} bytes"]

        self._inspector.setText("\n".join(lines))
