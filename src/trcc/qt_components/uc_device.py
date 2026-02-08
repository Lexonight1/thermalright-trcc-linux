"""
PyQt6 UCDevice - Device sidebar panel.

Matches Windows TRCC.UCDevice (180x800)
Shows connected LCD devices as clickable buttons.
"""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QLabel, QPushButton, QWidget

from .assets import Assets, asset_exists, load_pixmap
from .base import BasePanel, create_image_button, set_background_pixmap
from .constants import Colors, Layout, Sizes, Styles

# Import device detection
try:
    from ..scsi_device import find_lcd_devices
    SCSI_AVAILABLE = True
except ImportError:
    SCSI_AVAILABLE = False

    def find_lcd_devices():
        return []


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


def _get_device_images(device_info):
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


class DeviceButton(QPushButton):
    """
    Single device button with image.

    Windows: FlatStyle button at (25, 160 + i*60), Size(140, 50).
    """

    device_clicked = pyqtSignal(dict)

    def __init__(self, device_info, parent=None):
        super().__init__(parent)

        self.device_info = device_info
        self.selected = False
        self._has_images = False

        self.setFixedSize(Sizes.DEVICE_BTN_W, Sizes.DEVICE_BTN_H)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFlat(True)
        self.setCheckable(True)
        self.setStyleSheet(Styles.FLAT_BUTTON)

        # Load device images
        normal_name, active_name = _get_device_images(device_info)
        if normal_name:
            normal_pix = load_pixmap(normal_name, Sizes.DEVICE_BTN_W, Sizes.DEVICE_BTN_H)
            active_pix = load_pixmap(active_name, Sizes.DEVICE_BTN_W, Sizes.DEVICE_BTN_H)

            if not normal_pix.isNull():
                self._has_images = True
                icon = QIcon(normal_pix)
                if not active_pix.isNull():
                    icon.addPixmap(active_pix, QIcon.Mode.Normal, QIcon.State.On)
                self.setIcon(icon)
                self.setIconSize(self.size())
                self._img_refs = [normal_pix, active_pix]

        if not self._has_images:
            name = device_info.get('name', device_info.get('path', 'Unknown'))
            if len(name) > 18:
                name = name[:17] + '…'
            self.setText(name)
            self._apply_text_style(False)

        self.clicked.connect(self._on_clicked)

    def _on_clicked(self):
        self.device_clicked.emit(self.device_info)

    def _apply_text_style(self, selected):
        if selected:
            self.setStyleSheet(f"""
                QPushButton {{
                    background: qlineargradient(
                        x1:0, y1:0, x2:0, y2:1,
                        stop:0 {Colors.DEVICE_SELECTED_TOP}, stop:1 {Colors.DEVICE_SELECTED_BOTTOM}
                    );
                    color: white; border: 1px solid {Colors.DEVICE_SELECTED_BORDER};
                    border-radius: 3px; font-size: 10px;
                }}
            """)
        else:
            self.setStyleSheet(f"""
                QPushButton {{
                    background: qlineargradient(
                        x1:0, y1:0, x2:0, y2:1,
                        stop:0 {Colors.DEVICE_NORMAL_TOP}, stop:1 {Colors.DEVICE_NORMAL_BOTTOM}
                    );
                    color: #E0E0E0; border: 1px solid {Colors.DEVICE_NORMAL_BORDER};
                    border-radius: 3px; font-size: 10px;
                }}
                QPushButton:hover {{
                    background: qlineargradient(
                        x1:0, y1:0, x2:0, y2:1,
                        stop:0 {Colors.DEVICE_HOVER_TOP}, stop:1 {Colors.DEVICE_HOVER_BOTTOM}
                    );
                    border: 1px solid {Colors.HOVER_BORDER};
                }}
            """)

    def set_selected(self, selected):
        """Set selection state."""
        self.selected = selected
        if self._has_images:
            self.setChecked(selected)
        else:
            self._apply_text_style(selected)


class UCDevice(BasePanel):
    """
    Device sidebar panel.

    Windows: 180x800, background image A0硬件列表.png.
    """

    CMD_SELECT_DEVICE = 1
    CMD_ABOUT = 240
    CMD_HOME = 512

    device_selected = pyqtSignal(dict)
    about_clicked = pyqtSignal()
    home_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent, width=Sizes.SIDEBAR_W, height=Sizes.SIDEBAR_H)

        self.devices = []
        self.device_buttons = []
        self.selected_device = None

        self._setup_ui()
        self._detect_devices()

    def _setup_ui(self):
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

        # Sensor / Home button (checkable for active/inactive state)
        self.sensor_btn = create_image_button(
            self, *Layout.SENSOR_BTN,
            Assets.SENSOR_BTN, Assets.SENSOR_BTN_ACTIVE,
            checkable=True, fallback_text="Sensor"
        )
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

        # About / Control Center button (checkable for active/inactive state)
        self.about_btn = create_image_button(
            self, *Layout.ABOUT_BTN,
            Assets.ABOUT_BTN, Assets.ABOUT_BTN_ACTIVE,
            checkable=True, fallback_text="About"
        )
        self.about_btn.clicked.connect(self._on_about_clicked)

    def _build_device_buttons(self, devices):
        """Clear old buttons and create new DeviceButton for each device."""
        for btn in self.device_buttons:
            btn.deleteLater()
        self.device_buttons.clear()

        has_devices = bool(devices)
        self.no_devices_label.setVisible(not has_devices)
        self.hint_label.setVisible(not has_devices)

        for i, device in enumerate(devices):
            btn = DeviceButton(device, self.device_area)
            btn.setGeometry(
                Sizes.DEVICE_BTN_X,
                i * Sizes.DEVICE_BTN_SPACING,
                Sizes.DEVICE_BTN_W,
                Sizes.DEVICE_BTN_H,
            )
            btn.device_clicked.connect(self._on_device_clicked)
            btn.show()
            self.device_buttons.append(btn)

    def _detect_devices(self):
        """Detect connected LCD devices."""
        if SCSI_AVAILABLE:
            self.devices = find_lcd_devices()
        else:
            self.devices = []

        self._build_device_buttons(self.devices)

        if self.device_buttons:
            self._select_device(self.devices[0])

    def _on_device_clicked(self, device_info):
        self._select_device(device_info)

    def _deselect_all_devices(self):
        """Deselect all device buttons (Windows: set all to bitmap1)."""
        for btn in self.device_buttons:
            btn.set_selected(False)

    def _deselect_header_buttons(self):
        """Deselect sensor and about buttons (Windows: set to inactive images)."""
        self.sensor_btn.setChecked(False)
        self.about_btn.setChecked(False)

    def _select_device(self, device_info):
        """Select a device button — deselects sensor and about.

        Windows: userButton_Click sets clicked device to bitmap2 (active),
        all others to bitmap1, button1 to inactive, buttonSetting to inactive.
        """
        self._deselect_header_buttons()
        self.selected_device = device_info
        for btn in self.device_buttons:
            btn.set_selected(btn.device_info == device_info)
        self.device_selected.emit(device_info)
        self.invoke_delegate(self.CMD_SELECT_DEVICE, device_info)

    def _on_home_clicked(self):
        """Sensor/Home clicked — deselects about and all devices.

        Windows: button1_Click sets button1 to A1传感器a (active),
        buttonSetting to A1关于 (inactive), all devices to bitmap1.
        """
        self.sensor_btn.setChecked(True)
        self.about_btn.setChecked(False)
        self._deselect_all_devices()
        self.home_clicked.emit()
        self.invoke_delegate(self.CMD_HOME)

    def _on_about_clicked(self):
        """About/Settings clicked — deselects sensor and all devices.

        Windows: buttonSetting_Click sets buttonSetting to A1关于a (active),
        button1 to A1传感器 (inactive), all devices to bitmap1.
        """
        self.about_btn.setChecked(True)
        self.sensor_btn.setChecked(False)
        self._deselect_all_devices()
        self.about_clicked.emit()
        self.invoke_delegate(self.CMD_ABOUT)

    def update_devices(self, devices):
        """Update device list from hot-plug poller.

        Only rebuilds buttons if the set of device paths has changed.
        Preserves current selection when possible.
        """
        old_paths = {d.get('path') for d in self.devices}
        new_paths = {d.get('path') for d in devices}
        if old_paths == new_paths:
            return  # No change

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

    def restore_device_selection(self):
        """Re-activate current device button and deselect header buttons.

        Called when returning to form view from About or System Info.
        """
        self._deselect_header_buttons()
        if self.selected_device:
            for btn in self.device_buttons:
                btn.set_selected(btn.device_info == self.selected_device)

    def get_selected_device(self):
        return self.selected_device

    def get_devices(self):
        return self.devices
