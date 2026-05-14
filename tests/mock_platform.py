"""Mock platform — noop protocols at the USB boundary, everything else real.

Shared by dev/mock_gui.py and test fixtures. Follows the real app flow:
discovery → builder.build_device() → device.connect() → handler.

Usage in tests:
    from tests.mock_platform import MockPlatform, DEFAULT_DEVICES
    platform = MockPlatform(DEFAULT_DEVICES)
    builder = ControllerBuilder(platform)
    ...
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from trcc.adapters.device.factory import DeviceProtocol, ProtocolInfo
from trcc.core.ports import (
    DoctorPlatformConfig,
    Platform,
    ReportPlatformConfig,
    SensorEnumerator,
)

# ═════════════════════════════════════════════════════════════════════════════
# Noop protocols — the only fake at the USB boundary
# ═════════════════════════════════════════════════════════════════════════════


class NoopLCDProtocol(DeviceProtocol):
    """Noop LCD protocol — returns canned HandshakeResult, discards frames."""

    def __init__(self, resolution: tuple[int, int], fbl: int,
                 pm: int, sub: int):
        super().__init__()
        self._resolution = resolution
        self._fbl = fbl
        self._pm = pm
        self._sub = sub

    def _do_handshake(self):
        from trcc.core.models import HandshakeResult
        return HandshakeResult(
            resolution=self._resolution,
            model_id=self._fbl,
            pm_byte=self._pm,
            sub_byte=self._sub,
        )

    def send_data(self, image_data: bytes, width: int, height: int) -> bool:
        return True

    def close(self) -> None:
        pass

    def get_info(self) -> ProtocolInfo:
        return ProtocolInfo(protocol="noop-lcd", active_backend="noop")

    @property
    def protocol_name(self) -> str:
        return "noop-lcd"

    @property
    def is_available(self) -> bool:
        return True


class NoopLEDProtocol(DeviceProtocol):
    """Noop LED protocol — returns canned LedHandshakeInfo, discards data."""

    def __init__(self, pm: int, sub: int = 0):
        super().__init__()
        self._pm = pm
        self._sub = sub

    def send_data(self, colors, segment_on, global_on, brightness) -> bool:
        return True

    def _do_handshake(self):
        from trcc.core.models import LedHandshakeInfo, PmRegistry
        style = PmRegistry.get_style(self._pm, self._sub)
        model = PmRegistry.get_model_name(self._pm, self._sub)
        entry = PmRegistry.resolve(self._pm, self._sub)
        return LedHandshakeInfo(
            model_id=self._pm,
            pm=self._pm,
            sub_type=self._sub,
            style=style,
            model_name=model,
            style_sub=entry.style_sub if entry else 0,
        )

    def close(self) -> None:
        pass

    def get_info(self) -> ProtocolInfo:
        return ProtocolInfo(protocol="noop-led", active_backend="noop")

    @property
    def protocol_name(self) -> str:
        return "noop-led"

    @property
    def is_available(self) -> bool:
        return True

    @property
    def is_led(self) -> bool:
        return True


# ═════════════════════════════════════════════════════════════════════════════
# MockPlatform — real Platform subclass, noop USB, configurable paths
# ═════════════════════════════════════════════════════════════════════════════


class MockPlatform(Platform):
    """Platform subclass for tests — noop USB, real sensors, temp paths.

    Same DI flow as production: builder gets a Platform, injects it.
    The only difference: detect_fn returns mock devices, protocols are noop.
    """

    def __init__(self, device_specs: list[dict],
                 root: Path | None = None) -> None:
        super().__init__()
        self._specs = device_specs
        self._root = root or Path('/tmp/.trcc-test')
        self._register_noop_protocols()

    # ── Path overrides (point to temp dir for test isolation) ─────────

    def config_dir(self) -> str:
        return str(self._root)

    def data_dir(self) -> str:
        return str(self._root / 'data')

    def user_content_dir(self) -> str:
        return str(self._root.parent / '.trcc-user')

    def web_dir(self, width: int, height: int) -> str:
        from trcc.core.paths import web_dir_name
        return str(self._root / 'data' / 'web' / web_dir_name(width, height))

    def web_masks_dir(self, width: int, height: int) -> str:
        from trcc.core.paths import masks_dir_name
        return str(self._root / 'data' / 'web' / masks_dir_name(width, height))

    def user_masks_dir(self, width: int, height: int) -> str:
        from trcc.core.paths import masks_dir_name
        return str(self._root.parent / '.trcc-user' / 'data' / 'web'
                   / masks_dir_name(width, height))

    # ── Abstract implementations ─────────────────────────────────────

    def _make_metrics(self):
        """Mock platform reuses Linux's metrics composer for test convenience."""
        from trcc.adapters.system.linux_platform import LinuxMetrics
        return LinuxMetrics(self.sensors, {})

    def _make_sensor_enumerator(self) -> SensorEnumerator:
        from trcc.adapters.system.linux_platform import (
            SensorEnumerator as LinuxSensors,
        )
        return LinuxSensors()

    def create_scsi_transport(self, path: str,
                              vid: int = 0, pid: int = 0,
                              *, usb_address=None) -> Any:
        return None  # noop — protocols handle everything

    def detect_devices(self) -> list:
        from trcc.core.models import ALL_DEVICES, DetectedDevice

        devices: list[DetectedDevice] = []
        for i, spec in enumerate(self._specs):
            dtype = spec.get('type', 'lcd')
            vid = int(spec.get('vid', '0402'), 16)
            pid = int(spec.get('pid', '3922'), 16)
            name = spec.get('name', f'Mock {dtype.upper()} {i}')
            entry = ALL_DEVICES.get((vid, pid))

            if dtype == 'lcd':
                path = f"mock:lcd:{i}:{vid:04x}:{pid:04x}"
                devices.append(DetectedDevice(
                    vid=vid, pid=pid,
                    vendor_name=entry.vendor if entry else "Mock",
                    product_name=name,
                    usb_path=path, scsi_device=path,
                    protocol=entry.protocol if entry else "scsi",
                    device_type=entry.device_type if entry else 1,
                    implementation=entry.implementation if entry else "ali_corp_lcd_v1",
                    model=spec.get('model', entry.model if entry else 'FROZEN_WARFRAME'),
                    button_image=spec.get('button_image',
                                          entry.button_image if entry else "A1CZTV"),
                ))
            elif dtype == 'led':
                path = f"mock:led:{i}:{vid:04x}:{pid:04x}"
                devices.append(DetectedDevice(
                    vid=vid, pid=pid,
                    vendor_name=entry.vendor if entry else "Mock",
                    product_name=name,
                    usb_path=path, scsi_device=path,
                    protocol="led",
                    device_type=entry.device_type if entry else 1,
                    implementation=entry.implementation if entry else "hid_led",
                    model=spec.get('model', entry.model if entry else 'AX120'),
                    button_image=entry.button_image if entry else "",
                ))

        return devices

    def run_setup(self, auto_yes: bool = False) -> int:
        return 0

    def install_rules(self) -> int:
        return 0

    def check_deps(self) -> list:
        return []

    def get_pkg_manager(self) -> str | None:
        return None

    def distro_name(self) -> str:
        return 'Mock Linux'

    def doctor_config(self) -> DoctorPlatformConfig:
        return DoctorPlatformConfig(
            distro_name='Mock', pkg_manager=None,
            check_libusb=False, extra_binaries=[],
            run_gpu_check=False, run_udev_check=False,
            run_selinux_check=False, run_rapl_check=False,
            run_polkit_check=False, run_winusb_check=False,
            enable_ansi=False,
        )

    def report_config(self) -> ReportPlatformConfig:
        return ReportPlatformConfig(
            distro_name='Mock',
            collect_lsusb=False, collect_udev=False,
            collect_selinux=False, collect_rapl=False,
            collect_device_permissions=False,
        )

    def archive_tool_install_help(self) -> str:
        return '7z not found'

    def ffmpeg_install_help(self) -> str:
        return 'ffmpeg not found'

    def get_memory_info(self) -> list[dict[str, str]]:
        return [{'size': '16 GB', 'type': 'DDR4'}]

    def get_disk_info(self) -> list[dict[str, str]]:
        return [{'name': 'mock0', 'model': 'Mock SSD', 'size': '1 TB', 'type': 'SSD'}]

    def acquire_instance_lock(self) -> object | None:
        return 'mock-lock'

    def raise_existing_instance(self) -> None:
        pass

    def autostart_enable(self) -> None:
        pass

    def autostart_disable(self) -> None:
        pass

    def autostart_enabled(self) -> bool:
        return False

    # ── Power events — captured for tests to fire manually ───────────
    # Trcc.__init__ calls platform.subscribe_power(on_suspend, on_resume).
    # We stash the callbacks so a sleep-cycle smoke test can invoke them
    # without a real OS suspend.  Real Platform subclasses hook into
    # systemd-logind / WM_POWERBROADCAST / NSWorkspace.
    def subscribe_power(self, on_suspend, on_resume) -> None:
        self._on_suspend_cb = on_suspend
        self._on_resume_cb = on_resume

    def fire_suspend(self) -> None:
        """Test helper: simulate the OS firing its suspend event."""
        if (cb := getattr(self, '_on_suspend_cb', None)):
            cb()

    def fire_resume(self) -> None:
        """Test helper: simulate the OS firing its resume event."""
        if (cb := getattr(self, '_on_resume_cb', None)):
            cb()

    # ── Noop protocol registration ───────────────────────────────────

    def _register_noop_protocols(self) -> None:
        from trcc.adapters.device.factory import DeviceProtocolFactory
        factory = DeviceProtocolFactory
        specs = self._specs

        def _make_lcd_protocol(device_info):
            vid = getattr(device_info, 'vid', 0)
            pid = getattr(device_info, 'pid', 0)
            for spec in specs:
                sv = int(spec.get('vid', '0'), 16)
                sp = int(spec.get('pid', '0'), 16)
                if sv == vid and sp == pid:
                    res_str = spec.get('resolution', '320x320')
                    parts = res_str.split('x')
                    w, h = int(parts[0]), int(parts[1])
                    from trcc.core.models import RESOLUTION_TO_PM
                    fbl = RESOLUTION_TO_PM.get((w, h), 100)
                    pm = spec.get('pm', fbl)
                    sub = spec.get('sub', 0)
                    return NoopLCDProtocol((w, h), fbl, pm, sub)
            return NoopLCDProtocol((320, 320), 100, 100, 0)

        def _make_led_protocol(device_info):
            vid = getattr(device_info, 'vid', 0)
            pid = getattr(device_info, 'pid', 0)
            for spec in specs:
                if spec.get('type') != 'led':
                    continue
                sv = int(spec.get('vid', '0'), 16)
                sp = int(spec.get('pid', '0'), 16)
                if sv == vid and sp == pid:
                    pm = spec.get('pm')
                    sub = spec.get('sub', 0)
                    if pm is None:
                        from trcc.core.models import PmRegistry
                        model = spec.get('model', '')
                        pm = next(
                            (p for p, e in PmRegistry if e.model_name == model),
                            3)
                    return NoopLEDProtocol(pm, sub)
            return NoopLEDProtocol(3)

        factory._PROTOCOL_REGISTRY['scsi'] = _make_lcd_protocol
        factory._PROTOCOL_REGISTRY['hid'] = _make_lcd_protocol
        factory._PROTOCOL_REGISTRY['bulk'] = _make_lcd_protocol
        factory._PROTOCOL_REGISTRY['ly'] = _make_lcd_protocol
        factory._PROTOCOL_REGISTRY['led'] = _make_led_protocol


# ═════════════════════════════════════════════════════════════════════════════
# Default device specs
# ═════════════════════════════════════════════════════════════════════════════

DEFAULT_DEVICES = [
    {"type": "lcd", "resolution": "320x320", "name": "Frozen Warframe Pro",
     "vid": "0402", "pid": "3922", "pm": 32, "sub": 1},
    {"type": "lcd", "resolution": "1280x480", "name": "Trofeo Vision",
     "vid": "0418", "pid": "5303", "pm": 6, "sub": 1},
    {"type": "led", "model": "AX120_DIGITAL", "name": "AX120 R3",
     "vid": "0416", "pid": "8001"},
    {"type": "led", "model": "PA120_DIGITAL", "name": "PA120 DIGITAL",
     "vid": "0416", "pid": "8002"},
    {"type": "led", "model": "LF10", "name": "LF10",
     "vid": "0416", "pid": "8003"},
    {"type": "led", "model": "CZ1", "name": "CZ1",
     "vid": "0416", "pid": "8004"},
]
