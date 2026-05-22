"""Extended tests for DebugReport — covers paths not in test_debug_report.py.

Covers:
- _lsusb: all VID matches, VID case-insensitivity, subprocess error variants
- _udev_rules: file exists but empty active rules, generic exception
- _selinux: generic exception branch, timeout
- _rapl_permissions: powercap exists, RAPL domains, readable/unreadable
- _dependencies: version present, version missing/empty
- _devices: detect raises, SCSI device path
- _device_permissions: stat failure, read-only (no W_OK), HID device entries
- _handshakes: routing to each protocol handler, exception in handler
- _handshake_scsi: success with raw bytes, result=None, FBL unknown/known
- _handshake_hid_lcd: success, EACCES, EBUSY with/without cache, other error
- _handshake_led: success with/without style, EACCES, EBUSY, other error
- _handshake_bulk: success with raw, EACCES, EBUSY, other error
- _handshake_ly: success with raw, EACCES, EBUSY, other error
- _ebusy_fallback: cached data with raw, cached data without raw, no cache
- _process_usage: trcc procs found, none found, subprocess error
- _config: key subset shown, exception path
- __str__: section separator lines, header format
- sections property: returns (title, body) pairs correctly
"""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, mock_open, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src'))

from trcc.legacy.adapters.infra.diagnostics import _KNOWN_VIDS, DebugReport

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_usb_error(errno_val: int) -> Exception:
    """Build a USBError-like exception that carries an errno attribute."""
    e = Exception("USB error")
    e.errno = errno_val  # type: ignore[attr-defined]
    return e


def _section(rpt: DebugReport, idx: int = 0) -> tuple[str, str]:
    return rpt.sections[idx]


# ---------------------------------------------------------------------------
# lsusb — additional cases
# ---------------------------------------------------------------------------

class TestLsusbExtra:
    """Additional _lsusb coverage."""

    @patch("trcc.legacy.adapters.infra.diagnostics.subprocess.run")
    def test_all_known_vids_matched(self, mock_run):
        """Each VID in _KNOWN_VIDS is recognised."""
        lines = "\n".join(
            f"Bus 001 Device 00{i}: ID {vid}:0001 Thermalright Device"
            for i, vid in enumerate(_KNOWN_VIDS)
        )
        mock_run.return_value = MagicMock(stdout=lines + "\n")
        rpt = DebugReport()
        rpt._lsusb()
        _, body = _section(rpt)
        for vid in _KNOWN_VIDS:
            assert vid in body

    @patch("trcc.legacy.adapters.infra.diagnostics.subprocess.run")
    def test_vid_case_insensitive_match(self, mock_run):
        """Uppercase VID in lsusb output still matched."""
        mock_run.return_value = MagicMock(
            stdout="Bus 001 Device 003: ID 0416:8001 Winbond\n"
        )
        rpt = DebugReport()
        rpt._lsusb()
        _, body = _section(rpt)
        assert "0416:8001" in body

    @patch("trcc.legacy.adapters.infra.diagnostics.subprocess.run")
    def test_subprocess_timeout(self, mock_run):
        """TimeoutExpired is caught and reported."""
        import subprocess
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="lsusb", timeout=5)
        rpt = DebugReport()
        rpt._lsusb()
        _, body = _section(rpt)
        assert "failed" in body

    @patch("trcc.legacy.adapters.infra.diagnostics.subprocess.run")
    def test_oserror_reported(self, mock_run):
        """OSError propagates to failure message."""
        mock_run.side_effect = OSError("permission denied")
        rpt = DebugReport()
        rpt._lsusb()
        _, body = _section(rpt)
        assert "failed" in body


# ---------------------------------------------------------------------------
# udev rules — additional cases
# ---------------------------------------------------------------------------

class TestUdevRulesExtra:
    """Additional _udev_rules coverage."""

    @patch("builtins.open", mock_open(read_data="# comment only\n\n"))
    def test_file_exists_only_comments(self):
        """File with only comments → 'file exists but no active rules'."""
        rpt = DebugReport()
        rpt._udev_rules()
        _, body = _section(rpt)
        assert "no active rules" in body

    @patch("builtins.open", side_effect=OSError("disk error"))
    def test_generic_oserror(self, _):
        """Generic OSError shows Error: message."""
        rpt = DebugReport()
        rpt._udev_rules()
        _, body = _section(rpt)
        assert "Error" in body

    @patch("builtins.open", mock_open(read_data=(
        'SUBSYSTEM=="usb", ATTR{idVendor}=="0416", MODE="0666"\n'
        'SUBSYSTEM=="usb", ATTR{idVendor}=="87cd", MODE="0666"\n'
    )))
    def test_multiple_rules_all_shown(self):
        """Multiple non-comment lines are all included in output."""
        rpt = DebugReport()
        rpt._udev_rules()
        _, body = _section(rpt)
        assert "0416" in body
        assert "87cd" in body


# ---------------------------------------------------------------------------
# SELinux — additional branches
# ---------------------------------------------------------------------------

class TestSelinuxExtra:
    """_selinux() section coverage — delegates to check_selinux()."""

    @patch("trcc.legacy.adapters.infra.diagnostics.check_selinux")
    def test_permissive_mode(self, mock_check):
        from trcc.legacy.adapters.infra.diagnostics import SelinuxResult
        mock_check.return_value = SelinuxResult(ok=True, message='SELinux permissive (no policy needed)')
        rpt = DebugReport()
        rpt._selinux()
        _, body = _section(rpt)
        assert "permissive" in body

    @patch("trcc.legacy.adapters.infra.diagnostics.check_selinux")
    def test_disabled_mode(self, mock_check):
        from trcc.legacy.adapters.infra.diagnostics import SelinuxResult
        mock_check.return_value = SelinuxResult(ok=True, message='SELinux disabled (no policy needed)')
        rpt = DebugReport()
        rpt._selinux()
        _, body = _section(rpt)
        assert "disabled" in body

    @patch("trcc.legacy.adapters.infra.diagnostics.check_selinux")
    def test_status_unknown(self, mock_check):
        from trcc.legacy.adapters.infra.diagnostics import SelinuxResult
        mock_check.return_value = SelinuxResult(ok=True, message='SELinux status unknown')
        rpt = DebugReport()
        rpt._selinux()
        _, body = _section(rpt)
        assert "unknown" in body

    @patch("trcc.legacy.adapters.infra.diagnostics.check_selinux")
    def test_enforcing_policy_loaded(self, mock_check):
        """Enforcing with trcc_usb loaded — no setup hint shown."""
        from trcc.legacy.adapters.infra.diagnostics import SelinuxResult
        mock_check.return_value = SelinuxResult(
            ok=True, message='SELinux enforcing — trcc_usb module loaded',
            enforcing=True, module_loaded=True,
        )
        rpt = DebugReport()
        rpt._selinux()
        _, body = _section(rpt)
        assert "trcc_usb module loaded" in body
        assert "setup-selinux" not in body

    @patch("trcc.legacy.adapters.infra.diagnostics.check_selinux")
    def test_enforcing_policy_missing(self, mock_check):
        """Enforcing without USB policy — setup hint must appear."""
        from trcc.legacy.adapters.infra.diagnostics import SelinuxResult
        mock_check.return_value = SelinuxResult(
            ok=False, message='SELinux enforcing — USB policy not installed',
            enforcing=True, module_loaded=False,
        )
        rpt = DebugReport()
        rpt._selinux()
        _, body = _section(rpt)
        assert "USB policy not installed" in body
        assert "setup-selinux" in body


# ---------------------------------------------------------------------------
# RAPL permissions
# ---------------------------------------------------------------------------

class TestRaplPermissionsExtra:
    """_rapl_permissions section coverage."""

    @patch("trcc.legacy.adapters.infra.diagnostics.Path")
    def test_no_powercap(self, MockPath):
        """No powercap subsystem → 'not available' message."""
        mock_base = MagicMock()
        mock_base.exists.return_value = False
        MockPath.return_value = mock_base
        rpt = DebugReport()
        rpt._rapl_permissions()
        _, body = _section(rpt)
        assert "not available" in body

    @patch("os.access", return_value=True)
    @patch("trcc.legacy.adapters.infra.diagnostics.Path")
    def test_rapl_readable(self, MockPath, mock_access):
        """Readable RAPL domain shows 'readable' status."""
        mock_file = MagicMock()
        mock_file.parent.name = "intel-rapl:0"
        mock_stat = MagicMock()
        mock_stat.st_mode = 0o100444
        mock_file.stat.return_value = mock_stat
        mock_base = MagicMock()
        mock_base.exists.return_value = True
        mock_base.glob.return_value = [mock_file]
        MockPath.return_value = mock_base
        rpt = DebugReport()
        rpt._rapl_permissions()
        _, body = _section(rpt)
        assert "readable" in body
        assert "intel-rapl:0" in body

    @patch("os.access", return_value=False)
    @patch("trcc.legacy.adapters.infra.diagnostics.Path")
    def test_rapl_no_access(self, MockPath, mock_access):
        """Unreadable RAPL domain shows 'NO ACCESS'."""
        mock_file = MagicMock()
        mock_file.parent.name = "intel-rapl:0"
        mock_stat = MagicMock()
        mock_stat.st_mode = 0o100400
        mock_file.stat.return_value = mock_stat
        mock_base = MagicMock()
        mock_base.exists.return_value = True
        mock_base.glob.return_value = [mock_file]
        MockPath.return_value = mock_base
        rpt = DebugReport()
        rpt._rapl_permissions()
        _, body = _section(rpt)
        assert "NO ACCESS" in body

    @patch("trcc.legacy.adapters.infra.diagnostics.Path")
    def test_no_rapl_domains(self, MockPath):
        """Powercap exists but no intel-rapl domains."""
        mock_base = MagicMock()
        mock_base.exists.return_value = True
        mock_base.glob.return_value = []
        MockPath.return_value = mock_base
        rpt = DebugReport()
        rpt._rapl_permissions()
        _, body = _section(rpt)
        assert "not available" in body


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------

class TestDependenciesExtra:
    """_dependencies section — version scenarios."""

    def test_version_shown_when_present(self):
        rpt = DebugReport()
        rpt._dependencies()
        _, body = _section(rpt)
        # At least pyusb must be installed in the test env
        assert "pyusb:" in body

    @patch("trcc.legacy.adapters.infra.diagnostics.get_module_version", return_value=None)
    def test_not_installed_shown(self, _):
        rpt = DebugReport()
        rpt._dependencies()
        _, body = _section(rpt)
        assert "not installed" in body

    @patch("trcc.legacy.adapters.infra.diagnostics.get_module_version", return_value="")
    def test_empty_version_shown_as_question_mark(self, _):
        """Empty version string shows '?' rather than blank."""
        rpt = DebugReport()
        rpt._dependencies()
        _, body = _section(rpt)
        # The body should contain at least one '?' for the empty-version case
        assert "?" in body


# ---------------------------------------------------------------------------
# Devices — additional cases
# ---------------------------------------------------------------------------

class TestDevicesExtra:
    """_devices extra coverage."""

    def test_detect_raises(self):
        """Exception from detect() is caught gracefully."""
        rpt = DebugReport(detect_fn=lambda: (_ for _ in ()).throw(RuntimeError("usb error")))
        rpt._devices()
        _, body = _section(rpt)
        assert "detect failed" in body

    def test_scsi_device_shows_scsi_path(self):
        """SCSI device uses scsi_device path in output."""
        dev = MagicMock()
        dev.vid = 0x0402
        dev.pid = 0x3922
        dev.product_name = "Frozen Warframe"
        dev.protocol = "scsi"
        dev.scsi_device = "/dev/sg0"
        dev.usb_path = None
        rpt = DebugReport(detect_fn=lambda: [dev])
        rpt._devices()
        _, body = _section(rpt)
        assert "/dev/sg0" in body
        assert "SCSI" in body

    def test_hid_device_shows_usb_path(self):
        """HID device uses usb_path when scsi_device is None."""
        dev = MagicMock()
        dev.vid = 0x0416
        dev.pid = 0x5302
        dev.product_name = "AIO LCD"
        dev.protocol = "hid"
        dev.scsi_device = None
        dev.usb_path = "3-1.2"
        rpt = DebugReport(detect_fn=lambda: [dev])
        rpt._devices()
        _, body = _section(rpt)
        assert "3-1.2" in body


# ---------------------------------------------------------------------------
# Device permissions — additional cases
# ---------------------------------------------------------------------------

class TestDevicePermissionsExtra:
    """_device_permissions extra coverage."""

    @patch("os.listdir", return_value=["sg0"])
    @patch("os.stat", side_effect=PermissionError)
    def test_stat_failure_reported(self, mock_stat, _):
        """Stat failure on a sg device is reported, not silently skipped.

        The device exists (it appeared in /dev) but cannot be stat'd — that
        is a real diagnostic signal. Production reports `stat failed (...)`
        and treats the device as "found" so the (none found) line is NOT
        emitted.
        """
        rpt = DebugReport()
        rpt._device_permissions()
        _, body = _section(rpt)
        assert "/dev/sg0: stat failed" in body
        assert "/dev/sg*: (none found)" not in body

    @patch("os.listdir", return_value=["sg0", "sg1"])
    @patch("os.stat")
    @patch("os.access", return_value=False)
    def test_no_write_access(self, mock_access, mock_stat, _):
        """Device with no write access shows NO ACCESS."""
        mock_stat.return_value = MagicMock()
        mock_stat.return_value.st_mode = 0o100640
        rpt = DebugReport()
        rpt._device_permissions()
        _, body = _section(rpt)
        assert "NO ACCESS" in body

    @patch("os.listdir", return_value=["sda", "sdb", "tty0"])
    def test_non_sg_entries_ignored(self, _):
        """Non-sg entries do not appear."""
        rpt = DebugReport()
        rpt._device_permissions()
        _, body = _section(rpt)
        assert "/dev/sg*: (none found)" in body
        assert "sda" not in body


# ---------------------------------------------------------------------------
# Handshakes routing
# ---------------------------------------------------------------------------

class TestHandshakesRouting:
    """_handshakes routing to sub-handlers."""

    def test_no_devices_cached(self):
        rpt = DebugReport()
        # _detected_devices is empty by default
        rpt._handshakes()
        _, body = _section(rpt)
        assert "no devices to handshake" in body

    def test_scsi_device_routes_to_scsi_handler(self):
        dev = MagicMock()
        dev.protocol = "scsi"
        dev.vid = 0x0402
        dev.pid = 0x3922
        dev.scsi_device = "/dev/sg0"

        rpt = DebugReport()
        rpt._detected_devices = [dev]

        with patch.object(rpt, "_handshake_scsi") as mock_scsi:
            rpt._handshakes()
            mock_scsi.assert_called_once()

    def test_hid_lcd_device_routes_to_hid_handler(self):
        dev = MagicMock()
        dev.protocol = "hid"
        dev.implementation = "hid_lcd"
        dev.vid = 0x0416
        dev.pid = 0x5302
        dev.device_type = 2

        rpt = DebugReport()
        rpt._detected_devices = [dev]

        with patch.object(rpt, "_handshake_hid_lcd") as mock_hid:
            rpt._handshakes()
            mock_hid.assert_called_once()

    def test_led_device_routes_to_led_handler(self):
        dev = MagicMock()
        dev.protocol = "led"
        dev.implementation = "hid_led"
        dev.vid = 0x0416
        dev.pid = 0x8001
        dev.device_type = 1

        rpt = DebugReport()
        rpt._detected_devices = [dev]

        with patch.object(rpt, "_handshake_led") as mock_led:
            rpt._handshakes()
            mock_led.assert_called_once()

    def test_bulk_device_routes_to_bulk_handler(self):
        dev = MagicMock()
        dev.protocol = "bulk"
        dev.vid = 0x87AD
        dev.pid = 0x70DB

        rpt = DebugReport()
        rpt._detected_devices = [dev]

        with patch.object(rpt, "_handshake_bulk") as mock_bulk:
            rpt._handshakes()
            mock_bulk.assert_called_once()

    def test_ly_device_routes_to_ly_handler(self):
        dev = MagicMock()
        dev.protocol = "ly"
        dev.vid = 0x0416
        dev.pid = 0x5408

        rpt = DebugReport()
        rpt._detected_devices = [dev]

        with patch.object(rpt, "_handshake_ly") as mock_ly:
            rpt._handshakes()
            mock_ly.assert_called_once()

    @pytest.mark.parametrize("vid,pid,entry", [
        (vid, pid, entry)
        for (vid, pid), entry in __import__(
            'trcc.legacy.core.models', fromlist=['LED_DEVICES']
        ).LED_DEVICES.items()
    ])
    def test_led_registry_devices_route_to_led_handler(self, vid, pid, entry):
        """Every device in LED_DEVICES must route to _handshake_led (regression: #90)."""
        dev = MagicMock()
        dev.protocol = entry.protocol
        dev.implementation = entry.implementation
        dev.vid = vid
        dev.pid = pid
        dev.device_type = entry.device_type

        rpt = DebugReport()
        rpt._detected_devices = [dev]

        with patch.object(rpt, "_handshake_led") as mock_led:
            rpt._handshakes()
        mock_led.assert_called_once()

    def test_handler_exception_caught_per_device(self):
        """Exception in a handler appends FAILED line, does not abort others."""
        dev_scsi = MagicMock()
        dev_scsi.protocol = "scsi"
        dev_scsi.vid = 0x0402
        dev_scsi.pid = 0x3922

        dev_bulk = MagicMock()
        dev_bulk.protocol = "bulk"
        dev_bulk.vid = 0x87AD
        dev_bulk.pid = 0x70DB

        rpt = DebugReport()
        rpt._detected_devices = [dev_scsi, dev_bulk]

        with patch.object(rpt, "_handshake_scsi", side_effect=RuntimeError("boom")):
            with patch.object(rpt, "_handshake_bulk") as mock_bulk:
                rpt._handshakes()
                mock_bulk.assert_called_once()

        _, body = _section(rpt)
        assert "FAILED" in body


# ---------------------------------------------------------------------------
# _handshake_scsi
# ---------------------------------------------------------------------------

class TestHandshakeScsi:
    """_handshake_scsi result paths."""

    def _make_dev(self) -> MagicMock:
        dev = MagicMock()
        dev.scsi_device = "/dev/sg0"
        return dev

    @patch("trcc.legacy.adapters.device.factory.DeviceProtocolFactory.create_protocol")
    def test_success_known_fbl(self, mock_create):
        result = MagicMock()
        result.model_id = 50  # FBL 50 → known
        result.resolution = (320, 240)
        result.raw_response = bytes(range(64))
        proto = MagicMock()
        proto.handshake.return_value = result
        mock_create.return_value = proto

        sec_obj = MagicMock()
        sec_obj.lines = []
        rpt = DebugReport()
        rpt._handshake_scsi(self._make_dev(), sec_obj)

        text = " ".join(sec_obj.lines)
        assert "FBL=50" in text
        assert "KNOWN" in text
        assert "320" in text
        proto.close.assert_called_once()

    @patch("trcc.legacy.adapters.device.factory.DeviceProtocolFactory.create_protocol")
    def test_success_unknown_fbl(self, mock_create):
        result = MagicMock()
        result.model_id = 999  # not in table
        result.resolution = (0, 0)
        result.raw_response = b""
        proto = MagicMock()
        proto.handshake.return_value = result
        mock_create.return_value = proto

        sec_obj = MagicMock()
        sec_obj.lines = []
        rpt = DebugReport()
        rpt._handshake_scsi(self._make_dev(), sec_obj)

        text = " ".join(sec_obj.lines)
        assert "UNKNOWN" in text
        assert "FBL=999" in text

    @patch("trcc.legacy.adapters.device.factory.DeviceProtocolFactory.create_protocol")
    def test_handshake_returns_none(self, mock_create):
        proto = MagicMock()
        proto.handshake.return_value = None
        mock_create.return_value = proto

        sec_obj = MagicMock()
        sec_obj.lines = []
        rpt = DebugReport()
        rpt._handshake_scsi(self._make_dev(), sec_obj)

        text = " ".join(sec_obj.lines)
        assert "poll failed" in text
        proto.close.assert_called_once()

    @patch("trcc.legacy.adapters.device.factory.DeviceProtocolFactory.create_protocol")
    def test_close_called_on_exception(self, mock_create):
        proto = MagicMock()
        proto.handshake.side_effect = RuntimeError("device gone")
        mock_create.return_value = proto

        sec_obj = MagicMock()
        sec_obj.lines = []
        rpt = DebugReport()
        with pytest.raises(RuntimeError):
            rpt._handshake_scsi(self._make_dev(), sec_obj)

        proto.close.assert_called_once()


# ---------------------------------------------------------------------------
# _handshake_hid_lcd
# ---------------------------------------------------------------------------

class TestHandshakeHidLcd:
    """_handshake_hid_lcd result paths."""

    def _make_dev(self) -> MagicMock:
        dev = MagicMock()
        dev.vid = 0x0416
        dev.pid = 0x5302
        dev.device_type = 2
        return dev

    def _make_hid_info(self, fbl=50, pm=128, sub=0, serial="SN123",
                       resolution=(320, 240), raw=bytes(64)):
        from trcc.legacy.adapters.device.hid import HidHandshakeInfo
        info = MagicMock(spec=HidHandshakeInfo)
        info.mode_byte_1 = pm
        info.mode_byte_2 = sub
        info.fbl = fbl
        info.resolution = resolution
        info.serial = serial
        info.raw_response = raw
        return info

    @patch("trcc.legacy.adapters.device.hid_protocol.HidProtocol")
    def test_success_shows_pm_fbl_resolution(self, MockHid):
        proto = MagicMock()
        proto.handshake.return_value = self._make_hid_info()
        MockHid.return_value = proto

        sec_obj = MagicMock()
        sec_obj.lines = []
        rpt = DebugReport()
        rpt._handshake_hid_lcd(self._make_dev(), sec_obj)

        text = " ".join(sec_obj.lines)
        assert "PM=" in text
        assert "FBL=50" in text
        assert "320x240" in text
        assert "SN123" in text
        proto.close.assert_called_once()

    @patch("trcc.legacy.adapters.device.hid_protocol.HidProtocol")
    def test_success_without_serial(self, MockHid):
        proto = MagicMock()
        info = self._make_hid_info(serial=None)
        proto.handshake.return_value = info
        MockHid.return_value = proto

        sec_obj = MagicMock()
        sec_obj.lines = []
        rpt = DebugReport()
        rpt._handshake_hid_lcd(self._make_dev(), sec_obj)

        text = " ".join(sec_obj.lines)
        # No crash and PM line present
        assert "PM=" in text

    @patch("trcc.legacy.adapters.device.hid_protocol.HidProtocol")
    def test_eacces_shows_permission_denied(self, MockHid):
        proto = MagicMock()
        proto.handshake.return_value = None
        proto.last_error = _make_usb_error(13)
        MockHid.return_value = proto

        sec_obj = MagicMock()
        sec_obj.lines = []
        rpt = DebugReport()

        # _has_usb_errno is imported locally inside the method; patch at factory
        with patch("trcc.legacy.adapters.device.factory._has_usb_errno",
                   side_effect=lambda e, n: n == 13):
            rpt._handshake_hid_lcd(self._make_dev(), sec_obj)

        text = " ".join(sec_obj.lines)
        assert "Permission denied" in text or "setup-udev" in text

    @patch("trcc.legacy.adapters.device.hid_protocol.HidProtocol")
    def test_ebusy_calls_ebusy_fallback(self, MockHid):
        proto = MagicMock()
        proto.handshake.return_value = None
        proto.last_error = _make_usb_error(16)
        MockHid.return_value = proto

        sec_obj = MagicMock()
        sec_obj.lines = []
        rpt = DebugReport()

        with patch("trcc.legacy.adapters.device.factory._has_usb_errno",
                   side_effect=lambda e, n: n == 16):
            with patch.object(rpt, "_ebusy_fallback") as mock_fallback:
                rpt._handshake_hid_lcd(self._make_dev(), sec_obj)
                mock_fallback.assert_called_once_with(sec_obj)

    @patch("trcc.legacy.adapters.device.hid_protocol.HidProtocol")
    def test_other_error_shows_result_none(self, MockHid):
        proto = MagicMock()
        proto.handshake.return_value = None
        proto.last_error = None
        MockHid.return_value = proto

        sec_obj = MagicMock()
        sec_obj.lines = []
        rpt = DebugReport()

        with patch("trcc.legacy.adapters.device.factory._has_usb_errno",
                   return_value=False):
            rpt._handshake_hid_lcd(self._make_dev(), sec_obj)

        text = " ".join(sec_obj.lines)
        assert "Result: None" in text

    @patch("trcc.legacy.adapters.device.hid_protocol.HidProtocol")
    def test_close_always_called(self, MockHid):
        proto = MagicMock()
        proto.handshake.side_effect = RuntimeError("broken")
        MockHid.return_value = proto

        sec_obj = MagicMock()
        sec_obj.lines = []
        rpt = DebugReport()
        with pytest.raises(RuntimeError):
            rpt._handshake_hid_lcd(self._make_dev(), sec_obj)

        proto.close.assert_called_once()


# ---------------------------------------------------------------------------
# _handshake_led
# ---------------------------------------------------------------------------

class TestHandshakeLed:
    """_handshake_led result paths."""

    def _make_dev(self) -> MagicMock:
        dev = MagicMock()
        dev.vid = 0x0416
        dev.pid = 0x8001
        return dev

    def _make_led_info(self, pm=1, sub=0, model_name="PA120",
                       known=True, raw=bytes(64)):
        from trcc.legacy.adapters.device.led import LedHandshakeInfo
        info = MagicMock(spec=LedHandshakeInfo)
        info.pm = pm
        info.sub_type = sub
        info.model_name = model_name
        info.style = MagicMock(led_count=84, segment_count=4) if known else None
        info.raw_response = raw
        return info

    @patch("trcc.legacy.adapters.device.led_protocol.LedProtocol")
    def test_success_known_pm_with_style(self, MockLed):
        proto = MagicMock()
        proto.handshake.return_value = self._make_led_info()
        MockLed.return_value = proto

        sec_obj = MagicMock()
        sec_obj.lines = []
        rpt = DebugReport()

        with patch("trcc.legacy.adapters.device.led.PmRegistry") as MockReg:
            MockReg.PM_TO_STYLE = {1: "something"}
            rpt._handshake_led(self._make_dev(), sec_obj)

        text = " ".join(sec_obj.lines)
        assert "PM=1" in text
        assert "PA120" in text
        proto.close.assert_called_once()

    @patch("trcc.legacy.adapters.device.led_protocol.LedProtocol")
    def test_success_no_style_info(self, MockLed):
        proto = MagicMock()
        info = self._make_led_info(known=False)
        proto.handshake.return_value = info
        MockLed.return_value = proto

        sec_obj = MagicMock()
        sec_obj.lines = []
        rpt = DebugReport()

        with patch("trcc.legacy.adapters.device.led.PmRegistry") as MockReg:
            MockReg.PM_TO_STYLE = {}
            rpt._handshake_led(self._make_dev(), sec_obj)

        text = " ".join(sec_obj.lines)
        assert "PM=1" in text
        assert "UNKNOWN" in text

    @patch("trcc.legacy.adapters.device.led_protocol.LedProtocol")
    def test_ebusy_calls_fallback(self, MockLed):
        proto = MagicMock()
        proto.handshake.return_value = None
        proto.last_error = _make_usb_error(16)
        MockLed.return_value = proto

        sec_obj = MagicMock()
        sec_obj.lines = []
        rpt = DebugReport()

        with patch("trcc.legacy.adapters.device.factory._has_usb_errno",
                   side_effect=lambda e, n: n == 16):
            with patch.object(rpt, "_ebusy_fallback") as mock_fallback:
                rpt._handshake_led(self._make_dev(), sec_obj)
                mock_fallback.assert_called_once_with(sec_obj)

    @patch("trcc.legacy.adapters.device.led_protocol.LedProtocol")
    def test_eacces_shows_permission_denied(self, MockLed):
        proto = MagicMock()
        proto.handshake.return_value = None
        proto.last_error = _make_usb_error(13)
        MockLed.return_value = proto

        sec_obj = MagicMock()
        sec_obj.lines = []
        rpt = DebugReport()

        with patch("trcc.legacy.adapters.device.factory._has_usb_errno",
                   side_effect=lambda e, n: n == 13):
            rpt._handshake_led(self._make_dev(), sec_obj)

        text = " ".join(sec_obj.lines)
        assert "Permission denied" in text or "setup-udev" in text

    @patch("trcc.legacy.adapters.device.led_protocol.LedProtocol")
    def test_close_always_called(self, MockLed):
        proto = MagicMock()
        proto.handshake.side_effect = RuntimeError("broken")
        MockLed.return_value = proto

        sec_obj = MagicMock()
        sec_obj.lines = []
        rpt = DebugReport()
        with pytest.raises(RuntimeError):
            rpt._handshake_led(self._make_dev(), sec_obj)
        proto.close.assert_called_once()


# ---------------------------------------------------------------------------
# _handshake_bulk
# ---------------------------------------------------------------------------

class TestHandshakeBulk:
    """_handshake_bulk result paths."""

    def _make_dev(self) -> MagicMock:
        dev = MagicMock()
        dev.vid = 0x87AD
        dev.pid = 0x70DB
        return dev

    def _make_result(self, model_id=32, resolution=(480, 480),
                     serial="", raw=bytes(64), pm_byte=7, sub_byte=1):
        r = MagicMock()
        r.model_id = model_id
        r.resolution = resolution
        r.serial = serial
        r.pm_byte = pm_byte
        r.sub_byte = sub_byte
        r.raw_response = raw
        return r

    @patch("trcc.legacy.adapters.device.bulk_protocol.BulkProtocol")
    def test_success_shows_pm_resolution(self, MockBulk):
        proto = MagicMock()
        proto.handshake.return_value = self._make_result()
        MockBulk.return_value = proto

        sec_obj = MagicMock()
        sec_obj.lines = []
        rpt = DebugReport()
        rpt._handshake_bulk(self._make_dev(), sec_obj)

        text = " ".join(sec_obj.lines)
        assert "PM=7" in text
        assert "SUB=1" in text
        assert "FBL=32" in text
        assert "(480, 480)" in text
        proto.close.assert_called_once()

    @patch("trcc.legacy.adapters.device.bulk_protocol.BulkProtocol")
    def test_none_result_eacces(self, MockBulk):
        proto = MagicMock()
        proto.handshake.return_value = None
        proto.last_error = _make_usb_error(13)
        MockBulk.return_value = proto

        sec_obj = MagicMock()
        sec_obj.lines = []
        rpt = DebugReport()
        with patch("trcc.legacy.adapters.device.factory._has_usb_errno",
                   side_effect=lambda e, n: n == 13):
            rpt._handshake_bulk(self._make_dev(), sec_obj)

        text = " ".join(sec_obj.lines)
        assert "Permission denied" in text or "setup-udev" in text

    @patch("trcc.legacy.adapters.device.bulk_protocol.BulkProtocol")
    def test_none_result_ebusy(self, MockBulk):
        proto = MagicMock()
        proto.handshake.return_value = None
        proto.last_error = _make_usb_error(16)
        MockBulk.return_value = proto

        sec_obj = MagicMock()
        sec_obj.lines = []
        rpt = DebugReport()
        with patch("trcc.legacy.adapters.device.factory._has_usb_errno",
                   side_effect=lambda e, n: n == 16):
            with patch.object(rpt, "_ebusy_fallback") as mock_fallback:
                rpt._handshake_bulk(self._make_dev(), sec_obj)
                mock_fallback.assert_called_once_with(sec_obj)

    @patch("trcc.legacy.adapters.device.bulk_protocol.BulkProtocol")
    def test_none_result_other_error(self, MockBulk):
        proto = MagicMock()
        proto.handshake.return_value = None
        proto.last_error = Exception("unknown")
        MockBulk.return_value = proto

        sec_obj = MagicMock()
        sec_obj.lines = []
        rpt = DebugReport()
        with patch("trcc.legacy.adapters.device.factory._has_usb_errno",
                   return_value=False):
            rpt._handshake_bulk(self._make_dev(), sec_obj)

        text = " ".join(sec_obj.lines)
        assert "Result: None" in text

    @patch("trcc.legacy.adapters.device.bulk_protocol.BulkProtocol")
    def test_close_always_called(self, MockBulk):
        proto = MagicMock()
        proto.handshake.side_effect = RuntimeError("broken")
        MockBulk.return_value = proto

        sec_obj = MagicMock()
        sec_obj.lines = []
        rpt = DebugReport()
        with pytest.raises(RuntimeError):
            rpt._handshake_bulk(self._make_dev(), sec_obj)
        proto.close.assert_called_once()


# ---------------------------------------------------------------------------
# _handshake_ly
# ---------------------------------------------------------------------------

class TestHandshakeLy:
    """_handshake_ly result paths."""

    def _make_dev(self) -> MagicMock:
        dev = MagicMock()
        dev.vid = 0x0416
        dev.pid = 0x5408
        return dev

    def _make_result(self, model_id=64, resolution=(1280, 480),
                     serial="", raw=bytes(64), pm_byte=68, sub_byte=0):
        r = MagicMock()
        r.model_id = model_id
        r.resolution = resolution
        r.serial = serial
        r.pm_byte = pm_byte
        r.sub_byte = sub_byte
        r.raw_response = raw
        return r

    @patch("trcc.legacy.adapters.device.ly_protocol.LyProtocol")
    def test_success_shows_pm_resolution(self, MockLy):
        proto = MagicMock()
        proto.handshake.return_value = self._make_result()
        MockLy.return_value = proto

        sec_obj = MagicMock()
        sec_obj.lines = []
        rpt = DebugReport()
        rpt._handshake_ly(self._make_dev(), sec_obj)

        text = " ".join(sec_obj.lines)
        assert "PM=68" in text
        assert "SUB=0" in text
        assert "FBL=64" in text
        assert "(1280, 480)" in text
        proto.close.assert_called_once()

    @patch("trcc.legacy.adapters.device.ly_protocol.LyProtocol")
    def test_none_result_eacces(self, MockLy):
        proto = MagicMock()
        proto.handshake.return_value = None
        proto.last_error = _make_usb_error(13)
        MockLy.return_value = proto

        sec_obj = MagicMock()
        sec_obj.lines = []
        rpt = DebugReport()
        with patch("trcc.legacy.adapters.device.factory._has_usb_errno",
                   side_effect=lambda e, n: n == 13):
            rpt._handshake_ly(self._make_dev(), sec_obj)

        text = " ".join(sec_obj.lines)
        assert "Permission denied" in text or "setup-udev" in text

    @patch("trcc.legacy.adapters.device.ly_protocol.LyProtocol")
    def test_none_result_ebusy(self, MockLy):
        proto = MagicMock()
        proto.handshake.return_value = None
        proto.last_error = _make_usb_error(16)
        MockLy.return_value = proto

        sec_obj = MagicMock()
        sec_obj.lines = []
        rpt = DebugReport()
        with patch("trcc.legacy.adapters.device.factory._has_usb_errno",
                   side_effect=lambda e, n: n == 16):
            with patch.object(rpt, "_ebusy_fallback") as mock_fallback:
                rpt._handshake_ly(self._make_dev(), sec_obj)
                mock_fallback.assert_called_once_with(sec_obj)

    @patch("trcc.legacy.adapters.device.ly_protocol.LyProtocol")
    def test_none_result_no_error(self, MockLy):
        proto = MagicMock()
        proto.handshake.return_value = None
        proto.last_error = None
        MockLy.return_value = proto

        sec_obj = MagicMock()
        sec_obj.lines = []
        rpt = DebugReport()
        with patch("trcc.legacy.adapters.device.factory._has_usb_errno",
                   return_value=False):
            rpt._handshake_ly(self._make_dev(), sec_obj)

        text = " ".join(sec_obj.lines)
        assert "no response" in text

    @patch("trcc.legacy.adapters.device.ly_protocol.LyProtocol")
    def test_close_always_called(self, MockLy):
        proto = MagicMock()
        proto.handshake.side_effect = RuntimeError("gone")
        MockLy.return_value = proto

        sec_obj = MagicMock()
        sec_obj.lines = []
        rpt = DebugReport()
        with pytest.raises(RuntimeError):
            rpt._handshake_ly(self._make_dev(), sec_obj)
        proto.close.assert_called_once()

    @patch("trcc.legacy.adapters.device.ly_protocol.LyProtocol")
    def test_raw_response_included(self, MockLy):
        proto = MagicMock()
        proto.handshake.return_value = self._make_result(raw=bytes(range(64)))
        MockLy.return_value = proto

        sec_obj = MagicMock()
        sec_obj.lines = []
        rpt = DebugReport()
        rpt._handshake_ly(self._make_dev(), sec_obj)

        text = " ".join(sec_obj.lines)
        assert "raw[0:64]" in text


# ---------------------------------------------------------------------------
# _ebusy_fallback
# ---------------------------------------------------------------------------

class TestEbusyFallback:
    """_ebusy_fallback cached handshake data paths."""

    @patch("trcc.legacy.conf.load_last_handshake")
    def test_cached_data_with_raw(self, mock_load):
        mock_load.return_value = {
            "resolution": [320, 240],
            "model_id": 50,
            "serial": "ABC123",
            "raw": "deadbeef" * 16,
        }
        sec_obj = MagicMock()
        sec_obj.lines = []
        DebugReport._ebusy_fallback(sec_obj)

        text = " ".join(sec_obj.lines)
        assert "PM=50" in text
        assert "320" in text
        assert "from cache" in text
        assert "raw[0:64]" in text

    @patch("trcc.legacy.conf.load_last_handshake")
    def test_cached_data_without_raw(self, mock_load):
        mock_load.return_value = {
            "resolution": [640, 480],
            "model_id": 64,
            "serial": "",
            "raw": "",
        }
        sec_obj = MagicMock()
        sec_obj.lines = []
        DebugReport._ebusy_fallback(sec_obj)

        text = " ".join(sec_obj.lines)
        assert "from cache" in text
        assert "raw[0:64]" not in text

    @patch("trcc.legacy.conf.load_last_handshake", return_value={})
    def test_no_cache(self, _):
        sec_obj = MagicMock()
        sec_obj.lines = []
        DebugReport._ebusy_fallback(sec_obj)

        text = " ".join(sec_obj.lines)
        assert "device in use by trcc gui" in text
        assert "from cache" not in text

    @patch("trcc.legacy.conf.load_last_handshake",
           return_value={"model_id": 50})  # no "resolution" key
    def test_cache_without_resolution_key(self, _):
        sec_obj = MagicMock()
        sec_obj.lines = []
        DebugReport._ebusy_fallback(sec_obj)

        text = " ".join(sec_obj.lines)
        # Falls into the else branch (no resolution)
        assert "device in use by trcc gui" in text


# ---------------------------------------------------------------------------
# _last_cpu_baseline
# ---------------------------------------------------------------------------

class TestProcessUsageExtra:
    """_last_cpu_baseline edge cases."""

    def test_cpu_baseline_found(self, tmp_path):
        log_file = tmp_path / ".trcc" / "trcc.log"
        log_file.parent.mkdir(parents=True)
        log_file.write_text(
            "2026-01-01 INFO some line\n"
            "2026-01-01 INFO video cache built: 162 frames, trcc CPU 5.3%\n"
            "2026-01-01 INFO another line\n"
        )
        rpt = DebugReport()
        with patch("trcc.legacy.adapters.infra.diagnostics.Path.home", return_value=tmp_path):
            rpt._last_cpu_baseline()
        _, body = _section(rpt)
        assert "trcc CPU" in body
        assert "5.3%" in body

    def test_cpu_baseline_returns_last_match(self, tmp_path):
        log_file = tmp_path / ".trcc" / "trcc.log"
        log_file.parent.mkdir(parents=True)
        log_file.write_text(
            "2026-01-01 INFO video cache built: 50 frames, trcc CPU 6.1%\n"
            "2026-01-01 INFO video cache built: 90 frames, trcc CPU 5.3%\n"
        )
        rpt = DebugReport()
        with patch("trcc.legacy.adapters.infra.diagnostics.Path.home", return_value=tmp_path):
            rpt._last_cpu_baseline()
        _, body = _section(rpt)
        assert "5.3%" in body

    def test_cpu_baseline_no_log(self, tmp_path):
        rpt = DebugReport()
        with patch("trcc.legacy.adapters.infra.diagnostics.Path.home", return_value=tmp_path):
            rpt._last_cpu_baseline()
        _, body = _section(rpt)
        assert "no log file" in body

    def test_cpu_baseline_not_found_in_log(self, tmp_path):
        log_file = tmp_path / ".trcc" / "trcc.log"
        log_file.parent.mkdir(parents=True)
        log_file.write_text("2026-01-01 INFO some unrelated line\n")
        rpt = DebugReport()
        with patch("trcc.legacy.adapters.infra.diagnostics.Path.home", return_value=tmp_path):
            rpt._last_cpu_baseline()
        _, body = _section(rpt)
        assert "not found" in body


# ---------------------------------------------------------------------------
# _config — extra edge cases
# ---------------------------------------------------------------------------

class TestConfigExtra:
    """_config section extra cases."""

    @patch("trcc.legacy.conf.load_config", return_value={
        "resolution": [320, 240],
        "temp_unit": 1,
        "format_prefs": {"time_format": 1},
    })
    def test_shows_all_present_keys(self, _):
        rpt = DebugReport()
        rpt._app_config()
        _, body = _section(rpt)
        assert "temp_unit" in body
        assert "format_prefs" in body

    @patch("trcc.legacy.conf.load_config", side_effect=RuntimeError("read error"))
    def test_exception_shows_error(self, _):
        rpt = DebugReport()
        rpt._app_config()
        _, body = _section(rpt)
        assert "Error" in body

    @patch("trcc.legacy.conf.load_config", return_value={"resolution": [320, 240]})
    def test_no_devices_key_shows_none(self, _):
        """Missing 'devices' key → shows 'none configured'."""
        rpt = DebugReport()
        rpt._app_config()
        _, body = _section(rpt)
        assert "none configured" in body


# ---------------------------------------------------------------------------
# __str__ and sections property
# ---------------------------------------------------------------------------

class TestStrAndSections:
    """__str__ formatting and sections property."""

    def test_str_separator_lines(self):
        rpt = DebugReport()
        rpt._version()
        text = str(rpt)
        assert "─" in text

    def test_str_footer_separator(self):
        rpt = DebugReport()
        text = str(rpt)
        assert "═" * 10 in text or "=" * 10 in text or text.rstrip().endswith("═" * 60)

    def test_sections_returns_title_body_pairs(self):
        rpt = DebugReport()
        rpt._version()
        sections = rpt.sections
        assert len(sections) == 1
        title, body = sections[0]
        assert title == "Version"
        assert "trcc-linux:" in body

    def test_sections_body_is_multiline_string(self):
        rpt = DebugReport()
        rpt._version()
        _, body = rpt.sections[0]
        assert "\n" in body  # multiple lines joined

    def test_empty_report_sections_is_empty_list(self):
        rpt = DebugReport()
        assert rpt.sections == []

    def test_str_includes_all_section_titles(self):
        rpt = DebugReport()
        rpt._version()

        with (
            patch("trcc.legacy.adapters.infra.diagnostics.subprocess.run",
                  return_value=MagicMock(stdout="")),
            patch("builtins.open", side_effect=FileNotFoundError),
        ):
            rpt._lsusb()
            rpt._udev_rules()

        text = str(rpt)
        assert "Version" in text
        assert "lsusb" in text
        assert "udev" in text


class TestRecentLog:
    """Tests for _recent_log section."""

    def test_no_log_file(self, tmp_path):
        rpt = DebugReport()
        with patch("trcc.legacy.adapters.infra.diagnostics.Path.home",
                    return_value=tmp_path):
            rpt._recent_log()
        body = rpt.sections[-1][1]
        assert "(no log file)" in body

    def test_empty_log(self, tmp_path):
        log_dir = tmp_path / ".trcc"
        log_dir.mkdir()
        (log_dir / "trcc.log").write_text("")
        rpt = DebugReport()
        with patch("trcc.legacy.adapters.infra.diagnostics.Path.home",
                    return_value=tmp_path):
            rpt._recent_log()
        body = rpt.sections[-1][1]
        assert "(empty log)" in body

    def test_shows_last_50_lines(self, tmp_path):
        log_dir = tmp_path / ".trcc"
        log_dir.mkdir()
        lines = [f"line {i}" for i in range(100)]
        (log_dir / "trcc.log").write_text("\n".join(lines))
        rpt = DebugReport()
        with patch("trcc.legacy.adapters.infra.diagnostics.Path.home",
                    return_value=tmp_path):
            rpt._recent_log()
        body = rpt.sections[-1][1]
        assert "line 50" in body
        assert "line 99" in body
        assert "line 49" not in body

    def test_short_log_shows_all(self, tmp_path):
        log_dir = tmp_path / ".trcc"
        log_dir.mkdir()
        (log_dir / "trcc.log").write_text("only line\n")
        rpt = DebugReport()
        with patch("trcc.legacy.adapters.infra.diagnostics.Path.home",
                    return_value=tmp_path):
            rpt._recent_log()
        body = rpt.sections[-1][1]
        assert "only line" in body

    def test_read_error(self, tmp_path):
        rpt = DebugReport()
        with patch("trcc.legacy.adapters.infra.diagnostics.Path.home",
                    return_value=tmp_path):
            log_dir = tmp_path / ".trcc"
            log_dir.mkdir()
            log_file = log_dir / "trcc.log"
            log_file.write_text("data")
            log_file.chmod(0o000)
            rpt._recent_log()
            log_file.chmod(0o644)  # restore for cleanup
        body = rpt.sections[-1][1]
        assert "Error reading log" in body
