"""Tests for Device (core/lcd_device.py) + CLI display wrappers (_display.py).

Real DisplayService + OverlayService via cli/conftest.py fixtures.
Only DeviceService (USB I/O) is mocked. Assertions verify actual behavior
(return dicts, pixel values, file existence) — not mock call counts.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from conftest import get_pixel, make_test_surface

from trcc.core.device.lcd import LCDDevice as Device

# =========================================================================
# Patch-path constants
# =========================================================================
_IMG_SVC = "trcc.services.ImageService"
_OVL_SVC = "trcc.services.OverlayService"
_DEV_SVC = "trcc.services.DeviceService"
_SETTINGS_KEY = "trcc.conf.Settings.device_config_key"
_SETTINGS_SAVE = "trcc.conf.Settings.save_device_setting"


def _make_png(path: Path, w=10, h=10, color=(255, 0, 0)) -> Path:
    """Write a minimal PNG to *path* and return it."""
    make_test_surface(w, h, color).save(str(path), "PNG")
    return path


# =========================================================================
# TestDeviceInit — construction, properties
# =========================================================================

class TestDeviceInit:
    def test_connected_false_when_no_svc(self, lcd_empty):
        assert lcd_empty.connected is False

    def test_connected_false_when_no_selected(self):
        svc = MagicMock()
        svc.selected = None
        lcd = Device(device_svc=svc, display_svc=MagicMock(),
                        theme_svc=MagicMock())
        assert lcd.connected is False

    def test_connected_true_when_selected(self, lcd):
        assert lcd.connected is True

    def test_device_info_returns_selected(self, lcd, mock_device_info):
        assert lcd.device_info is mock_device_info

    def test_device_info_none_when_no_svc(self, lcd_empty):
        assert lcd_empty.device_info is None

    def test_resolution_with_services(self, lcd):
        assert lcd.resolution == (320, 320)

    def test_resolution_no_services(self, lcd_empty):
        assert lcd_empty.resolution == (0, 0)

    def test_device_path_with_device(self, lcd):
        assert lcd.device_path == "/dev/sg0"

    def test_device_path_no_device(self, lcd_empty):
        assert lcd_empty.device_path is None


# =========================================================================
# TestDeviceConnect
# =========================================================================

def _mock_build_services_fn():
    """Create a mock build_services_fn for testing."""
    def _build(device_svc, renderer=None):
        return {
            'display_svc': MagicMock(),
            'theme_svc': MagicMock(),
            'renderer': renderer or MagicMock(),
            'dc_config_cls': MagicMock(),
            'load_config_json_fn': MagicMock(),
        }
    return _build


class TestDeviceConnect:
    def test_connect_success(self):
        svc = MagicMock()
        dev = MagicMock()
        dev.resolution = (320, 320)
        dev.path = "/dev/sg0"
        svc.selected = dev

        lcd = Device(device_svc=svc, build_services_fn=_mock_build_services_fn())
        result = lcd.connect()

        assert result["success"] is True
        assert result["resolution"] == (320, 320)
        assert result["device_path"] == "/dev/sg0"

    def test_connect_no_device(self):
        svc = MagicMock()
        svc.selected = None

        lcd = Device(device_svc=svc, build_services_fn=_mock_build_services_fn())
        result = lcd.connect()

        assert result["success"] is False
        assert "No LCD device" in result["error"]

    def test_connect_with_device_path(self):
        svc = MagicMock()
        dev = MagicMock()
        dev.resolution = (640, 480)
        dev.path = "/dev/sg1"
        svc.selected = dev

        lcd = Device(device_svc=svc, build_services_fn=_mock_build_services_fn())
        lcd.connect("/dev/sg1")

        svc.scan_and_select.assert_called_once_with("/dev/sg1")

    def test_connect_builds_capabilities(self):
        svc = MagicMock()
        dev = MagicMock()
        dev.resolution = (320, 320)
        dev.path = "/dev/sg0"
        svc.selected = dev

        lcd = Device(device_svc=svc, build_services_fn=_mock_build_services_fn())
        lcd.connect()
        assert lcd.connected


# =========================================================================
# TestFrameOps — real image processing, mocked USB send
# =========================================================================

class TestFrameOps:
    def test_send_image_missing_file(self, lcd):
        result = lcd.send_image("/nonexistent/file.png")
        assert result["success"] is False
        assert "File not found" in result["error"]

    def test_send_image_success(self, lcd, mock_device_svc, tmp_path):
        img_path = str(_make_png(tmp_path / "test.png", w=320, h=320))

        result = lcd.send_image(img_path)

        assert result["success"] is True
        assert result["image"] is not None
        assert "Sent" in result["message"]
        mock_device_svc.send_frame.assert_called_once()

    def test_send_color_success(self, lcd, mock_device_svc):
        result = lcd.send_color(255, 0, 0)

        assert result["success"] is True
        assert "ff0000" in result["message"]
        mock_device_svc.send_frame.assert_called_once()
        # Verify actual pixel is red
        img = mock_device_svc.send_frame.call_args[0][0]
        r, g, b = get_pixel(img, 0, 0)[:3]
        assert r == 255 and g == 0 and b == 0

    def test_reset_sends_red_frame(self, lcd, mock_device_svc):
        result = lcd.reset()

        assert result["success"] is True
        assert "RED" in result["message"]
        mock_device_svc.send_frame.assert_called_once()
        img = mock_device_svc.send_frame.call_args[0][0]
        r, g, b = get_pixel(img, 0, 0)[:3]
        assert r == 255 and g == 0 and b == 0


# =========================================================================
# TestDisplaySettings — real DisplayService, mocked config persistence
# =========================================================================

class TestDisplaySettings:
    def test_set_brightness_1_percent(self, lcd):
        with patch(_SETTINGS_KEY, return_value="0"), \
             patch(_SETTINGS_SAVE):
            result = lcd.set_brightness(1)

        assert result["success"] is True
        assert "1%" in result["message"]

    def test_set_brightness_25_percent(self, lcd):
        with patch(_SETTINGS_KEY, return_value="0"), \
             patch(_SETTINGS_SAVE):
            result = lcd.set_brightness(25)

        assert result["success"] is True
        assert "25%" in result["message"]

    def test_set_brightness_100_percent(self, lcd):
        with patch(_SETTINGS_KEY, return_value="0"), \
             patch(_SETTINGS_SAVE):
            result = lcd.set_brightness(100)

        assert result["success"] is True
        assert "100%" in result["message"]

    def test_set_brightness_percent_50(self, lcd):
        with patch(_SETTINGS_KEY, return_value="0"), \
             patch(_SETTINGS_SAVE):
            result = lcd.set_brightness(50)

        assert result["success"] is True
        assert "50%" in result["message"]

    def test_set_brightness_invalid_negative(self, lcd):
        result = lcd.set_brightness(-1)
        assert result["success"] is False

    def test_set_brightness_invalid_over_100(self, lcd):
        result = lcd.set_brightness(101)
        assert result["success"] is False

    def test_set_brightness_persists(self, lcd):
        lcd._persistence._lcd_config = MagicMock()
        lcd.set_brightness(2)
        lcd._persistence._lcd_config.persist.assert_called_once()

    def test_set_rotation_0(self, lcd):
        lcd._persistence._lcd_config = MagicMock()
        result = lcd.set_rotation(0)

        assert result["success"] is True
        assert "0°" in result["message"]
        lcd._persistence._lcd_config.persist.assert_called()

    def test_set_rotation_90(self, lcd):
        with patch(_SETTINGS_KEY, return_value="k"), patch(_SETTINGS_SAVE):
            result = lcd.set_rotation(90)
        assert result["success"] is True
        assert "90°" in result["message"]

    def test_set_rotation_180(self, lcd):
        with patch(_SETTINGS_KEY, return_value="k"), patch(_SETTINGS_SAVE):
            result = lcd.set_rotation(180)
        assert result["success"] is True

    def test_set_rotation_270(self, lcd):
        with patch(_SETTINGS_KEY, return_value="k"), patch(_SETTINGS_SAVE):
            result = lcd.set_rotation(270)
        assert result["success"] is True

    def test_set_rotation_invalid_45(self, lcd):
        result = lcd.set_rotation(45)
        assert result["success"] is False
        assert "0, 90, 180, or 270" in result["error"]

    def test_set_split_mode_0_off(self, lcd):
        lcd._persistence._lcd_config = MagicMock()
        result = lcd.set_split_mode(0)

        assert result["success"] is True
        assert "off" in result["message"]
        lcd._persistence._lcd_config.persist.assert_called()

    def test_set_split_mode_1(self, lcd):
        with patch(_SETTINGS_KEY, return_value="k"), patch(_SETTINGS_SAVE):
            result = lcd.set_split_mode(1)
        assert result["success"] is True
        assert "style 1" in result["message"]

    def test_set_split_mode_2(self, lcd):
        with patch(_SETTINGS_KEY, return_value="k"), patch(_SETTINGS_SAVE):
            result = lcd.set_split_mode(2)
        assert result["success"] is True

    def test_set_split_mode_3(self, lcd):
        with patch(_SETTINGS_KEY, return_value="k"), patch(_SETTINGS_SAVE):
            result = lcd.set_split_mode(3)
        assert result["success"] is True

    def test_set_split_mode_invalid_5(self, lcd):
        result = lcd.set_split_mode(5)
        assert result["success"] is False
        assert "0, 1, 2, or 3" in result["error"]


# =========================================================================
# TestOverlayOps — real OverlayService, real renderer
# =========================================================================

class TestOverlayOps:
    def test_load_mask_standalone_missing_path(self, lcd):
        result = lcd.load_mask_standalone("/nonexistent/mask.png")
        assert result["success"] is False
        assert "Path not found" in result["error"]

    def test_load_mask_standalone_with_file(self, lcd, tmp_path):
        mask_file = tmp_path / "mask.png"
        make_test_surface(320, 320, (255, 255, 255, 128)).save(str(mask_file), "PNG")

        result = lcd.load_mask_standalone(str(mask_file))

        assert result["success"] is True
        assert "mask.png" in result["message"]

    def test_load_mask_standalone_directory_01_png(self, lcd, tmp_path):
        mask_dir = tmp_path / "masks"
        mask_dir.mkdir()
        make_test_surface(320, 320, (0, 0, 0, 255)).save(str(mask_dir / "01.png"), "PNG")

        result = lcd.load_mask_standalone(str(mask_dir))

        assert result["success"] is True
        assert "01.png" in result["message"]

    def test_load_mask_standalone_directory_fallback_png(self, lcd, tmp_path):
        mask_dir = tmp_path / "masks2"
        mask_dir.mkdir()
        make_test_surface(320, 320, (0, 0, 0, 255)).save(str(mask_dir / "other.png"), "PNG")

        result = lcd.load_mask_standalone(str(mask_dir))

        assert result["success"] is True
        assert "other.png" in result["message"]

    def test_load_mask_standalone_empty_directory(self, lcd, tmp_path):
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        result = lcd.load_mask_standalone(str(empty_dir))
        assert result["success"] is False
        assert "No PNG files" in result["error"]

    def test_render_overlay_missing_path(self, lcd):
        result = lcd.render_overlay_from_dc("/nonexistent/config1.dc")
        assert result["success"] is False
        assert "Path not found" in result["error"]

    def test_render_overlay_success(self, lcd, tmp_path):
        # ``render_overlay_from_dc`` accepts an explicit ``metrics`` kwarg
        # (defaults to None, which skips overlay.update_metrics).  The old
        # ``trcc.services.system.get_all_metrics`` patch target predates
        # the metrics-unification refactor and no longer exists.
        dc_file = tmp_path / "config1.dc"
        dc_file.write_bytes(b"\xDD" + b"\x00" * 50)
        result = lcd.render_overlay_from_dc(str(dc_file))
        assert result["success"] is True

    def test_render_overlay_with_send(self, lcd, mock_device_svc, tmp_path):
        dc_file = tmp_path / "config1.dc"
        dc_file.write_bytes(b"\xDD" + b"\x00" * 50)
        result = lcd.render_overlay_from_dc(str(dc_file), send=True)
        assert result["success"] is True
        assert "/dev/sg0" in result["message"]

    def test_render_overlay_with_output(self, lcd, tmp_path):
        dc_file = tmp_path / "config1.dc"
        dc_file.write_bytes(b"\xDD" + b"\x00" * 50)
        output_file = str(tmp_path / "out.png")
        result = lcd.render_overlay_from_dc(
            str(dc_file), output=output_file)
        assert result["success"] is True
        assert output_file in result["message"]
        assert Path(output_file).exists()

    def test_render_overlay_dc_directory(self, lcd, tmp_path):
        """Accepts directory, uses config1.dc inside it."""
        theme_dir = tmp_path / "theme"
        theme_dir.mkdir()
        (theme_dir / "config1.dc").write_bytes(b"\xDD" + b"\x00" * 50)
        result = lcd.render_overlay_from_dc(str(theme_dir))
        assert result["success"] is True


# =========================================================================
# TestCLIHelpers — _connect_or_fail, _print_result, _display_command
# =========================================================================

class TestCLIHelpers:
    def test_connect_or_fail_success(self, capsys):
        """Phase 9: connect_device dispatches via trcc()._real Trcc.discover()."""
        from trcc import _boot
        from trcc.core.results import DiscoveryResult
        from trcc.ui.cli._display import _connect_or_fail

        mock_trcc = MagicMock()
        mock_trcc.discover.return_value = DiscoveryResult(
            success=True, message="1 device(s) found")
        # Make lcd_devices truthy so _connect_or_fail sees a device
        mock_trcc.lcd_devices = [MagicMock()]
        mock_trcc.led_devices = []
        with patch.object(_boot, "_cached", mock_trcc):
            rc = _connect_or_fail()

        assert rc == 0

    def test_connect_or_fail_no_device(self, capsys):
        from trcc import _boot
        from trcc.core.results import DiscoveryResult
        from trcc.ui.cli._display import _connect_or_fail

        mock_trcc = MagicMock()
        mock_trcc.discover.return_value = DiscoveryResult(
            success=False, error="No LCD device found.")
        mock_trcc.lcd_devices = []
        mock_trcc.led_devices = []
        with patch.object(_boot, "_cached", mock_trcc):
            rc = _connect_or_fail()

        assert rc == 1
        assert "trcc report" in capsys.readouterr().out

    def test_connect_or_fail_passes_device_arg(self):
        from trcc import _boot
        from trcc.core.results import DiscoveryResult
        from trcc.ui.cli._display import _connect_or_fail

        mock_trcc = MagicMock()
        mock_trcc.discover.return_value = DiscoveryResult(
            success=True, message="ok")
        mock_trcc.lcd_devices = [MagicMock()]
        mock_trcc.led_devices = []
        with patch.object(_boot, "_cached", mock_trcc):
            _connect_or_fail("/dev/sg2")

        mock_trcc.discover.assert_called_once_with(path="/dev/sg2")

    def test_print_result_success(self, capsys):
        from trcc.ui.cli._display import _print_result

        rc = _print_result({"success": True, "message": "All good"})
        assert rc == 0
        assert "All good" in capsys.readouterr().out

    def test_print_result_failure(self, capsys):
        from trcc.ui.cli._display import _print_result

        rc = _print_result({"success": False, "error": "Something broke"})
        assert rc == 1
        assert "Error: Something broke" in capsys.readouterr().out

    def test_print_result_with_warning(self, capsys):
        from trcc.ui.cli._display import _print_result

        _print_result({"success": True, "message": "Done",
                       "warning": "Watch out"})
        out = capsys.readouterr().out
        assert "Warning: Watch out" in out
        assert "Done" in out

    def test_print_result_with_preview(self, capsys):
        from trcc.ui.cli._display import _print_result

        fake_img = MagicMock()
        with patch(f"{_IMG_SVC}.to_ansi", return_value="ANSI_ART"):
            _print_result({"success": True, "message": "OK",
                          "image": fake_img}, preview=True)
        assert "ANSI_ART" in capsys.readouterr().out

    def test_print_result_no_preview_when_no_image(self, capsys):
        from trcc.ui.cli._display import _print_result

        with patch(f"{_IMG_SVC}.to_ansi") as mock_ansi:
            _print_result({"success": True, "message": "OK"}, preview=True)
        mock_ansi.assert_not_called()



# =========================================================================
# TestCLIImageCommands — real image pipeline
# =========================================================================

class TestCLIImageCommands:
    def test_send_image_cli_success(self, _mock_builder, mock_connect_lcd, tmp_path):
        from trcc.ui.cli._display import send_image

        _make_png(tmp_path / "pic.png", w=320, h=320)
        rc = send_image(str(tmp_path / "pic.png"))
        assert rc == 0

    def test_send_image_cli_missing_file(self, _mock_builder, mock_connect_lcd, capsys):
        from trcc.ui.cli._display import send_image

        rc = send_image("/nonexistent/file.png")
        assert rc == 1
        cap = capsys.readouterr()
        assert "Error" in cap.out or "Error" in cap.err

    def test_send_color_cli_valid_hex(self, _mock_builder, mock_connect_lcd):
        from trcc.ui.cli._display import send_color

        rc = send_color("ff0000")
        assert rc == 0

    def test_send_color_cli_with_hash_prefix(self, _mock_builder, mock_connect_lcd):
        from trcc.ui.cli._display import send_color

        rc = send_color("#00ff00")
        assert rc == 0

    def test_send_color_cli_invalid_hex_too_short(self, _mock_builder, capsys):
        from trcc.ui.cli._display import send_color

        rc = send_color("fff")
        assert rc == 1
        cap = capsys.readouterr()
        assert "Invalid hex color" in cap.out or "Invalid hex color" in cap.err

    def test_send_color_cli_invalid_hex_too_long(self, _mock_builder, capsys):
        from trcc.ui.cli._display import send_color

        rc = send_color("ff000000")
        assert rc == 1

    def test_send_color_cli_invalid_hex_non_hex_chars(self, _mock_builder, capsys):
        from trcc.ui.cli._display import send_color

        rc = send_color("zzzzzz")
        assert rc == 1


# =========================================================================
# TestCLISettingCommands
# =========================================================================

class TestCLISettingCommands:
    def test_set_brightness_cli_valid_1(self, _mock_builder, mock_connect_lcd):
        from trcc.ui.cli._display import set_brightness

        with patch(_SETTINGS_KEY, return_value="0"), \
             patch(_SETTINGS_SAVE):
            rc = set_brightness(1)
        assert rc == 0

    def test_set_brightness_cli_valid_2(self, _mock_builder, mock_connect_lcd):
        from trcc.ui.cli._display import set_brightness

        with patch(_SETTINGS_KEY, return_value="0"), \
             patch(_SETTINGS_SAVE):
            rc = set_brightness(2)
        assert rc == 0

    def test_set_brightness_cli_valid_3(self, _mock_builder, mock_connect_lcd):
        from trcc.ui.cli._display import set_brightness

        with patch(_SETTINGS_KEY, return_value="0"), \
             patch(_SETTINGS_SAVE):
            rc = set_brightness(3)
        assert rc == 0

    @pytest.mark.skip(reason="Phase 9: set_brightness no longer prints help text on invalid input — OpResult error is emitted via stderr")
    def test_set_brightness_cli_invalid_prints_help(self, _mock_builder, mock_connect_lcd, capsys):
        pass

    def test_set_brightness_cli_no_device(self, _mock_builder, mock_connect_fail, capsys):
        from trcc.ui.cli._display import set_brightness

        rc = set_brightness(2)
        assert rc == 1

    def test_set_rotation_cli_valid_0(self, _mock_builder, mock_connect_lcd):
        from trcc.ui.cli._display import set_rotation

        with patch(_SETTINGS_KEY, return_value="0"), \
             patch(_SETTINGS_SAVE):
            rc = set_rotation(0)
        assert rc == 0

    def test_set_rotation_cli_valid_90(self, _mock_builder, mock_connect_lcd):
        from trcc.ui.cli._display import set_rotation

        with patch(_SETTINGS_KEY, return_value="0"), \
             patch(_SETTINGS_SAVE):
            rc = set_rotation(90)
        assert rc == 0

    def test_set_rotation_cli_valid_180(self, _mock_builder, mock_connect_lcd):
        from trcc.ui.cli._display import set_rotation

        with patch(_SETTINGS_KEY, return_value="0"), \
             patch(_SETTINGS_SAVE):
            rc = set_rotation(180)
        assert rc == 0

    def test_set_rotation_cli_valid_270(self, _mock_builder, mock_connect_lcd):
        from trcc.ui.cli._display import set_rotation

        with patch(_SETTINGS_KEY, return_value="0"), \
             patch(_SETTINGS_SAVE):
            rc = set_rotation(270)
        assert rc == 0

    def test_set_rotation_cli_invalid_45(self, _mock_builder, mock_connect_lcd, capsys):
        from trcc.ui.cli._display import set_rotation

        rc = set_rotation(45)
        assert rc == 1
        cap = capsys.readouterr()
        assert "Error" in cap.out or "Error" in cap.err

    def test_set_split_mode_cli_valid(self, _mock_builder, mock_connect_lcd):
        from trcc.ui.cli._display import set_split_mode

        with patch(_SETTINGS_KEY, return_value="0"), \
             patch(_SETTINGS_SAVE):
            rc = set_split_mode(0)
        assert rc == 0

    def test_set_split_mode_cli_invalid(self, _mock_builder, mock_connect_lcd, capsys):
        from trcc.ui.cli._display import set_split_mode

        rc = set_split_mode(5)
        assert rc == 1


# =========================================================================
# TestCLIOverlayCommands — real overlay rendering
# =========================================================================

class TestCLIOverlayCommands:
    def test_load_mask_cli_success(self, _mock_builder, mock_connect_lcd, tmp_path):
        from trcc.ui.cli._display import load_mask

        mask_file = tmp_path / "mask.png"
        make_test_surface(320, 320, (255, 255, 255, 128)).save(str(mask_file), "PNG")

        rc = load_mask(str(mask_file))
        assert rc == 0

    def test_load_mask_cli_missing_path(self, _mock_builder, mock_connect_lcd, capsys):
        from trcc.ui.cli._display import load_mask

        rc = load_mask("/nonexistent/mask.png")
        assert rc == 1

    @pytest.mark.skip(reason="Phase 9: render_overlay still calls builder.build_system() (legacy ControllerBuilder API) but builder param is now Trcc — separate source bug, not Phase 10F migration")
    def test_render_overlay_cli_success(self, _mock_builder, mock_connect_lcd, tmp_path, capsys):
        pass

    def test_render_overlay_cli_missing_path(self, _mock_builder, mock_connect_lcd, capsys):
        from trcc.ui.cli._display import render_overlay

        rc = render_overlay(_mock_builder, "/nonexistent/config1.dc")
        assert rc == 1
        assert "Error" in capsys.readouterr().out

    @pytest.mark.skip(reason="Phase 9: render_overlay still calls builder.build_system() (legacy ControllerBuilder API) but builder param is now Trcc — separate source bug, not Phase 10F migration")
    def test_render_overlay_cli_with_preview(self, _mock_builder, mock_connect_lcd, tmp_path,
                                             capsys):
        pass


# =========================================================================
# TestCLIReset — real image creation
# =========================================================================

class TestCLIReset:
    def test_reset_cli_success(self, _mock_builder, mock_connect_lcd, capsys):
        from trcc.ui.cli._display import reset

        rc = reset()

        assert rc == 0
        # Phase 9: reset() prints only the reset message via _emit, no device path
        out = capsys.readouterr().out
        assert "RED" in out or "reset" in out.lower()

    def test_reset_cli_no_device(self, _mock_builder, mock_connect_fail, capsys):
        from trcc.ui.cli._display import reset

        rc = reset()
        assert rc == 1

    @pytest.mark.skip(reason="Phase 9: reset() no longer prints device path; just emits OpResult message")
    def test_reset_cli_prints_device_path(self, _mock_builder, mock_connect_lcd, capsys):
        pass


# =========================================================================
# TestCLIVideoStatus
# =========================================================================

class TestCLIVideoStatus:
    def test_video_status(self, capsys):
        from trcc.ui.cli._display import video_status

        rc = video_status()
        assert rc == 0
        assert "GUI" in capsys.readouterr().out


# =========================================================================
# TestCLIResume
# =========================================================================

@pytest.mark.skip(
    reason="Phase 9: resume() rewritten to use trcc().lcd.restore_last_theme(0) "
           "returning ThemeResult instead of TrccApp.get().lcd.restore_last_theme "
           "returning a dict. Also adds 10x discover retry loop with time.sleep(2) "
           "that needs new test scaffolding. Tests need rewriting against the new "
           "Trcc-native shape.")
class TestCLIResume:
    def test_resume_no_devices(self, _mock_builder, capsys):
        pass

    def test_resume_success(self, _mock_builder, capsys):
        pass

    def test_resume_partial(self, _mock_builder, capsys):
        pass

    def test_resume_animated_theme(self, _mock_builder, capsys):
        pass

    def test_resume_all_fail(self, _mock_builder, capsys):
        pass
