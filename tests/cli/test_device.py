"""Tests for trcc.ui.cli._device — device detection, selection, probing."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from trcc.legacy.ui.cli._device import (
    _format,
    _probe,
    detect,
    select,
)

# =============================================================================
# TestGetService
# =============================================================================

class TestScanAndSelect:
    """DeviceService.scan_and_select() — selection logic (moved from CLI)."""

    def _make_svc(self, devices):
        from tests.conftest import make_device_service
        svc = make_device_service()
        svc._devices = devices
        return svc

    def test_explicit_path_match(self, make_detected_device):
        dev = make_detected_device(path="/dev/sg1")
        svc = self._make_svc([dev])
        with patch.object(svc, 'detect'), \
             patch.object(svc, '_discover_resolution'):
            svc.scan_and_select("/dev/sg1")
        assert svc.selected is dev

    def test_explicit_path_no_match_falls_back(self, make_detected_device):
        dev = make_detected_device(path="/dev/sg0")
        svc = self._make_svc([dev])
        with patch.object(svc, 'detect'), \
             patch.object(svc, '_discover_resolution'):
            svc.scan_and_select("/dev/sg99")
        assert svc.selected is dev

    def test_explicit_path_no_devices(self):
        svc = self._make_svc([])
        with patch.object(svc, 'detect'), \
             patch.object(svc, '_discover_resolution'):
            svc.scan_and_select("/dev/sg0")
        assert svc.selected is None

    def test_no_path_uses_saved(self, make_detected_device):
        dev = make_detected_device(path="/dev/sg2")
        svc = self._make_svc([dev])
        with patch.object(svc, 'detect'), \
             patch("trcc.legacy.conf.Settings.get_selected_device", return_value="/dev/sg2"), \
             patch.object(svc, '_discover_resolution'):
            svc.scan_and_select()
        assert svc.selected is dev

    def test_no_path_saved_not_found(self, make_detected_device):
        dev = make_detected_device(path="/dev/sg0")
        svc = self._make_svc([dev])
        with patch.object(svc, 'detect'), \
             patch("trcc.legacy.conf.Settings.get_selected_device", return_value="/dev/sg99"), \
             patch.object(svc, '_discover_resolution'):
            svc.scan_and_select()
        assert svc.selected is dev

    def test_no_path_no_saved_selects_first(self, make_detected_device):
        dev = make_detected_device()
        svc = self._make_svc([dev])
        with patch.object(svc, 'detect'), \
             patch("trcc.legacy.conf.Settings.get_selected_device", return_value=None), \
             patch.object(svc, '_discover_resolution'):
            svc.scan_and_select()
        assert svc.selected is dev

    def test_already_selected_skips_saved(self, make_detected_device):
        dev = make_detected_device()
        svc = self._make_svc([dev])
        svc._selected = dev
        with patch.object(svc, 'detect'), \
             patch("trcc.legacy.conf.Settings.get_selected_device") as mock_get, \
             patch.object(svc, '_discover_resolution'):
            svc.scan_and_select()
        mock_get.assert_not_called()

    def test_discover_called_when_selected(self, make_detected_device):
        dev = make_detected_device()
        svc = self._make_svc([dev])
        with patch.object(svc, 'detect'), \
             patch.object(svc, '_discover_resolution') as mock_disc:
            svc.scan_and_select()
        mock_disc.assert_called_once_with(dev)

    def test_discover_not_called_when_empty(self):
        svc = self._make_svc([])
        with patch.object(svc, 'detect'), \
             patch.object(svc, '_discover_resolution') as mock_disc:
            svc.scan_and_select()
        mock_disc.assert_not_called()


def test_use_jpeg_computed_from_protocol_fbl():
    """use_jpeg is computed from protocol+fbl, not hardcoded."""
    from trcc.legacy.core.models import DeviceInfo
    # Bulk + FBL=100 → RGB565
    dev = DeviceInfo(name='bulk', path='b', protocol='bulk', fbl_code=100)
    assert dev.use_jpeg is False
    # Bulk + FBL=72 → JPEG
    dev2 = DeviceInfo(name='bulk', path='b', protocol='bulk', fbl_code=72)
    assert dev2.use_jpeg is True
    # SCSI → always RGB565
    dev3 = DeviceInfo(name='scsi', path='s', protocol='scsi', fbl_code=100)
    assert dev3.use_jpeg is False


# =============================================================================
# TestProbe
# =============================================================================

class TestProbe:
    """_probe() — probe for hid_led, hid_type2, hid_type3, bulk_usblcdnew."""

    def test_unknown_implementation_returns_empty(self, make_detected_device):
        """Unknown implementation type returns empty dict."""
        dev = make_detected_device(implementation="unknown_impl")
        result = _probe(dev)
        assert result == {}

    # -- hid_led ---

    def test_hid_led_success(self, make_detected_device):
        """hid_led: probe_led_model returns model info."""
        dev = make_detected_device(
            implementation="hid_led", vid=0x0416, pid=0x8001
        )

        led_info = MagicMock()
        led_info.model_name = "PA120 Digital"
        led_info.pm = 2
        led_info.style = 2

        mock_led_mod = MagicMock()
        mock_led_mod.probe_led_model.return_value = led_info

        with patch.dict("sys.modules", {"trcc.legacy.adapters.device.led": mock_led_mod}):
            result = _probe(dev)

        assert result["model"] == "PA120 Digital"
        assert result["pm"] == 2
        assert result["style"] == 2

    def test_hid_led_no_model_name_excluded(self, make_detected_device):
        """hid_led: info with empty model_name produces empty result."""
        dev = make_detected_device(implementation="hid_led")

        led_info = MagicMock()
        led_info.model_name = None

        mock_led_mod = MagicMock()
        mock_led_mod.probe_led_model.return_value = led_info

        with patch.dict("sys.modules", {"trcc.legacy.adapters.device.led": mock_led_mod}):
            result = _probe(dev)

        assert result == {}

    def test_hid_led_probe_returns_none_excluded(self, make_detected_device):
        """hid_led: probe_led_model returning None produces empty result."""
        dev = make_detected_device(implementation="hid_led")

        mock_led_mod = MagicMock()
        mock_led_mod.probe_led_model.return_value = None

        with patch.dict("sys.modules", {"trcc.legacy.adapters.device.led": mock_led_mod}):
            result = _probe(dev)

        assert result == {}

    def test_hid_led_exception_returns_empty(self, make_detected_device):
        """hid_led: exception in probe is swallowed, returns empty dict."""
        dev = make_detected_device(implementation="hid_led")

        mock_led_mod = MagicMock()
        mock_led_mod.probe_led_model.side_effect = RuntimeError("USB error")

        with patch.dict("sys.modules", {"trcc.legacy.adapters.device.led": mock_led_mod}):
            result = _probe(dev)

        assert result == {}

    # -- hid_type2 ---

    def test_hid_type2_success_with_serial(self, make_detected_device):
        """hid_type2: HidHandshakeInfo is parsed for pm, resolution, serial."""
        dev = make_detected_device(
            implementation="hid_type2",
            vid=0x0416, pid=0x5302,
            protocol="hid", device_type=2,
        )

        class FakeHidHandshakeInfo:
            mode_byte_1 = 54
            resolution = (360, 360)
            serial = "SN123456789ABCDEF"

        fake_info = FakeHidHandshakeInfo()
        mock_protocol = MagicMock()
        mock_protocol.handshake.return_value = fake_info

        with patch("trcc.legacy.adapters.device.factory.DeviceProtocolFactory") as mock_factory_cls, \
             patch("trcc.legacy.adapters.device.hid.HidHandshakeInfo", FakeHidHandshakeInfo):
            mock_factory_cls.get_protocol.return_value = mock_protocol
            result = _probe(dev)

        assert result.get("pm") == 54
        assert result.get("resolution") == (360, 360)
        # _probe returns full serial; _format truncates to 16 when rendering
        assert result.get("serial") == "SN123456789ABCDEF"

    def test_hid_type2_no_serial_omitted(self, make_detected_device):
        """hid_type2: empty serial is not included in result."""
        dev = make_detected_device(
            implementation="hid_type2",
            vid=0x0416, pid=0x5302,
            protocol="hid", device_type=2,
        )

        class FakeHidHandshakeInfo:
            mode_byte_1 = 54
            resolution = (360, 360)
            serial = ""

        fake_info = FakeHidHandshakeInfo()
        mock_protocol = MagicMock()
        mock_protocol.handshake.return_value = fake_info

        with patch("trcc.legacy.adapters.device.factory.DeviceProtocolFactory") as mock_factory_cls, \
             patch("trcc.legacy.adapters.device.hid.HidHandshakeInfo", FakeHidHandshakeInfo):
            mock_factory_cls.get_protocol.return_value = mock_protocol
            result = _probe(dev)

        assert "serial" not in result

    def test_hid_type2_exception_returns_empty(self, make_detected_device):
        """hid_type2: exception in handshake is swallowed."""
        dev = make_detected_device(implementation="hid_type2")

        with patch("trcc.legacy.adapters.device.factory.DeviceProtocolFactory") as mock_factory_cls:
            mock_factory_cls.get_protocol.side_effect = RuntimeError("HID error")
            result = _probe(dev)

        assert result == {}

    def test_hid_type3_routes_to_hid_branch(self, make_detected_device):
        """hid_type3 takes the same HID probe path as hid_type2."""
        dev = make_detected_device(
            implementation="hid_type3",
            vid=0x0418, pid=0x5303,
            protocol="hid", device_type=3,
        )

        class FakeHidHandshakeInfo:
            mode_byte_1 = 100
            resolution = (320, 320)
            serial = ""

        fake_info = FakeHidHandshakeInfo()
        mock_protocol = MagicMock()
        mock_protocol.handshake.return_value = fake_info

        with patch("trcc.legacy.adapters.device.factory.DeviceProtocolFactory") as mock_factory_cls, \
             patch("trcc.legacy.adapters.device.hid.HidHandshakeInfo", FakeHidHandshakeInfo):
            mock_factory_cls.get_protocol.return_value = mock_protocol
            result = _probe(dev)

        assert result.get("pm") == 100
        assert result.get("resolution") == (320, 320)

    # -- bulk_usblcdnew ---

    def test_bulk_success(self, make_detected_device):
        """bulk_usblcdnew: factory handshake parsed for resolution and pm."""
        dev = make_detected_device(implementation="bulk_usblcdnew")

        hs = MagicMock()
        hs.resolution = (480, 480)
        hs.model_id = 32

        mock_bp = MagicMock()
        mock_bp.handshake.return_value = hs

        with patch("trcc.legacy.adapters.device.factory.DeviceProtocolFactory.create_protocol",
                   return_value=mock_bp):
            result = _probe(dev)

        assert result["resolution"] == (480, 480)
        assert result["pm"] == 32
        mock_bp.close.assert_called_once()

    def test_bulk_no_resolution_excluded(self, make_detected_device):
        """bulk_usblcdnew: falsy handshake result -> empty dict."""
        dev = make_detected_device(implementation="bulk_usblcdnew")

        mock_bp = MagicMock()
        mock_bp.handshake.return_value = None

        with patch("trcc.legacy.adapters.device.factory.DeviceProtocolFactory.create_protocol",
                   return_value=mock_bp):
            result = _probe(dev)

        assert result == {}
        mock_bp.close.assert_called_once()

    def test_bulk_exception_returns_empty(self, make_detected_device):
        """bulk_usblcdnew: exception in handshake is swallowed."""
        dev = make_detected_device(implementation="bulk_usblcdnew")

        with patch("trcc.legacy.adapters.device.factory.DeviceProtocolFactory.create_protocol",
                   side_effect=RuntimeError("bulk error")):
            result = _probe(dev)

        assert result == {}


# =============================================================================
# TestFormat
# =============================================================================

class TestFormat:
    """_format() — format device string with/without probe info."""

    def test_scsi_device_uses_scsi_path(self, make_detected_device):
        """SCSI device: path comes from scsi_device attribute."""
        dev = make_detected_device(
            scsi_device="/dev/sg0",
            product_name="Frost Commander",
            vid=0x87CD, pid=0x70DB,
            protocol="scsi",
        )
        line = _format(dev, probe=False)
        assert "/dev/sg0" in line
        assert "Frost Commander" in line
        assert "[87cd:70db]" in line
        assert "(SCSI)" in line

    def test_hid_device_uses_vid_pid_path(self, make_detected_device):
        """HID device: path is formatted as vid:pid."""
        dev = make_detected_device(
            scsi_device=None,
            product_name="AX120 Digital",
            vid=0x0416, pid=0x5302,
            protocol="hid",
        )
        line = _format(dev, probe=False)
        assert "0416:5302" in line
        assert "AX120 Digital" in line
        assert "(HID)" in line

    def test_bulk_device_uses_vid_pid_path(self, make_detected_device):
        """Bulk device: path is formatted as vid:pid."""
        dev = make_detected_device(
            scsi_device=None,
            product_name="Frozen Warframe Pro",
            vid=0x87AD, pid=0x70DB,
            protocol="bulk",
        )
        line = _format(dev, probe=False)
        assert "87ad:70db" in line
        assert "(BULK)" in line

    def test_ly_device_uses_vid_pid_path(self, make_detected_device):
        """LY device: path is formatted as vid:pid."""
        dev = make_detected_device(
            scsi_device=None,
            product_name="Peerless Vision",
            vid=0x0416, pid=0x5408,
            protocol="ly",
        )
        line = _format(dev, probe=False)
        assert "0416:5408" in line
        assert "(LY)" in line

    def test_led_device_uses_vid_pid_path(self, make_detected_device):
        """LED controller: path is formatted as vid:pid (issue #90)."""
        dev = make_detected_device(
            scsi_device=None,
            product_name="LED Controller",
            vid=0x0416, pid=0x8001,
            protocol="led",
        )
        line = _format(dev, probe=False)
        assert "0416:8001" in line
        assert "No device path found" not in line
        assert "(LED)" in line

    def test_unknown_protocol_no_scsi_path_shows_fallback(self, make_detected_device):
        """Unknown protocol without scsi_device: shows fallback path text."""
        dev = make_detected_device(
            scsi_device=None,
            product_name="Unknown",
            vid=0x1234, pid=0x5678,
            protocol="exotic",
        )
        line = _format(dev, probe=False)
        assert "No device path found" in line

    def test_probe_false_skips_probe(self, make_detected_device):
        """probe=False: _probe is never called."""
        dev = make_detected_device()
        with patch("trcc.legacy.ui.cli._device._probe") as mock_probe:
            _format(dev, probe=False)
        mock_probe.assert_not_called()

    def test_probe_true_calls_probe(self, make_detected_device):
        """probe=True: _probe is called and result is appended."""
        dev = make_detected_device()
        with patch("trcc.legacy.ui.cli._device._probe", return_value={}):
            line = _format(dev, probe=True)
        # Empty probe result -> no extra parens appended
        assert "(" not in line.split("—")[1] or "(SCSI)" in line

    def test_probe_with_model(self, make_detected_device):
        """probe=True with model info: model appears in output."""
        dev = make_detected_device()
        with patch("trcc.legacy.ui.cli._device._probe", return_value={"model": "PA120 Digital"}):
            line = _format(dev, probe=True)
        assert "model: PA120 Digital" in line

    def test_probe_with_resolution(self, make_detected_device):
        """probe=True with resolution: WxH string appears in output."""
        dev = make_detected_device()
        with patch("trcc.legacy.ui.cli._device._probe",
                   return_value={"resolution": (360, 360)}):
            line = _format(dev, probe=True)
        assert "resolution: 360x360" in line

    def test_probe_with_pm(self, make_detected_device):
        """probe=True with PM: PM=N appears in output."""
        dev = make_detected_device()
        with patch("trcc.legacy.ui.cli._device._probe", return_value={"pm": 54}):
            line = _format(dev, probe=True)
        assert "PM=54" in line

    def test_probe_with_serial_truncated_to_16(self, make_detected_device):
        """probe=True with long serial: serial is truncated to 16 characters."""
        dev = make_detected_device()
        with patch("trcc.legacy.ui.cli._device._probe",
                   return_value={"serial": "ABCDEFGHIJKLMNOPQRSTUVWXYZ"}):
            line = _format(dev, probe=True)
        assert "serial: ABCDEFGHIJKLMNOP" in line

    def test_probe_with_all_details(self, make_detected_device):
        """probe=True with all probe fields: all appear in output."""
        dev = make_detected_device()
        with patch("trcc.legacy.ui.cli._device._probe", return_value={
            "model": "PA120",
            "resolution": (480, 480),
            "pm": 72,
            "serial": "SN0001",
        }):
            line = _format(dev, probe=True)
        assert "model: PA120" in line
        assert "resolution: 480x480" in line
        assert "PM=72" in line
        assert "serial: SN0001" in line


# =============================================================================
# TestDetect
# =============================================================================

class TestDetect:
    """detect() command — device listing, udev warnings."""

    def _scsi_dev(
        self,
        make_detected_device,
        path: str = "/dev/sg0",
        name: str = "Frost Commander",
        vid: int = 0x87CD,
        pid: int = 0x70DB,
        protocol: str = "scsi",
    ) -> MagicMock:
        return make_detected_device(
            scsi_device=path, product_name=name,
            vid=vid, pid=pid, protocol=protocol,
        )

    def _ok_setup(self) -> MagicMock:
        """Platform setup mock with no permission warnings."""
        mock_setup = MagicMock()
        mock_setup.check_device_permissions.return_value = []
        mock_setup.no_devices_hint.return_value = None
        return mock_setup

    def test_no_devices_returns_1(self):
        """No devices detected -> prints message, returns 1."""
        mock_setup = self._ok_setup()
        result = detect(show_all=False, detect_fn=lambda: [], os_platform=mock_setup)
        assert result == 1

    def test_single_device_returns_0(self, make_detected_device):
        """One device detected -> prints 'Active:', returns 0."""
        dev = self._scsi_dev(make_detected_device)
        mock_setup = self._ok_setup()

        with patch("trcc.legacy.conf.Settings.get_selected_device", return_value=None), \
             patch("trcc.legacy.ui.cli._device._probe", return_value={}):
            result = detect(show_all=False, detect_fn=lambda: [dev], os_platform=mock_setup)

        assert result == 0

    def test_show_all_lists_all_devices(self, capsys, make_detected_device):
        """show_all=True enumerates all devices with index."""
        dev1 = self._scsi_dev(make_detected_device, "/dev/sg0", "Device A")
        dev2 = self._scsi_dev(make_detected_device, "/dev/sg1", "Device B")
        mock_setup = self._ok_setup()

        with patch("trcc.legacy.conf.Settings.get_selected_device", return_value=None), \
             patch("trcc.legacy.ui.cli._device._probe", return_value={}):
            result = detect(show_all=True, detect_fn=lambda: [dev1, dev2], os_platform=mock_setup)

        captured = capsys.readouterr()
        assert "[1]" in captured.out
        assert "[2]" in captured.out
        assert result == 0

    def test_show_all_marks_selected_device(self, capsys, make_detected_device):
        """show_all=True marks the saved selected device with '*'."""
        dev = self._scsi_dev(make_detected_device, "/dev/sg0")
        mock_setup = self._ok_setup()

        with patch("trcc.legacy.conf.Settings.get_selected_device", return_value="/dev/sg0"), \
             patch("trcc.legacy.ui.cli._device._probe", return_value={}):
            detect(show_all=True, detect_fn=lambda: [dev], os_platform=mock_setup)

        captured = capsys.readouterr()
        assert "* [1]" in captured.out

    def test_show_all_single_device_no_switch_hint(self, capsys, make_detected_device):
        """show_all with one device does not print 'use trcc select' hint."""
        dev = self._scsi_dev(make_detected_device)
        mock_setup = self._ok_setup()

        with patch("trcc.legacy.conf.Settings.get_selected_device", return_value=None), \
             patch("trcc.legacy.ui.cli._device._probe", return_value={}):
            detect(show_all=True, detect_fn=lambda: [dev], os_platform=mock_setup)

        captured = capsys.readouterr()
        assert "trcc select" not in captured.out

    def test_show_all_multiple_devices_shows_switch_hint(self, capsys, make_detected_device):
        """show_all with multiple devices prints 'use trcc select' hint."""
        dev1 = self._scsi_dev(make_detected_device, "/dev/sg0")
        dev2 = self._scsi_dev(make_detected_device, "/dev/sg1")
        mock_setup = self._ok_setup()

        with patch("trcc.legacy.conf.Settings.get_selected_device", return_value=None), \
             patch("trcc.legacy.ui.cli._device._probe", return_value={}):
            detect(show_all=True, detect_fn=lambda: [dev1, dev2], os_platform=mock_setup)

        captured = capsys.readouterr()
        assert "trcc select" in captured.out

    def test_multiple_devices_always_shows_numbered_list(self, capsys, make_detected_device):
        """Multiple devices always shows numbered list even without show_all."""
        dev0 = self._scsi_dev(make_detected_device, "/dev/sg0", "Device A")
        dev1 = self._scsi_dev(make_detected_device, "/dev/sg1", "Device B")
        mock_setup = self._ok_setup()

        with patch("trcc.legacy.conf.Settings.get_selected_device", return_value="/dev/sg1"), \
             patch("trcc.legacy.ui.cli._device._probe", return_value={}):
            detect(show_all=False, detect_fn=lambda: [dev0, dev1], os_platform=mock_setup)

        captured = capsys.readouterr()
        assert "[1]" in captured.out
        assert "[2]" in captured.out
        assert "Device B" in captured.out

    @pytest.mark.skip(reason="Phase 9: udev warning logic removed from cli._device.detect — moved to platform.run_setup")
    def test_udev_warning_printed_when_rules_missing(self, capsys, make_detected_device):
        """Device needing udev rule update prints a warning."""
        dev = self._scsi_dev(make_detected_device, protocol="scsi")
        mock_setup = MagicMock()
        mock_setup.check_device_permissions.return_value = [
            "\nudev rules are missing. Run: trcc setup-udev\nA reboot may be required."
        ]
        mock_setup.no_devices_hint.return_value = None

        with patch("trcc.legacy.conf.Settings.get_selected_device", return_value=None), \
             patch("trcc.legacy.ui.cli._device._probe", return_value={}):
            detect(show_all=False, detect_fn=lambda: [dev], os_platform=mock_setup)

        captured = capsys.readouterr()
        assert "udev rules" in captured.out
        assert "setup-udev" in captured.out

    @pytest.mark.skip(reason="Phase 9: udev warning logic removed from cli._device.detect — moved to platform.run_setup")
    def test_udev_warning_includes_reboot_for_scsi(self, capsys, make_detected_device):
        """SCSI protocol (requires_reboot=True) adds reboot notice to udev warning."""
        dev = self._scsi_dev(make_detected_device, protocol="scsi")
        mock_setup = MagicMock()
        mock_setup.check_device_permissions.return_value = [
            "\nudev rules are missing. Run: trcc setup-udev\nA reboot may be required."
        ]
        mock_setup.no_devices_hint.return_value = None

        with patch("trcc.legacy.conf.Settings.get_selected_device", return_value=None), \
             patch("trcc.legacy.ui.cli._device._probe", return_value={}):
            detect(show_all=False, detect_fn=lambda: [dev], os_platform=mock_setup)

        captured = capsys.readouterr()
        assert "reboot" in captured.out

    def test_udev_warning_no_reboot_for_hid(self, capsys, make_detected_device):
        """HID protocol (requires_reboot=False) does NOT add reboot notice."""
        dev = self._scsi_dev(make_detected_device, protocol="hid", vid=0x0416, pid=0x5302)
        dev.scsi_device = None
        mock_setup = MagicMock()
        mock_setup.check_device_permissions.return_value = [
            "\nudev rules are missing. Run: trcc setup-udev"
        ]
        mock_setup.no_devices_hint.return_value = None

        with patch("trcc.legacy.conf.Settings.get_selected_device", return_value=None), \
             patch("trcc.legacy.ui.cli._device._probe", return_value={}):
            detect(show_all=False, detect_fn=lambda: [dev], os_platform=mock_setup)

        captured = capsys.readouterr()
        assert "reboot" not in captured.out

    def test_no_udev_warning_when_rules_ok(self, capsys, make_detected_device):
        """No udev warning when check_device_permissions returns no warnings."""
        dev = self._scsi_dev(make_detected_device)
        mock_setup = self._ok_setup()

        with patch("trcc.legacy.conf.Settings.get_selected_device", return_value=None), \
             patch("trcc.legacy.ui.cli._device._probe", return_value={}):
            detect(show_all=False, detect_fn=lambda: [dev], os_platform=mock_setup)

        captured = capsys.readouterr()
        assert "udev rules" not in captured.out


# =============================================================================
# TestSelect
# =============================================================================

class TestSelect:
    """select() command — device selection by number."""

    def _scsi_dev(self, path: str = "/dev/sg0", name: str = "LCD") -> MagicMock:
        dev = MagicMock()
        dev.scsi_device = path
        dev.path = path
        dev.product_name = name
        dev.protocol = "scsi"
        dev.vid = 0x87CD
        dev.pid = 0x70DB
        return dev

    def test_no_devices_returns_1(self):
        """No devices -> prints message, returns 1."""
        result = select(1, detect_fn=lambda: [])
        assert result == 1

    def test_number_zero_is_invalid(self):
        """Number 0 is below minimum (1), returns 1."""
        dev = self._scsi_dev()
        result = select(0, detect_fn=lambda: [dev])
        assert result == 1

    def test_number_too_high_is_invalid(self):
        """Number exceeding device count returns 1."""
        dev = self._scsi_dev()
        result = select(5, detect_fn=lambda: [dev])
        assert result == 1

    def test_invalid_number_prints_device_list(self, capsys, make_detected_device):
        """Invalid number prints the available device list so the user knows what to pick."""
        dev1 = make_detected_device(path="/dev/sg0", product_name="Device A")
        dev2 = make_detected_device(path="/dev/sg1", product_name="Device B")
        dev1.scsi_device = "/dev/sg0"
        dev2.scsi_device = "/dev/sg1"
        select(5, detect_fn=lambda: [dev1, dev2])
        captured = capsys.readouterr()
        assert "[1]" in captured.out
        assert "[2]" in captured.out
        assert "Device A" in captured.out
        assert "Device B" in captured.out

    def test_valid_number_selects_device(self):
        """Valid device number saves and returns 0."""
        dev = self._scsi_dev("/dev/sg1", "Frost Commander")

        with patch("trcc.legacy.conf.Settings.save_selected_device") as mock_save:
            result = select(1, detect_fn=lambda: [dev])

        assert result == 0
        mock_save.assert_called_once_with("/dev/sg1")

    def test_valid_selection_prints_device_info(self, capsys):
        """Valid selection prints formatted device info."""
        dev = self._scsi_dev("/dev/sg0", "Frost Commander 360")

        with patch("trcc.legacy.conf.Settings.save_selected_device"):
            select(1, detect_fn=lambda: [dev])

        captured = capsys.readouterr()
        assert "/dev/sg0" in captured.out
        assert "Frost Commander 360" in captured.out

    def test_hid_device_saved_by_path_not_scsi(self, make_detected_device):
        """HID devices have no scsi_device — saved via path so selection persists."""
        dev = make_detected_device(path="0416:5408", scsi_device=None, protocol="hid")
        with patch("trcc.legacy.conf.Settings.save_selected_device") as mock_save:
            select(1, detect_fn=lambda: [dev])
        mock_save.assert_called_once_with("0416:5408")

    def test_selects_second_device_when_number_is_2(self):
        """Number 2 selects the second device in the list."""
        dev1 = self._scsi_dev("/dev/sg0", "Device A")
        dev2 = self._scsi_dev("/dev/sg1", "Device B")

        with patch("trcc.legacy.conf.Settings.save_selected_device") as mock_save:
            result = select(2, detect_fn=lambda: [dev1, dev2])

        assert result == 0
        mock_save.assert_called_once_with("/dev/sg1")
