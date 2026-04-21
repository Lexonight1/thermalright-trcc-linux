"""DevicePanel — discover / connect / disconnect devices."""
from __future__ import annotations

from typing import Optional

from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ....app import App
from ....core.commands import ConnectDevice, DisconnectDevice, DiscoverDevices


class DevicePanel(QWidget):
    """Lists detected devices and lets the user connect/disconnect each."""

    def __init__(self, app: App, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._app = app

        self._list = QListWidget()
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)

        self._scan_btn = QPushButton("Scan")
        self._scan_btn.clicked.connect(self._on_scan)

        self._connect_btn = QPushButton("Connect")
        self._connect_btn.clicked.connect(self._on_connect)

        self._disconnect_btn = QPushButton("Disconnect")
        self._disconnect_btn.clicked.connect(self._on_disconnect)

        self._status = QLabel("No devices scanned yet.")

        buttons = QHBoxLayout()
        buttons.addWidget(self._scan_btn)
        buttons.addWidget(self._connect_btn)
        buttons.addWidget(self._disconnect_btn)
        buttons.addStretch(1)

        root = QVBoxLayout(self)
        root.addLayout(buttons)
        root.addWidget(self._list, stretch=1)
        root.addWidget(self._status)

    # ── Actions ───────────────────────────────────────────────────────

    def _on_scan(self) -> None:
        result = self._app.dispatch(DiscoverDevices())
        self._list.clear()
        for product in result.products:
            item = QListWidgetItem(
                f"{product.key}  —  {product.vendor} {product.product}  "
                f"({product.wire.value}, {product.native_resolution[0]}×"
                f"{product.native_resolution[1]})"
            )
            item.setData(0x0100, product.key)  # Qt.UserRole
            self._list.addItem(item)
        self._status.setText(result.message)

    def _selected_key(self) -> Optional[str]:
        item = self._list.currentItem()
        if item is None:
            self._status.setText("Select a device first.")
            return None
        return str(item.data(0x0100))

    def _on_connect(self) -> None:
        key = self._selected_key()
        if key is None:
            return
        result = self._app.dispatch(ConnectDevice(key=key))
        self._status.setText(result.message)

    def _on_disconnect(self) -> None:
        key = self._selected_key()
        if key is None:
            return
        result = self._app.dispatch(DisconnectDevice(key=key))
        self._status.setText(result.message)
