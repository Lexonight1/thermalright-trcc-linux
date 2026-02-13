"""Diagnostic report for bug reports.

Collects version, OS, devices, udev rules, SELinux, dependencies,
handshake results, and config into a single copyable text block.

Usage:
    from trcc.debug_report import DebugReport

    rpt = DebugReport()
    rpt.collect()
    print(rpt)           # formatted text
    rpt.sections          # list of (title, body) tuples
"""

from __future__ import annotations

import logging
import os
import platform
import subprocess
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

# All known Thermalright VIDs (lowercase hex, no prefix)
_KNOWN_VIDS = ("0416", "0418", "87cd", "87ad", "0402")

_UDEV_PATH = "/etc/udev/rules.d/99-trcc-lcd.rules"

_WIDTH = 60


@dataclass
class _Section:
    title: str
    lines: list[str] = field(default_factory=list)


class DebugReport:
    """Collects and formats system diagnostics for GitHub issues."""

    def __init__(self) -> None:
        self._sections: list[_Section] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def collect(self) -> None:
        """Gather all diagnostic sections."""
        self._version()
        self._lsusb()
        self._udev_rules()
        self._selinux()
        self._dependencies()
        self._devices()
        self._device_permissions()
        self._handshakes()
        self._config()

    @property
    def sections(self) -> list[tuple[str, str]]:
        """Return collected sections as (title, body) pairs."""
        return [(s.title, "\n".join(s.lines)) for s in self._sections]

    def __str__(self) -> str:
        parts: list[str] = []
        for s in self._sections:
            parts.append(f"\n{'─' * _WIDTH}")
            parts.append(s.title)
            parts.append(f"{'─' * _WIDTH}")
            parts.extend(s.lines)
        parts.append(f"\n{'=' * _WIDTH}")
        parts.append("Copy everything above and paste it into your GitHub issue:")
        parts.append("  https://github.com/Lexonight1/thermalright-trcc-linux/issues/new")
        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Section collectors
    # ------------------------------------------------------------------

    def _add(self, title: str) -> _Section:
        sec = _Section(title)
        self._sections.append(sec)
        return sec

    def _version(self) -> None:
        from trcc.__version__ import __version__

        sec = self._add("Version")
        sec.lines.append(f"  trcc-linux:  {__version__}")
        sec.lines.append(f"  Python:      {platform.python_version()}")
        sec.lines.append(f"  OS:          {platform.platform()}")
        sec.lines.append(f"  Kernel:      {platform.release()}")

    def _lsusb(self) -> None:
        sec = self._add("lsusb (filtered)")
        try:
            result = subprocess.run(
                ["lsusb"], capture_output=True, text=True, timeout=5,
            )
            matches = [
                line for line in result.stdout.splitlines()
                if any(vid in line.lower() for vid in _KNOWN_VIDS)
            ]
            if matches:
                for line in matches:
                    sec.lines.append(f"  {line}")
            else:
                sec.lines.append("  (no Thermalright devices found)")
        except Exception as e:
            sec.lines.append(f"  lsusb failed: {e}")

    def _udev_rules(self) -> None:
        sec = self._add(f"udev rules ({_UDEV_PATH})")
        try:
            with open(_UDEV_PATH) as f:
                for line in f:
                    stripped = line.rstrip()
                    if stripped and not stripped.startswith("#"):
                        sec.lines.append(f"  {stripped}")
            if not sec.lines:
                sec.lines.append("  (file exists but no active rules)")
        except FileNotFoundError:
            sec.lines.append("  NOT INSTALLED — run: trcc setup-udev")
        except PermissionError:
            sec.lines.append("  (permission denied reading udev rules)")
        except Exception as e:
            sec.lines.append(f"  Error: {e}")

    def _selinux(self) -> None:
        sec = self._add("SELinux")
        try:
            result = subprocess.run(
                ["getenforce"], capture_output=True, text=True, timeout=5,
            )
            sec.lines.append(f"  {result.stdout.strip()}")
        except FileNotFoundError:
            sec.lines.append("  not installed")
        except Exception as e:
            sec.lines.append(f"  getenforce failed: {e}")

    def _dependencies(self) -> None:
        from trcc.doctor import get_module_version

        sec = self._add("Dependencies")
        for import_name, pkg_name in [
            ("PyQt6", "PyQt6"),
            ("usb.core", "pyusb"),
            ("hid", "hidapi"),
        ]:
            ver = get_module_version(import_name)
            if ver is not None:
                sec.lines.append(f"  {pkg_name}: {ver or '?'}")
            else:
                sec.lines.append(f"  {pkg_name}: not installed")

    def _devices(self) -> None:
        sec = self._add("Detected devices")
        try:
            from trcc.device_detector import detect_devices

            devices = detect_devices()
            if not devices:
                sec.lines.append("  (none)")
                return
            for i, dev in enumerate(devices, 1):
                proto = dev.protocol.upper()
                sec.lines.append(
                    f"  [{i}] {dev.vid:04x}:{dev.pid:04x}  "
                    f"{dev.product_name}  ({proto})  "
                    f"path={dev.scsi_device or dev.usb_path}"
                )
        except Exception as e:
            sec.lines.append(f"  detect failed: {e}")

    def _device_permissions(self) -> None:
        sec = self._add("Device permissions")
        # Check /dev/sg* for SCSI devices
        sg_found = False
        for entry in sorted(os.listdir("/dev")):
            if entry.startswith("sg"):
                path = f"/dev/{entry}"
                try:
                    mode = oct(os.stat(path).st_mode)[-3:]
                    readable = os.access(path, os.R_OK | os.W_OK)
                    status = "OK" if readable else "NO ACCESS"
                    sec.lines.append(f"  {path}: mode={mode}  {status}")
                    sg_found = True
                except Exception:
                    pass
        if not sg_found:
            sec.lines.append("  (no /dev/sg* devices)")

    def _handshakes(self) -> None:
        sec = self._add("Handshakes")
        try:
            from trcc.device_detector import detect_devices

            devices = detect_devices()
            hid_devs = [d for d in devices if d.protocol == "hid"]
            bulk_devs = [d for d in devices if d.protocol == "bulk"]

            if not hid_devs and not bulk_devs:
                sec.lines.append("  (no HID/Bulk devices to handshake)")
                return

            for dev in hid_devs:
                is_led = dev.implementation == "hid_led"
                kind = "LED" if is_led else f"HID-LCD (Type {dev.device_type})"
                sec.lines.append(f"\n  {dev.vid:04x}:{dev.pid:04x} — {kind}")
                try:
                    if is_led:
                        self._handshake_led(dev, sec)
                    else:
                        self._handshake_hid_lcd(dev, sec)
                except Exception as e:
                    sec.lines.append(f"    FAILED: {e}")

            for dev in bulk_devs:
                sec.lines.append(f"\n  {dev.vid:04x}:{dev.pid:04x} — Bulk")
                try:
                    self._handshake_bulk(dev, sec)
                except Exception as e:
                    sec.lines.append(f"    FAILED: {e}")

        except Exception as e:
            sec.lines.append(f"  Error: {e}")

    def _config(self) -> None:
        sec = self._add("Config")
        try:
            from trcc.conf import CONFIG_PATH, load_config

            config = load_config()
            if not config:
                sec.lines.append(f"  {CONFIG_PATH}: (empty or missing)")
                return
            sec.lines.append(f"  path: {CONFIG_PATH}")
            # Show non-sensitive keys
            for key in ("resolution", "temp_unit", "format_prefs"):
                if key in config:
                    sec.lines.append(f"  {key}: {config[key]}")
            # Device count
            devices = config.get("devices", {})
            if devices:
                sec.lines.append(f"  devices: {len(devices)} configured")
        except Exception as e:
            sec.lines.append(f"  Error: {e}")

    # ------------------------------------------------------------------
    # Handshake helpers
    # ------------------------------------------------------------------

    def _handshake_hid_lcd(self, dev, sec: _Section) -> None:
        from trcc.device_factory import HidProtocol
        from trcc.device_hid import HidHandshakeInfo, fbl_to_resolution, pm_to_fbl

        protocol = HidProtocol(vid=dev.vid, pid=dev.pid, device_type=dev.device_type)
        try:
            info = protocol.handshake()
            if info is None:
                error = protocol.last_error
                sec.lines.append(f"    Result: None ({error or 'no response'})")
                return

            assert isinstance(info, HidHandshakeInfo)
            pm = info.mode_byte_1
            sub = info.mode_byte_2
            fbl = info.fbl if info.fbl is not None else pm_to_fbl(pm, sub)
            resolution = info.resolution or fbl_to_resolution(fbl, pm)
            sec.lines.append(f"    PM={pm} (0x{pm:02x}), SUB={sub} (0x{sub:02x}), "
                             f"FBL={fbl}, resolution={resolution[0]}x{resolution[1]}")
            if info.serial:
                sec.lines.append(f"    serial={info.serial}")
            if info.raw_response:
                sec.lines.append(f"    raw[0:16]={info.raw_response[:16].hex()}")
        finally:
            protocol.close()

    def _handshake_led(self, dev, sec: _Section) -> None:
        from trcc.device_factory import LedProtocol
        from trcc.device_led import PM_TO_STYLE, LedHandshakeInfo

        protocol = LedProtocol(vid=dev.vid, pid=dev.pid)
        try:
            info = protocol.handshake()
            if info is None:
                error = protocol.last_error
                sec.lines.append(f"    Result: None ({error or 'no response'})")
                return

            assert isinstance(info, LedHandshakeInfo)
            known = "KNOWN" if info.pm in PM_TO_STYLE else "UNKNOWN"
            style_info = ""
            if info.style:
                style_info = (f", LEDs={info.style.led_count}, "
                              f"segments={info.style.segment_count}")
            sec.lines.append(
                f"    PM={info.pm} (0x{info.pm:02x}), SUB={info.sub_type}, "
                f"model={info.model_name}, {known}{style_info}"
            )
            if info.raw_response:
                sec.lines.append(f"    raw[0:16]={info.raw_response[:16].hex()}")
        finally:
            protocol.close()

    def _handshake_bulk(self, dev, sec: _Section) -> None:
        from trcc.device_factory import BulkProtocol

        protocol = BulkProtocol(vid=dev.vid, pid=dev.pid)
        try:
            result = protocol.handshake()
            if result is None:
                error = protocol.last_error
                sec.lines.append(f"    Result: None ({error or 'no response'})")
                return
            sec.lines.append(
                f"    PM={result.model_id}, resolution={result.resolution}, "
                f"serial={result.serial}"
            )
            if result.raw_response:
                sec.lines.append(f"    raw[0:16]={result.raw_response[:16].hex()}")
        finally:
            protocol.close()
