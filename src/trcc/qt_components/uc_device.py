"""
PyQt6 UCDevice - Device sidebar panel.

Matches Windows TRCC.UCDevice (180x800)
Shows connected LCD devices as clickable buttons.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QLabel, QPushButton, QWidget

from ..device_scsi import find_lcd_devices
from .assets import Assets, asset_exists
from .base import BasePanel, create_image_button, set_background_pixmap
from .constants import Colors, Layout, Sizes

# Map device model names to A1 image base names (without .png)
DEVICE_IMAGE_MAP = {
    'CZTV': 'A1CZTV',
    'CZ1': 'A1CZ1',
    'FROZEN_WARFRAME': 'A1FROZEN WARFRAME',
    'FROZEN_WARFRAME_PRO': 'A1FROZEN WARFRAME PRO',
    'FROZEN_WARFRAME_SE': 'A1FROZEN WARFRAME SE',
    'FROZEN_HORIZON_PRO': 'A1FROZEN HORIZON PRO',
    'FROZEN_MAGIC_PRO': 'A1FROZEN MAGIC PRO',
    'FROZEN_VISION_V2': 'A1FROZEN VISION V2',
    'AX120_DIGITAL': 'A1AX120 DIGITAL',
    'HR10_2280_PRO_DIGITAL': 'A1HR10 2280 PRO DIGITAL',
    'AK120_DIGITAL': 'A1AK120 Digital',
    'PA120_DIGITAL': 'A1PA120 DIGITAL',
    'RK120_DIGITAL': 'A1RK120 DIGITAL',
    'AS120_VISION': 'A1AS120 VISION',
    'BA120_VISION': 'A1BA120 VISION',
    'RP130_VISION': 'A1RP130 VISION',
    'CORE_VISION': 'A1CORE VISION',
    'ELITE_VISION': 'A1ELITE VISION',
    'GRAND_VISION': 'A1GRAND VISION',
    'HYPER_VISION': 'A1HYPER VISION',
    'Mjolnir_VISION': 'A1Mjolnir VISION',
    'Mjolnir_VISION_PRO': 'A1Mjolnir VISION PRO',
    'Stream_Vision': 'A1Stream Vision',
    'KVMALEDC6': 'A1KVMALEDC6',
    'LC1': 'A1LC1',
    'LC2': 'A1LC2',
    'LC2JD': 'A1LC2JD',
    'LC3': 'A1LC3',
    'LC5': 'A1LC5',
    'LF8': 'A1LF8',
    'LF10': 'A1LF10',
    'LF11': 'A1LF11',
    'LF12': 'A1LF12',
    'LF13': 'A1LF13',
    'LF14': 'A1LF14',
    'LF15': 'A1LF15',
    'LF16': 'A1LF16',
    'LF18': 'A1LF18',
    'LF19': 'A1LF19',
    'LM16SE': 'A1LM16SE',
    'LM22': 'A1LM22',
    'LM24': 'A1LM24',
    'LM26': 'A1LM26',
    'LM27': 'A1LM27',
}


def _get_device_images(device_info: dict) -> tuple[str | None, str | None]:
    """Get normal and active image names for a device.

    Returns:
        (normal_image_name, active_image_name) or (None, None)

    For HID devices with the generic default button_image (A1CZTV), returns
    (None, None) so the text name is shown instead of the misleading default
    image.  After the HID handshake resolves the actual product, the button
    image is updated via PM_TO_BUTTON_IMAGE.
    """
    button_image = device_info.get('button_image', '')
    protocol = device_info.get('protocol', 'scsi')

    # For HID devices, skip the generic A1CZTV default — show text name
    # until the handshake identifies the actual product.
    if protocol == 'hid' and button_image == 'A1CZTV':
        return None, None

    # Try button_image field first (from DetectedDevice)
    if button_image:
        normal = f"{button_image}.png"
        active = f"{button_image}a.png"
        if asset_exists(normal):
            return normal, active

        spaced = button_image.replace('_', ' ')
        normal = f"{spaced}.png"
        active = f"{spaced}a.png"
        if asset_exists(normal):
            return normal, active

    # Try model field
    model = device_info.get('model', '')
    if model in DEVICE_IMAGE_MAP:
        base = DEVICE_IMAGE_MAP[model]
        normal = f"{base}.png"
        active = f"{base}a.png"
        if asset_exists(normal):
            return normal, active

    # Try name field as fallback
    name = device_info.get('name', '')
    for model_key, img_base in DEVICE_IMAGE_MAP.items():
        if model_key.lower() in name.lower():
            normal = f"{img_base}.png"
            active = f"{img_base}a.png"
            if asset_exists(normal):
                return normal, active

    # Default to CZTV for non-HID devices
    if protocol != 'hid' and asset_exists('A1CZTV.png'):
        return 'A1CZTV.png', 'A1CZTVa.png'

    return None, None


class UCDevice(BasePanel):
    """Device sidebar panel.

    Windows: 180x800, background image A0硬件列表.png.
    All sidebar buttons (sensor, device, about) use create_image_button
    with checkable=True — setChecked() toggles QIcon State.On/Off.
    """

    CMD_SELECT_DEVICE = 1
    CMD_ABOUT = 240
    CMD_HOME = 512

    device_selected = Signal(dict)
    about_clicked = Signal()
    home_clicked = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent, width=Sizes.SIDEBAR_W, height=Sizes.SIDEBAR_H)

        self.devices: list[dict] = []
        self.device_buttons: list[QPushButton] = []
        self.selected_device: dict | None = None

        self._setup_ui()
        self._detect_devices()

    def _setup_ui(self) -> None:
        """Build the UI matching Windows UCDevice layout."""
        set_background_pixmap(self, Assets.SIDEBAR_BG,
            Sizes.SIDEBAR_W, Sizes.SIDEBAR_H,
            fallback_style=f"""
                UCDevice {{
                    background: qlineargradient(
                        x1:0, y1:0, x2:0, y2:1,
                        stop:0 #252525, stop:0.5 {Colors.BASE_BG}, stop:1 {Colors.THUMB_BG}
                    );
                    border-right: 1px solid {Colors.THUMB_BORDER};
                }}
            """)

        # Sensor / Home button
        self.sensor_btn = create_image_button(
            self, *Layout.SENSOR_BTN,
            Assets.SENSOR_BTN, Assets.SENSOR_BTN_ACTIVE,
            checkable=True, fallback_text="Sensor"
        )
        self.sensor_btn.setToolTip("System sensors")
        self.sensor_btn.clicked.connect(self._on_home_clicked)

        # Device buttons area
        self.device_area = QWidget(self)
        self.device_area.setGeometry(*Layout.DEVICE_AREA)

        # "No devices" labels
        self.no_devices_label = QLabel("No devices found", self.device_area)
        self.no_devices_label.setGeometry(*Layout.NO_DEVICES_LABEL)
        self.no_devices_label.setStyleSheet(
            f"color: {Colors.EMPTY_TEXT}; font-size: 10px; background: transparent;"
        )
        self.no_devices_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.no_devices_label.setWordWrap(True)

        self.hint_label = QLabel("Connect a Thermalright\nLCD cooler via USB", self.device_area)
        self.hint_label.setGeometry(*Layout.HINT_LABEL)
        self.hint_label.setStyleSheet(
            f"color: {Colors.MUTED_TEXT}; font-size: 9px; background: transparent;"
        )
        self.hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # About / Control Center button
        self.about_btn = create_image_button(
            self, *Layout.ABOUT_BTN,
            Assets.ABOUT_BTN, Assets.ABOUT_BTN_ACTIVE,
            checkable=True, fallback_text="About"
        )
        self.about_btn.setToolTip("Settings")
        self.about_btn.clicked.connect(self._on_about_clicked)

    def _build_device_buttons(self, devices: list[dict]) -> None:
        """Clear old buttons and create new device buttons.

        Every device gets a create_image_button with checkable=True.
        Selection is controlled via setChecked() — same as sensor/about.
        """
        for btn in self.device_buttons:
            btn.deleteLater()
        self.device_buttons.clear()

        has_devices = bool(devices)
        self.no_devices_label.setVisible(not has_devices)
        self.hint_label.setVisible(not has_devices)

        for i, device in enumerate(devices):
            normal_name, active_name = _get_device_images(device)
            x = Sizes.DEVICE_BTN_X
            y = i * Sizes.DEVICE_BTN_SPACING
            w = Sizes.DEVICE_BTN_W
            h = Sizes.DEVICE_BTN_H

            btn = create_image_button(
                self.device_area, x, y, w, h,
                normal_name, active_name,
                checkable=True,
                fallback_text=device.get('name', 'Unknown')[:18],
            )
            btn.device_info = device  # type: ignore[attr-defined]
            btn.clicked.connect(lambda _=False, d=device: self._on_device_clicked(d))
            btn.show()
            self.device_buttons.append(btn)

    def _detect_devices(self) -> None:
        """Detect connected LCD devices."""
        self.devices = find_lcd_devices()
        self._build_device_buttons(self.devices)
        if self.device_buttons:
            self._select_device(self.devices[0])

    def _on_device_clicked(self, device_info: dict) -> None:
        self._select_device(device_info)

    def _deselect_all_devices(self) -> None:
        """Deselect all device buttons (Windows: set all to bitmap1)."""
        for btn in self.device_buttons:
            btn.setChecked(False)

    def _deselect_header_buttons(self) -> None:
        """Deselect sensor and about buttons (Windows: set to inactive images)."""
        self.sensor_btn.setChecked(False)
        self.about_btn.setChecked(False)

    def _select_device(self, device_info: dict) -> None:
        """Select a device button — deselects sensor and about.

        Windows: userButton_Click sets clicked device to bitmap2 (active),
        all others to bitmap1, button1 to inactive, buttonSetting to inactive.
        """
        self._deselect_header_buttons()
        self.selected_device = device_info
        for btn in self.device_buttons:
            btn.setChecked(btn.device_info == device_info)  # type: ignore[attr-defined]
        self.device_selected.emit(device_info)
        self.invoke_delegate(self.CMD_SELECT_DEVICE, device_info)

    def _on_home_clicked(self) -> None:
        """Sensor/Home clicked — deselects about and all devices."""
        self.sensor_btn.setChecked(True)
        self.about_btn.setChecked(False)
        self._deselect_all_devices()
        self.home_clicked.emit()
        self.invoke_delegate(self.CMD_HOME)

    def _on_about_clicked(self) -> None:
        """About/Settings clicked — deselects sensor and all devices."""
        self.about_btn.setChecked(True)
        self.sensor_btn.setChecked(False)
        self._deselect_all_devices()
        self.about_clicked.emit()
        self.invoke_delegate(self.CMD_ABOUT)

    def update_devices(self, devices: list[dict]) -> None:
        """Update device list from hot-plug poller.

        Only rebuilds buttons if the set of device paths has changed.
        Preserves current selection when possible.
        """
        old_paths = {d.get('path') for d in self.devices}
        new_paths = {d.get('path') for d in devices}
        if old_paths == new_paths:
            return

        prev_path = self.selected_device.get('path') if self.selected_device else None
        self.devices = devices
        self._build_device_buttons(devices)

        if not devices:
            self.selected_device = None
            return

        # Restore previous selection or select first device
        restored = False
        if prev_path:
            for d in devices:
                if d.get('path') == prev_path:
                    self._select_device(d)
                    restored = True
                    break
        if not restored:
            self._select_device(devices[0])

    def restore_device_selection(self) -> None:
        """Re-activate current device button and deselect header buttons.

        Called when returning to form view from About or System Info.
        """
        self._deselect_header_buttons()
        if self.selected_device:
            for btn in self.device_buttons:
                btn.setChecked(btn.device_info == self.selected_device)  # type: ignore[attr-defined]

    def get_selected_device(self) -> dict | None:
        return self.selected_device

    def get_devices(self) -> list[dict]:
        return self.devices
