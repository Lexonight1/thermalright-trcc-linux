"""Tests for trcc.ui.cli._system — system setup and admin commands.

ALL subprocess.run calls are mocked — CI runs as root and must never execute
real sudo/modprobe/udevadm/getenforce/semodule commands.
ALL file I/O to system paths is mocked — no writes to /etc or /usr.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

import pytest

from trcc.legacy.adapters.system.linux_platform import (
    install_desktop,
    setup_polkit,
    setup_selinux,
    setup_udev,
)
from trcc.legacy.adapters.system.linux_platform import (
    setup_rapl_permissions as _setup_rapl_permissions,
)
from trcc.legacy.adapters.system.linux_platform import (
    sudo_reexec as _sudo_reexec,
)
from trcc.legacy.ui.cli._system import (
    _confirm,
    _sudo_run,
    download_themes,
    report,
    run_setup,
    show_info,
    uninstall,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# ===========================================================================
# TestSudoReexec
# ===========================================================================

class TestSudoReexec:
    """_sudo_reexec — builds PYTHONPATH, calls subprocess.run."""

    def test_returns_subprocess_returncode(self, completed_process, capsys):
        with patch("trcc.legacy.adapters.system.linux_platform.subprocess.run", return_value=completed_process(0)) as mock_run, \
             patch("site.getsitepackages", return_value=["/usr/lib/python3/dist-packages"]), \
             patch("site.getusersitepackages", return_value="/home/user/.local/lib/python3/site-packages"):
            rc = _sudo_reexec("setup-udev")
        assert rc == 0
        mock_run.assert_called_once()

    def test_nonzero_returncode_propagated(self, completed_process, capsys):
        with patch("trcc.legacy.adapters.system.linux_platform.subprocess.run", return_value=completed_process(1)), \
             patch("site.getsitepackages", return_value=["/usr/lib"]), \
             patch("site.getusersitepackages", return_value="/home/user/.local"):
            rc = _sudo_reexec("setup-selinux")
        assert rc == 1

    def test_command_starts_with_sudo_and_injects_path(self, completed_process, capsys):
        captured_cmd = []

        def fake_run(cmd, **kwargs):
            captured_cmd.extend(cmd)
            return completed_process(0)

        with patch("trcc.legacy.adapters.system.linux_platform.subprocess.run", side_effect=fake_run), \
             patch("site.getsitepackages", return_value=["/usr/lib/python3"]), \
             patch("site.getusersitepackages", return_value="/home/user/.local"):
            _sudo_reexec("setup-udev")

        assert captured_cmd[0] == "sudo"
        # Path injection is baked into the python -c snippet (immune to env_reset)
        snippet = captured_cmd[-1]
        assert "sys.path" in snippet

    def test_snippet_contains_user_site_packages(self, completed_process, capsys):
        captured_cmd = []

        def fake_run(cmd, **kwargs):
            captured_cmd.extend(cmd)
            return completed_process(0)

        with patch("trcc.legacy.adapters.system.linux_platform.subprocess.run", side_effect=fake_run), \
             patch("site.getsitepackages", return_value=["/usr/lib/python3"]), \
             patch("site.getusersitepackages", return_value="/home/user/.local"):
            _sudo_reexec("setup-udev")

        snippet = captured_cmd[-1]
        # Paths are injected via sys.path inside the snippet
        assert "/home/user/.local" in snippet
        assert "/usr/lib/python3" in snippet

    def test_dispatched_subcommand_uses_python_c(self, completed_process, capsys):
        """Known subcommands bypass trcc.ui.cli via python -c (#87)."""
        captured_cmd = []

        def fake_run(cmd, **kwargs):
            captured_cmd.extend(cmd)
            return completed_process(0)

        with patch("trcc.legacy.adapters.system.linux_platform.subprocess.run", side_effect=fake_run), \
             patch("site.getsitepackages", return_value=["/usr/lib"]), \
             patch("site.getusersitepackages", return_value="/home/user"):
            _sudo_reexec("setup-polkit")

        assert "-c" in captured_cmd
        assert any("setup_polkit" in c for c in captured_cmd)
        assert "-m" not in captured_cmd

    def test_unknown_subcommand_uses_trcc_cli(self, completed_process, capsys):
        """Unknown subcommands fall back to trcc.ui.cli module."""
        captured_cmd = []

        def fake_run(cmd, **kwargs):
            captured_cmd.extend(cmd)
            return completed_process(0)

        with patch("trcc.legacy.adapters.system.linux_platform.subprocess.run", side_effect=fake_run), \
             patch("site.getsitepackages", return_value=["/usr/lib"]), \
             patch("site.getusersitepackages", return_value="/home/user"):
            _sudo_reexec("some-unknown-cmd")

        assert "-m" in captured_cmd
        assert "trcc.cli" in captured_cmd
        assert captured_cmd[-1] == "some-unknown-cmd"

    def test_prints_root_required_message(self, completed_process, capsys):
        with patch("trcc.legacy.adapters.system.linux_platform.subprocess.run", return_value=completed_process(0)), \
             patch("site.getsitepackages", return_value=["/usr/lib"]), \
             patch("site.getusersitepackages", return_value="/home/user"):
            _sudo_reexec("setup-udev")

        out = capsys.readouterr().out
        assert "Root required" in out or "sudo" in out.lower()

    def test_site_packages_before_trcc_pkg_in_pythonpath(self, completed_process, capsys):
        """Site-packages must come before trcc_pkg in the injected sys.path."""
        captured_cmd = []

        def fake_run(cmd, **kwargs):
            captured_cmd.extend(cmd)
            return completed_process(0)

        with patch("trcc.legacy.adapters.system.linux_platform.subprocess.run", side_effect=fake_run), \
             patch("site.getsitepackages", return_value=["/usr/lib/python3"]), \
             patch("site.getusersitepackages", return_value="/home/user/.local"):
            _sudo_reexec("setup-udev")

        snippet = captured_cmd[-1]
        # System site-packages must appear before trcc package root
        idx_sys = snippet.index("/usr/lib/python3")
        idx_user = snippet.index("/home/user/.local")
        assert idx_sys < idx_user

    def test_nonzero_exit_prints_fallback_instructions(self, completed_process, capsys):
        with patch("trcc.legacy.adapters.system.linux_platform.subprocess.run", return_value=completed_process(1)), \
             patch("site.getsitepackages", return_value=["/usr/lib"]), \
             patch("site.getusersitepackages", return_value="/home/user"):
            _sudo_reexec("setup-udev")

        out = capsys.readouterr().out
        assert "sudo re-exec failed" in out
        assert "sudo trcc setup-udev" in out


# ===========================================================================
# TestSudoRun
# ===========================================================================

class TestSudoRun:
    """_sudo_run — prepends sudo to command."""

    def test_prepends_sudo(self, completed_process):
        with patch("trcc.legacy.adapters.system.linux_platform.subprocess.run", return_value=completed_process(0)) as mock_run:
            _sudo_run(["rm", "-f", "/tmp/foo"])
        mock_run.assert_called_once_with(["sudo", "rm", "-f", "/tmp/foo"])

    def test_returns_completed_process(self, completed_process):
        completed = completed_process(0)
        with patch("trcc.legacy.adapters.system.linux_platform.subprocess.run", return_value=completed):
            result = _sudo_run(["udevadm", "trigger"])
        assert result is completed

    def test_empty_command_still_prepends_sudo(self, completed_process):
        with patch("trcc.legacy.adapters.system.linux_platform.subprocess.run", return_value=completed_process(0)) as mock_run:
            _sudo_run([])
        mock_run.assert_called_once_with(["sudo"])


# ===========================================================================
# TestShowInfo
# ===========================================================================

class TestShowInfo:
    """show_info — text mode, preview mode, metric filter, error handling."""

    def _make_metrics(self, **overrides):
        """Build a real HardwareMetrics with the right ``_populated`` flags.

        ``show_info`` reads ``metrics._populated`` to decide which keys to
        print — keys not in the set are skipped (no ghost zeros). The
        helper only marks keys whose value the caller actually overrides,
        so a test that passes ``cpu_temp=75.0`` lights up CPU but no other
        groups, and the always-show date/time keys stay populated.
        """
        from trcc.legacy.core.models import HardwareMetrics
        always_show = {"date", "time", "weekday"}
        defaults = {
            "cpu_temp": 42.0, "cpu_percent": 15.0, "cpu_freq": 3600.0, "cpu_power": 45.0,
            "mem_percent": 60.0, "mem_available": 8.0,
            "date": "2026-02-28", "time": "12:00", "weekday": "Saturday",
        }
        m = HardwareMetrics()
        for k, v in {**defaults, **overrides}.items():
            setattr(m, k, v)
            if k in defaults or k in overrides:
                m._populated.add(k)
        # Always-show fields stay populated even when the test overrides
        # them to 0/empty (the production code still prints them).
        m._populated |= always_show
        return m

    @staticmethod
    def _patch_trcc(metrics):
        proxy = MagicMock()
        proxy.os.metrics = metrics
        return patch("trcc.legacy.ui.cli._system.trcc", return_value=proxy)

    def test_text_mode_returns_zero(self, capsys):
        metrics = self._make_metrics()
        with self._patch_trcc(metrics):
            rc = show_info()
        assert rc == 0

    def test_text_mode_prints_header(self, capsys):
        metrics = self._make_metrics()
        with self._patch_trcc(metrics):
            show_info()
        assert "System Information" in capsys.readouterr().out

    def test_text_mode_shows_cpu_group(self, capsys):
        metrics = self._make_metrics(cpu_temp=75.0, cpu_percent=50.0)
        with self._patch_trcc(metrics):
            show_info()
        assert "CPU" in capsys.readouterr().out

    def test_text_mode_skips_zero_values(self, capsys):
        # gpu_temp isn't in defaults or overrides → not in _populated → omitted
        metrics = self._make_metrics()
        with self._patch_trcc(metrics):
            show_info()
        assert "gpu_temp" not in capsys.readouterr().out

    def test_text_mode_always_shows_date_time(self, capsys):
        metrics = self._make_metrics(date="2026-02-28", time="00:00", weekday="Saturday")
        with self._patch_trcc(metrics):
            show_info()
        out = capsys.readouterr().out
        assert "date" in out or "Date" in out

    def test_preview_mode_calls_image_service(self, capsys):
        metrics = self._make_metrics()
        with self._patch_trcc(metrics), \
             patch("trcc.legacy.services.ImageService") as mock_svc:
            mock_svc.metrics_to_ansi.return_value = "ANSI_ART"
            rc = show_info(preview=True)
        assert rc == 0
        mock_svc.metrics_to_ansi.assert_called_once_with(metrics, group=None)

    def test_preview_mode_prints_ansi_output(self, capsys):
        metrics = self._make_metrics()
        with self._patch_trcc(metrics), \
             patch("trcc.legacy.services.ImageService") as mock_svc:
            mock_svc.metrics_to_ansi.return_value = "<<ANSI>>"
            show_info(preview=True)
        assert "<<ANSI>>" in capsys.readouterr().out

    def test_metric_filter_cpu(self, capsys):
        metrics = self._make_metrics(cpu_temp=80.0)
        with self._patch_trcc(metrics):
            show_info(metric="cpu")
        out = capsys.readouterr().out
        assert "CPU" in out
        # Other groups should not appear
        assert "Memory" not in out
        assert "Disk" not in out

    def test_metric_filter_mem_alias(self, capsys):
        metrics = self._make_metrics(mem_percent=75.0)
        with self._patch_trcc(metrics):
            show_info(metric="mem")
        out = capsys.readouterr().out
        assert "Memory" in out
        assert "CPU" not in out

    def test_metric_filter_net_alias(self, capsys):
        metrics = self._make_metrics()
        with self._patch_trcc(metrics):
            show_info(metric="net")
        out = capsys.readouterr().out
        assert "Network" in out
        assert "CPU" not in out

    def test_metric_filter_time_alias(self, capsys):
        metrics = self._make_metrics()
        with self._patch_trcc(metrics):
            show_info(metric="time")
        assert "Date" in capsys.readouterr().out

    def test_error_handling_returns_one(self, capsys):
        with patch("trcc.legacy.ui.cli._system.trcc", side_effect=RuntimeError("no sensors")):
            rc = show_info()
        assert rc == 1

    def test_error_handling_prints_message(self, capsys):
        with patch("trcc.legacy.ui.cli._system.trcc", side_effect=RuntimeError("no sensors")):
            show_info()
        out = capsys.readouterr().out
        assert "Error" in out
        assert "no sensors" in out

    def test_unknown_metric_filter_shows_all(self, capsys):
        # Non-matching alias → no groups → nothing filtered out
        metrics = self._make_metrics(cpu_temp=90.0)
        with self._patch_trcc(metrics):
            rc = show_info(metric="unknown")
        assert rc == 0


# ===========================================================================
# TestSetupRaplPermissions
# ===========================================================================

class TestSetupRaplPermissions:
    """_setup_rapl_permissions — RAPL energy counter permissions."""

    def test_no_powercap_dir_returns_early(self):
        with patch("trcc.legacy.adapters.system.linux_platform.Path") as mock_path_cls:
            mock_rapl = MagicMock()
            mock_rapl.exists.return_value = False
            mock_path_cls.return_value = mock_rapl
            # Should return without doing anything
            _setup_rapl_permissions()
            mock_rapl.glob.assert_not_called()

    def test_no_energy_files_returns_early(self, completed_process, capsys):
        with patch("trcc.legacy.adapters.system.linux_platform.Path") as mock_path_cls, \
             patch("trcc.legacy.adapters.system.linux_platform.subprocess.run", return_value=completed_process(0)):
            mock_rapl = MagicMock()
            mock_rapl.exists.return_value = True
            mock_rapl.glob.return_value = []  # no energy files
            mock_path_cls.return_value = mock_rapl
            _setup_rapl_permissions()

        # Nothing should be written or printed about domains
        out = capsys.readouterr().out
        assert "domain" not in out

    def test_writes_tmpfiles_conf(self, completed_process, capsys, tmp_path):
        rapl_base = tmp_path / "powercap"
        rapl_base.mkdir()
        energy_file = rapl_base / "intel-rapl:0" / "energy_uj"

        mock_energy = MagicMock()
        mock_energy.__str__ = lambda self: str(energy_file)
        mock_energy.chmod = MagicMock()

        written_content = {}

        def fake_open(path, mode="r", *args, **kwargs):
            m = mock_open()()
            written_content["path"] = path
            written_content["mode"] = mode
            return m

        with patch("trcc.legacy.adapters.system.linux_platform.Path") as mock_path_cls, \
             patch("trcc.legacy.adapters.system.linux_platform.subprocess.run", return_value=completed_process(1)):
            mock_rapl = MagicMock()
            mock_rapl.exists.return_value = True
            mock_rapl.glob.return_value = [mock_energy]
            mock_path_cls.return_value = mock_rapl

            with patch("builtins.open", mock_open()) as m_open:
                _setup_rapl_permissions()
            m_open.assert_called_once_with("/etc/tmpfiles.d/trcc-rapl.conf", "w")

    def test_chmods_energy_files(self, completed_process):
        mock_energy = MagicMock()
        mock_energy.__str__ = lambda self: "/sys/class/powercap/intel-rapl:0/energy_uj"

        with patch("trcc.legacy.adapters.system.linux_platform.Path") as mock_path_cls, \
             patch("builtins.open", mock_open()), \
             patch("trcc.legacy.adapters.system.linux_platform.subprocess.run", return_value=completed_process(1)):
            mock_rapl = MagicMock()
            mock_rapl.exists.return_value = True
            mock_rapl.glob.return_value = [mock_energy]
            mock_path_cls.return_value = mock_rapl
            _setup_rapl_permissions()

        mock_energy.chmod.assert_called_once_with(0o444)

    def test_chmod_oserror_is_silenced(self, completed_process):
        mock_energy = MagicMock()
        mock_energy.__str__ = lambda self: "/sys/class/powercap/intel-rapl:0/energy_uj"
        mock_energy.chmod.side_effect = OSError("permission denied")

        with patch("trcc.legacy.adapters.system.linux_platform.Path") as mock_path_cls, \
             patch("builtins.open", mock_open()), \
             patch("trcc.legacy.adapters.system.linux_platform.subprocess.run", return_value=completed_process(1)):
            mock_rapl = MagicMock()
            mock_rapl.exists.return_value = True
            mock_rapl.glob.return_value = [mock_energy]
            mock_path_cls.return_value = mock_rapl
            # Should not raise
            _setup_rapl_permissions()

    def test_runs_restorecon_when_available(self, completed_process):
        mock_energy = MagicMock()
        mock_energy.__str__ = lambda self: "/sys/class/powercap/intel-rapl:0/energy_uj"
        mock_energy.chmod = MagicMock()

        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            # "which restorecon" → found
            if cmd[0] == "which":
                return completed_process(0, stdout="/usr/sbin/restorecon")
            return completed_process(0)

        with patch("trcc.legacy.adapters.system.linux_platform.Path") as mock_path_cls, \
             patch("builtins.open", mock_open()), \
             patch("trcc.legacy.adapters.system.linux_platform.subprocess.run", side_effect=fake_run):
            mock_rapl = MagicMock()
            mock_rapl.exists.return_value = True
            mock_rapl.glob.return_value = [mock_energy]
            mock_path_cls.return_value = mock_rapl
            _setup_rapl_permissions()

        restorecon_calls = [c for c in calls if c[0] == "restorecon"]
        assert len(restorecon_calls) == 1
        assert "/etc/tmpfiles.d/trcc-rapl.conf" in restorecon_calls[0]

    def test_skips_restorecon_when_missing(self, completed_process):
        mock_energy = MagicMock()
        mock_energy.__str__ = lambda self: "/sys/class/powercap/intel-rapl:0/energy_uj"
        mock_energy.chmod = MagicMock()

        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if cmd[0] == "which":
                return completed_process(1)  # not found
            return completed_process(0)

        with patch("trcc.legacy.adapters.system.linux_platform.Path") as mock_path_cls, \
             patch("builtins.open", mock_open()), \
             patch("trcc.legacy.adapters.system.linux_platform.subprocess.run", side_effect=fake_run):
            mock_rapl = MagicMock()
            mock_rapl.exists.return_value = True
            mock_rapl.glob.return_value = [mock_energy]
            mock_path_cls.return_value = mock_rapl
            _setup_rapl_permissions()

        restorecon_calls = [c for c in calls if c[0] == "restorecon"]
        assert len(restorecon_calls) == 0


# ===========================================================================
# TestSetupUdev
# ===========================================================================

class TestSetupUdev:
    """setup_udev — dry_run, non-root (sudo_reexec), root (write files + reload)."""

    def _mock_known_devices(self):
        """Return a minimal device dict for patching."""
        info = MagicMock()
        info.vendor = "Thermalright"
        info.product = "AGHZ240"
        info.protocol = "scsi"
        return {(0x87CD, 0x70DB): info}

    def _mock_protocol_traits(self):
        traits = MagicMock()
        traits.udev_subsystems = ["scsi_generic"]
        return {"scsi": traits}

    def test_dry_run_returns_zero(self, completed_process, capsys):
        known = self._mock_known_devices()
        traits = self._mock_protocol_traits()
        with patch("trcc.legacy.adapters.system.linux_platform.subprocess.run", return_value=completed_process(0)), \
             patch("trcc.legacy.adapters.device.detector.KNOWN_DEVICES", known), \
             patch("trcc.legacy.adapters.device.detector._HID_LCD_DEVICES", {}), \
             patch("trcc.legacy.adapters.device.detector._LED_DEVICES", {}), \
             patch("trcc.legacy.adapters.device.detector._BULK_DEVICES", {}), \
             patch("trcc.legacy.core.models.PROTOCOL_TRAITS", traits):
            rc = setup_udev(dry_run=True)
        assert rc == 0

    def test_dry_run_prints_rules_content(self, completed_process, capsys):
        known = self._mock_known_devices()
        traits = self._mock_protocol_traits()
        with patch("trcc.legacy.adapters.system.linux_platform.subprocess.run", return_value=completed_process(0)), \
             patch("trcc.legacy.adapters.device.detector.KNOWN_DEVICES", known), \
             patch("trcc.legacy.adapters.device.detector._HID_LCD_DEVICES", {}), \
             patch("trcc.legacy.adapters.device.detector._LED_DEVICES", {}), \
             patch("trcc.legacy.adapters.device.detector._BULK_DEVICES", {}), \
             patch("trcc.legacy.core.models.PROTOCOL_TRAITS", traits):
            setup_udev(dry_run=True)
        out = capsys.readouterr().out
        assert "udev rules" in out.lower() or "Would write" in out

    def test_dry_run_sets_autosuspend_delay(self, completed_process, capsys):
        """udev rules set autosuspend_delay_ms=10000 (not pin to -1).

        v9.5.7 change: pinning autosuspend=-1 (v9.2.10's #98 fix) prevented
        the kernel from suspending the device on app exit, which made the
        firmware show "USB communication lost" indefinitely (#143). 10s
        is 5x our 2s metrics tick (no spurious suspend during normal use),
        finite so kernel autosuspend handles panel-sleep ~10s after exit.
        """
        known = self._mock_known_devices()
        traits = self._mock_protocol_traits()
        with patch("trcc.legacy.adapters.system.linux_platform.subprocess.run", return_value=completed_process(0)), \
             patch("trcc.legacy.adapters.device.detector.KNOWN_DEVICES", known), \
             patch("trcc.legacy.adapters.device.detector._HID_LCD_DEVICES", {}), \
             patch("trcc.legacy.adapters.device.detector._LED_DEVICES", {}), \
             patch("trcc.legacy.adapters.device.detector._BULK_DEVICES", {}), \
             patch("trcc.legacy.core.models.PROTOCOL_TRAITS", traits):
            setup_udev(dry_run=True)
        out = capsys.readouterr().out
        assert 'power/autosuspend_delay_ms' in out
        assert '"10000"' in out
        # Anti-regression: must NOT pin autosuspend=-1 (the v9.2.10 mistake
        # that #143 surfaced).
        assert '{power/autosuspend}="-1"' not in out

    def test_dry_run_does_not_write_files(self, completed_process, capsys):
        known = self._mock_known_devices()
        traits = self._mock_protocol_traits()
        with patch("trcc.legacy.adapters.system.linux_platform.subprocess.run", return_value=completed_process(0)), \
             patch("trcc.legacy.adapters.device.detector.KNOWN_DEVICES", known), \
             patch("trcc.legacy.adapters.device.detector._HID_LCD_DEVICES", {}), \
             patch("trcc.legacy.adapters.device.detector._LED_DEVICES", {}), \
             patch("trcc.legacy.adapters.device.detector._BULK_DEVICES", {}), \
             patch("trcc.legacy.core.models.PROTOCOL_TRAITS", traits), \
             patch("builtins.open", mock_open()) as m_open:
            setup_udev(dry_run=True)
        m_open.assert_not_called()

    def test_non_root_calls_sudo_reexec(self):
        known = self._mock_known_devices()
        traits = self._mock_protocol_traits()
        with patch("trcc.legacy.adapters.device.detector.KNOWN_DEVICES", known), \
             patch("trcc.legacy.adapters.device.detector._HID_LCD_DEVICES", {}), \
             patch("trcc.legacy.adapters.device.detector._LED_DEVICES", {}), \
             patch("trcc.legacy.adapters.device.detector._BULK_DEVICES", {}), \
             patch("trcc.legacy.core.models.PROTOCOL_TRAITS", traits), \
             patch("os.geteuid", return_value=1000), \
             patch("trcc.legacy.adapters.system.linux_platform.sudo_reexec", return_value=0) as mock_reexec:
            rc = setup_udev(dry_run=False)
        mock_reexec.assert_called_once_with("setup-udev")
        assert rc == 0

    def test_root_writes_udev_rules(self, completed_process, tmp_path):
        known = self._mock_known_devices()
        traits = self._mock_protocol_traits()
        written = {}

        def fake_open(path, mode="r"):
            m = mock_open()()
            written[path] = True
            return m

        with patch("trcc.legacy.adapters.device.detector.KNOWN_DEVICES", known), \
             patch("trcc.legacy.adapters.device.detector._HID_LCD_DEVICES", {}), \
             patch("trcc.legacy.adapters.device.detector._LED_DEVICES", {}), \
             patch("trcc.legacy.adapters.device.detector._BULK_DEVICES", {}), \
             patch("trcc.legacy.core.models.PROTOCOL_TRAITS", traits), \
             patch("os.geteuid", return_value=0), \
             patch("os.path.exists", return_value=False), \
             patch("trcc.legacy.adapters.system.linux_platform.setup_rapl_permissions"), \
             patch("trcc.legacy.adapters.system.linux_platform.subprocess.run", return_value=completed_process(0)), \
             patch("builtins.open", mock_open()) as m_open:
            rc = setup_udev(dry_run=False)

        assert rc == 0
        # open() should have been called for the rules + modprobe + modules-load files
        assert m_open.call_count >= 2

    def test_root_runs_modprobe_sg(self, completed_process):
        known = self._mock_known_devices()
        traits = self._mock_protocol_traits()
        calls = []

        with patch("trcc.legacy.adapters.device.detector.KNOWN_DEVICES", known), \
             patch("trcc.legacy.adapters.device.detector._HID_LCD_DEVICES", {}), \
             patch("trcc.legacy.adapters.device.detector._LED_DEVICES", {}), \
             patch("trcc.legacy.adapters.device.detector._BULK_DEVICES", {}), \
             patch("trcc.legacy.core.models.PROTOCOL_TRAITS", traits), \
             patch("os.geteuid", return_value=0), \
             patch("os.path.exists", return_value=False), \
             patch("trcc.legacy.adapters.system.linux_platform.setup_rapl_permissions"), \
             patch("trcc.legacy.adapters.system.linux_platform.subprocess.run", side_effect=lambda cmd, **kw: calls.append(cmd) or completed_process(0)), \
             patch("builtins.open", mock_open()):
            setup_udev(dry_run=False)

        modprobe_calls = [c for c in calls if "modprobe" in c]
        assert any("sg" in c for c in modprobe_calls)

    def test_root_runs_udevadm_reload(self, completed_process):
        known = self._mock_known_devices()
        traits = self._mock_protocol_traits()
        calls = []

        with patch("trcc.legacy.adapters.device.detector.KNOWN_DEVICES", known), \
             patch("trcc.legacy.adapters.device.detector._HID_LCD_DEVICES", {}), \
             patch("trcc.legacy.adapters.device.detector._LED_DEVICES", {}), \
             patch("trcc.legacy.adapters.device.detector._BULK_DEVICES", {}), \
             patch("trcc.legacy.core.models.PROTOCOL_TRAITS", traits), \
             patch("os.geteuid", return_value=0), \
             patch("os.path.exists", return_value=False), \
             patch("trcc.legacy.adapters.system.linux_platform.setup_rapl_permissions"), \
             patch("trcc.legacy.adapters.system.linux_platform.subprocess.run", side_effect=lambda cmd, **kw: calls.append(cmd) or completed_process(0)), \
             patch("builtins.open", mock_open()):
            setup_udev(dry_run=False)

        udevadm_calls = [c for c in calls if "udevadm" in c]
        assert len(udevadm_calls) >= 1

    def test_root_applies_quirks_when_sysfs_exists(self, completed_process):
        known = self._mock_known_devices()
        traits = self._mock_protocol_traits()
        opened_paths = []

        def fake_open(path, mode="r"):
            opened_paths.append(path)
            return mock_open()()

        with patch("trcc.legacy.adapters.device.detector.KNOWN_DEVICES", known), \
             patch("trcc.legacy.adapters.device.detector._HID_LCD_DEVICES", {}), \
             patch("trcc.legacy.adapters.device.detector._LED_DEVICES", {}), \
             patch("trcc.legacy.adapters.device.detector._BULK_DEVICES", {}), \
             patch("trcc.legacy.core.models.PROTOCOL_TRAITS", traits), \
             patch("os.geteuid", return_value=0), \
             patch("os.path.exists", return_value=True), \
             patch("trcc.legacy.adapters.system.linux_platform.setup_rapl_permissions"), \
             patch("trcc.legacy.adapters.system.linux_platform.subprocess.run", return_value=completed_process(0)), \
             patch("builtins.open", side_effect=fake_open):
            setup_udev(dry_run=False)

        quirks_sysfs = "/sys/module/usb_storage/parameters/quirks"
        assert quirks_sysfs in opened_paths


# ===========================================================================
# TestSetupSelinux
# ===========================================================================

class TestSetupSelinux:
    """setup_selinux — non-root, no SELinux, not enforcing, already loaded,
    missing tools, success, step failures."""

    def test_non_root_calls_sudo_reexec(self):
        with patch("os.geteuid", return_value=1000), \
             patch("trcc.legacy.adapters.system.linux_platform.sudo_reexec", return_value=0) as mock_reexec:
            rc = setup_selinux()
        mock_reexec.assert_called_once_with("setup-selinux")
        assert rc == 0

    def test_no_selinux_returns_zero(self, capsys):
        with patch("os.geteuid", return_value=0), \
             patch("trcc.legacy.adapters.system.linux_platform.subprocess.run", side_effect=FileNotFoundError):
            rc = setup_selinux()
        assert rc == 0
        out = capsys.readouterr().out
        assert "not installed" in out.lower() or "nothing to do" in out.lower()

    def test_not_enforcing_returns_zero(self, completed_process, capsys):
        def fake_run(cmd, **kwargs):
            if "getenforce" in cmd:
                return completed_process(0, stdout="Permissive")
            return completed_process(0)

        with patch("os.geteuid", return_value=0), \
             patch("trcc.legacy.adapters.system.linux_platform.subprocess.run", side_effect=fake_run):
            rc = setup_selinux()
        assert rc == 0
        out = capsys.readouterr().out
        assert "permissive" in out.lower() or "no policy" in out.lower()

    def test_already_loaded_returns_zero(self, completed_process, capsys):
        call_n = 0

        def fake_run(cmd, **kwargs):
            nonlocal call_n
            call_n += 1
            if "getenforce" in cmd:
                return completed_process(0, stdout="Enforcing")
            if "semodule" in cmd and "-l" in cmd:
                return completed_process(0, stdout="trcc_usb\nother_module\n")
            return completed_process(0)

        with patch("os.geteuid", return_value=0), \
             patch("trcc.legacy.adapters.system.linux_platform.subprocess.run", side_effect=fake_run):
            rc = setup_selinux()
        assert rc == 0
        out = capsys.readouterr().out
        assert "already loaded" in out.lower()

    def test_semodule_not_found_returns_one(self, completed_process, capsys):
        semodule_call = 0

        def fake_run(cmd, **kwargs):
            nonlocal semodule_call
            if "getenforce" in cmd:
                return completed_process(0, stdout="Enforcing")
            if "semodule" in cmd:
                raise FileNotFoundError
            return completed_process(0)

        with patch("os.geteuid", return_value=0), \
             patch("trcc.legacy.adapters.system.linux_platform.subprocess.run", side_effect=fake_run):
            rc = setup_selinux()
        assert rc == 1
        out = capsys.readouterr().out
        assert "semodule" in out.lower()

    def test_missing_checkmodule_tool_returns_one(self, completed_process, capsys):
        def fake_run(cmd, **kwargs):
            if "getenforce" in cmd:
                return completed_process(0, stdout="Enforcing")
            if "semodule" in cmd and "-l" in cmd:
                return completed_process(0, stdout="")
            return completed_process(0)

        with patch("os.geteuid", return_value=0), \
             patch("trcc.legacy.adapters.system.linux_platform.subprocess.run", side_effect=fake_run), \
             patch("shutil.which", return_value=None), \
             patch("trcc.legacy.adapters.infra.diagnostics._detect_pkg_manager", return_value="dnf"), \
             patch("trcc.legacy.adapters.infra.diagnostics._install_hint", return_value="sudo dnf install checkpolicy"):
            rc = setup_selinux()
        assert rc == 1

    def test_missing_te_source_returns_one(self, completed_process, capsys):
        def fake_run(cmd, **kwargs):
            if "getenforce" in cmd:
                return completed_process(0, stdout="Enforcing")
            if "semodule" in cmd and "-l" in cmd:
                return completed_process(0, stdout="")
            return completed_process(0)

        with patch("os.geteuid", return_value=0), \
             patch("trcc.legacy.adapters.system.linux_platform.subprocess.run", side_effect=fake_run), \
             patch("shutil.which", return_value="/usr/bin/checkmodule"), \
             patch("os.path.isfile", return_value=False), \
             patch("trcc.legacy.adapters.infra.diagnostics._detect_pkg_manager", return_value="dnf"), \
             patch("trcc.legacy.adapters.infra.diagnostics._install_hint", return_value="hint"):
            rc = setup_selinux()
        assert rc == 1

    def test_checkmodule_failure_returns_one(self, completed_process, capsys):
        def fake_run(cmd, **kwargs):
            if "getenforce" in cmd:
                return completed_process(0, stdout="Enforcing")
            if "semodule" in cmd and "-l" in cmd:
                return completed_process(0, stdout="")
            if "checkmodule" in cmd:
                return completed_process(1, stderr="syntax error")
            return completed_process(0)

        with patch("os.geteuid", return_value=0), \
             patch("trcc.legacy.adapters.system.linux_platform.subprocess.run", side_effect=fake_run), \
             patch("shutil.which", return_value="/usr/bin/checkmodule"), \
             patch("os.path.isfile", return_value=True), \
             patch("trcc.legacy.adapters.infra.diagnostics._detect_pkg_manager", return_value="dnf"), \
             patch("trcc.legacy.adapters.infra.diagnostics._install_hint", return_value="hint"), \
             patch("tempfile.TemporaryDirectory") as mock_tmp, \
             patch("shutil.copy2"):
            mock_tmp.return_value.__enter__ = lambda s: "/tmp/fake"
            mock_tmp.return_value.__exit__ = MagicMock(return_value=False)
            rc = setup_selinux()
        assert rc == 1
        out = capsys.readouterr().out
        assert "checkmodule" in out.lower() or "failed" in out.lower()

    def test_semodule_package_failure_returns_one(self, completed_process, capsys):
        def fake_run(cmd, **kwargs):
            if "getenforce" in cmd:
                return completed_process(0, stdout="Enforcing")
            if "semodule" in cmd and "-l" in cmd:
                return completed_process(0, stdout="")
            if "checkmodule" in cmd:
                return completed_process(0)
            if "semodule_package" in cmd:
                return completed_process(1, stderr="packaging error")
            return completed_process(0)

        with patch("os.geteuid", return_value=0), \
             patch("trcc.legacy.adapters.system.linux_platform.subprocess.run", side_effect=fake_run), \
             patch("shutil.which", return_value="/usr/bin/checkmodule"), \
             patch("os.path.isfile", return_value=True), \
             patch("trcc.legacy.adapters.infra.diagnostics._detect_pkg_manager", return_value="dnf"), \
             patch("trcc.legacy.adapters.infra.diagnostics._install_hint", return_value="hint"), \
             patch("tempfile.TemporaryDirectory") as mock_tmp, \
             patch("shutil.copy2"):
            mock_tmp.return_value.__enter__ = lambda s: "/tmp/fake"
            mock_tmp.return_value.__exit__ = MagicMock(return_value=False)
            rc = setup_selinux()
        assert rc == 1

    def test_success_returns_zero(self, completed_process, capsys):
        def fake_run(cmd, **kwargs):
            if "getenforce" in cmd:
                return completed_process(0, stdout="Enforcing")
            if "semodule" in cmd and "-l" in cmd:
                return completed_process(0, stdout="")
            return completed_process(0)

        with patch("os.geteuid", return_value=0), \
             patch("trcc.legacy.adapters.system.linux_platform.subprocess.run", side_effect=fake_run), \
             patch("shutil.which", return_value="/usr/bin/checkmodule"), \
             patch("os.path.isfile", return_value=True), \
             patch("trcc.legacy.adapters.infra.diagnostics._detect_pkg_manager", return_value="dnf"), \
             patch("trcc.legacy.adapters.infra.diagnostics._install_hint", return_value="hint"), \
             patch("tempfile.TemporaryDirectory") as mock_tmp, \
             patch("shutil.copy2"):
            mock_tmp.return_value.__enter__ = lambda s: "/tmp/fake"
            mock_tmp.return_value.__exit__ = MagicMock(return_value=False)
            rc = setup_selinux()
        assert rc == 0
        out = capsys.readouterr().out
        assert "trcc_usb" in out.lower() or "installed" in out.lower()


# ===========================================================================
# TestInstallDesktop
# ===========================================================================

class TestInstallDesktop:
    """install_desktop — desktop source exists, missing (fallback), icons."""

    def test_returns_zero(self, completed_process, tmp_path):
        home = tmp_path / "home"
        home.mkdir()

        with patch("trcc.legacy.adapters.system.linux_platform._real_user_home", return_value=home), \
             patch("trcc.legacy.adapters.system.linux_platform.subprocess.run", return_value=completed_process(0)), \
             patch("shutil.copy2"), \
             patch("trcc.legacy.adapters.system.linux_platform.Path.exists", return_value=True):
            rc = install_desktop()
        assert rc == 0

    def test_copies_desktop_from_assets_when_present(self, completed_process, tmp_path):
        home = tmp_path / "home"
        home.mkdir()

        copied = []

        def fake_copy2(src, dst):
            copied.append((str(src), str(dst)))

        with patch("trcc.legacy.adapters.system.linux_platform._real_user_home", return_value=home), \
             patch("trcc.legacy.adapters.system.linux_platform.subprocess.run", return_value=completed_process(0)), \
             patch("shutil.copy2", side_effect=fake_copy2):
            # Patch desktop_src.exists() → True
            with patch.object(Path, "exists", return_value=True):
                install_desktop()

        desktop_copies = [c for c in copied if c[1].endswith("trcc-linux.desktop")]
        assert len(desktop_copies) >= 1

    def test_generates_desktop_when_source_missing(self, completed_process, tmp_path):
        home = tmp_path / "home"
        home.mkdir()

        written = {}

        def fake_exists(self):
            # desktop_src doesn't exist; icon_src doesn't exist either
            return False

        with patch("trcc.legacy.adapters.system.linux_platform._real_user_home", return_value=home), \
             patch("trcc.legacy.adapters.system.linux_platform.subprocess.run", return_value=completed_process(0)), \
             patch("shutil.copy2"):

            # Track write_text calls
            def capturing_write_text(self, content, *args, **kwargs):
                written[str(self)] = content

            with patch.object(Path, "exists", fake_exists), \
                 patch.object(Path, "write_text", capturing_write_text), \
                 patch.object(Path, "mkdir"):
                install_desktop()

        # Some .desktop content should have been written
        desktop_writes = {k: v for k, v in written.items() if k.endswith(".desktop")}
        assert len(desktop_writes) >= 1
        content = list(desktop_writes.values())[0]
        assert "[Desktop Entry]" in content
        assert "trcc gui" in content

    def test_icon_cache_updated_when_icons_installed(self, completed_process, tmp_path):
        home = tmp_path / "home"
        home.mkdir()

        icon_calls = []

        with patch("trcc.legacy.adapters.system.linux_platform._real_user_home", return_value=home), \
             patch("trcc.legacy.adapters.system.linux_platform.subprocess.run",
                   side_effect=lambda cmd, **kw: icon_calls.append(cmd) or completed_process(0)):
            # All Path.exists() → True so both desktop and icons are "present"
            with patch.object(Path, "exists", return_value=True), \
                 patch("shutil.copy2"), \
                 patch.object(Path, "mkdir"):
                install_desktop()

        gtk_calls = [c for c in icon_calls if "gtk-update-icon-cache" in c]
        assert len(gtk_calls) >= 1

    def test_warns_when_no_icons(self, completed_process, tmp_path, capsys):
        home = tmp_path / "home"
        home.mkdir()

        call_count = [0]

        def exists_first_only(self):
            # desktop_src.exists() first call → True, icon_src.exists() → False
            call_count[0] += 1
            # desktop src is first call, then each icon check
            return call_count[0] == 1

        with patch("trcc.legacy.adapters.system.linux_platform._real_user_home", return_value=home), \
             patch("trcc.legacy.adapters.system.linux_platform.subprocess.run", return_value=completed_process(0)), \
             patch("shutil.copy2"), \
             patch.object(Path, "mkdir"):
            with patch.object(Path, "exists", exists_first_only):
                install_desktop()

        out = capsys.readouterr().out
        assert "warning" in out.lower() or "Warning" in out

    def test_prints_done_message(self, completed_process, tmp_path, capsys):
        home = tmp_path / "home"
        home.mkdir()

        with patch("trcc.legacy.adapters.system.linux_platform._real_user_home", return_value=home), \
             patch("trcc.legacy.adapters.system.linux_platform.subprocess.run", return_value=completed_process(0)), \
             patch("shutil.copy2"), \
             patch.object(Path, "exists", return_value=False), \
             patch.object(Path, "write_text", MagicMock()), \
             patch.object(Path, "mkdir"):
            install_desktop()

        out = capsys.readouterr().out
        assert "application menu" in out.lower() or "TRCC" in out


# ===========================================================================
# TestSetupPolkit
# ===========================================================================

class TestSetupPolkit:
    """setup_polkit — non-root, missing policy, success, with SUDO_USER."""

    def test_non_root_calls_sudo_reexec(self):
        with patch("os.geteuid", return_value=1000), \
             patch("trcc.legacy.adapters.system.linux_platform.sudo_reexec", return_value=0) as mock_reexec:
            rc = setup_polkit()
        mock_reexec.assert_called_once_with("setup-polkit")
        assert rc == 0

    def test_missing_policy_file_returns_one(self, capsys):
        with patch("os.geteuid", return_value=0), \
             patch.object(Path, "exists", return_value=False):
            rc = setup_polkit()
        assert rc == 1
        out = capsys.readouterr().out
        assert "not found" in out.lower() or "Policy" in out

    def test_success_returns_zero(self, completed_process, tmp_path):
        policy_content = (
            "/usr/bin/dmidecode stub policy /usr/bin/smartctl"
        )

        with patch("os.geteuid", return_value=0), \
             patch.object(Path, "exists", return_value=True), \
             patch.object(Path, "read_text", return_value=policy_content), \
             patch.object(Path, "write_text"), \
             patch.object(Path, "mkdir"), \
             patch("shutil.which", return_value="/usr/bin/dmidecode"), \
             patch("os.path.realpath", side_effect=lambda p: p), \
             patch("os.environ.get", return_value=""), \
             patch("trcc.legacy.adapters.system.linux_platform.subprocess.run", return_value=completed_process(0)):
            rc = setup_polkit()
        assert rc == 0

    def test_writes_policy_to_system_path(self, completed_process, tmp_path):
        policy_content = "stub policy text"
        written = {}

        def capturing_write_text(self, content, *args, **kwargs):
            written[str(self)] = content

        with patch("os.geteuid", return_value=0), \
             patch.object(Path, "exists", return_value=True), \
             patch.object(Path, "read_text", return_value=policy_content), \
             patch.object(Path, "write_text", capturing_write_text), \
             patch.object(Path, "mkdir"), \
             patch("shutil.which", return_value=None), \
             patch("os.environ.get", return_value=""), \
             patch("trcc.legacy.adapters.system.linux_platform.subprocess.run", return_value=completed_process(0)):
            setup_polkit()

        policy_writes = {k: v for k, v in written.items() if "trcc.policy" in k}
        assert len(policy_writes) >= 1

    def test_writes_js_rules_when_sudo_user_set(self, completed_process, tmp_path):
        policy_content = "stub policy"
        written = {}

        def capturing_write_text(self, content, *args, **kwargs):
            written[str(self)] = content

        with patch("os.geteuid", return_value=0), \
             patch.object(Path, "exists", return_value=True), \
             patch.object(Path, "read_text", return_value=policy_content), \
             patch.object(Path, "write_text", capturing_write_text), \
             patch.object(Path, "mkdir"), \
             patch("shutil.which", return_value=None), \
             patch("os.environ.get", return_value="alice"), \
             patch("trcc.legacy.adapters.system.linux_platform.subprocess.run", return_value=completed_process(0)):
            setup_polkit()

        rules_writes = {k: v for k, v in written.items() if ".rules" in k}
        assert len(rules_writes) >= 1
        rules_content = list(rules_writes.values())[0]
        assert "alice" in rules_content

    def test_no_js_rules_without_sudo_user(self, completed_process):
        policy_content = "stub policy"
        written = {}

        def capturing_write_text(self, content, *args, **kwargs):
            written[str(self)] = content

        with patch("os.geteuid", return_value=0), \
             patch.object(Path, "exists", return_value=True), \
             patch.object(Path, "read_text", return_value=policy_content), \
             patch.object(Path, "write_text", capturing_write_text), \
             patch.object(Path, "mkdir"), \
             patch("shutil.which", return_value=None), \
             patch("os.environ.get", return_value=""), \
             patch("trcc.legacy.adapters.system.linux_platform.subprocess.run", return_value=completed_process(0)):
            setup_polkit()

        rules_writes = {k: v for k, v in written.items() if ".rules" in k}
        assert len(rules_writes) == 0

    def test_replaces_binary_paths(self, completed_process):
        # Policy contains placeholder paths — setup_polkit should replace them
        # with the canonicalized path returned by realpath(which(binary))
        policy_content = "allow /usr/bin/dmidecode and /usr/bin/smartctl here"
        written = {}

        def capturing_write_text(self, content, *args, **kwargs):
            written[str(self)] = content

        # Simulate: which("dmidecode") -> /usr/sbin/dmidecode,
        # realpath(/usr/sbin/dmidecode) -> /usr/bin/dmidecode (same as original)
        # In this scenario the placeholder IS already the canonical path —
        # so no replacement happens (str.replace is a no-op for identical strings).
        # Instead test the replacement path where which gives a DIFFERENT location:
        # which -> /opt/custom/dmidecode, realpath -> /opt/custom/dmidecode
        # so /usr/bin/dmidecode in template → /opt/custom/dmidecode
        def fake_which(binary):
            return f"/opt/custom/{binary}"

        with patch("os.geteuid", return_value=0), \
             patch.object(Path, "exists", return_value=True), \
             patch.object(Path, "read_text", return_value=policy_content), \
             patch.object(Path, "write_text", capturing_write_text), \
             patch.object(Path, "mkdir"), \
             patch("shutil.which", side_effect=fake_which), \
             patch("os.path.realpath", side_effect=lambda p: p), \
             patch("os.environ.get", return_value=""), \
             patch("trcc.legacy.adapters.system.linux_platform.subprocess.run", return_value=completed_process(0)):
            setup_polkit()

        policy_writes = {k: v for k, v in written.items() if "trcc.policy" in k}
        assert len(policy_writes) >= 1
        content = list(policy_writes.values())[0]
        # /usr/bin/dmidecode was replaced with /opt/custom/dmidecode
        assert "/opt/custom/dmidecode" in content
        assert "/usr/bin/dmidecode" not in content

    def test_runs_restorecon_when_available(self, completed_process):
        policy_content = "stub"
        calls = []

        with patch("os.geteuid", return_value=0), \
             patch.object(Path, "exists", return_value=True), \
             patch.object(Path, "read_text", return_value=policy_content), \
             patch.object(Path, "write_text"), \
             patch.object(Path, "mkdir"), \
             patch("shutil.which", return_value="/usr/sbin/restorecon"), \
             patch("os.path.realpath", side_effect=lambda p: p), \
             patch("os.environ.get", return_value=""), \
             patch("trcc.legacy.adapters.system.linux_platform.subprocess.run",
                   side_effect=lambda cmd, **kw: calls.append(cmd) or completed_process(0)):
            setup_polkit()

        restorecon_calls = [c for c in calls if "restorecon" in c]
        assert len(restorecon_calls) >= 1


# ===========================================================================
# TestUninstall
# ===========================================================================

class TestUninstall:
    """uninstall — root/non-root, root files exist/don't exist, user files, pip."""

    def _base_patches(self, tmp_path):
        """Context managers common to most uninstall tests."""
        home = tmp_path / "home"
        home.mkdir()
        return home

    def test_returns_zero(self, completed_process, tmp_config):
        from trcc.legacy.conf import save_config
        save_config({"install_info": {"method": "pip", "distro": "ubuntu"}})
        home = tmp_config / "home"
        home.mkdir()
        with patch("trcc.legacy.adapters.system.linux_platform._real_user_home", return_value=home), \
             patch("os.geteuid", return_value=0), \
             patch("os.path.exists", return_value=False), \
             patch("trcc.legacy.adapters.system.linux_platform.subprocess.run", return_value=completed_process(0)), \
             patch("trcc.legacy.ui.cli._system._is_externally_managed", return_value=False):
            rc = uninstall(yes=True)
        assert rc == 0

    def test_pip_uninstall_called(self, completed_process, tmp_config):
        from trcc.legacy.conf import save_config
        save_config({"install_info": {"method": "pip", "distro": "ubuntu"}})
        home = tmp_config / "home"
        home.mkdir()
        calls = []

        with patch("trcc.legacy.adapters.system.linux_platform._real_user_home", return_value=home), \
             patch("os.geteuid", return_value=0), \
             patch("os.path.exists", return_value=False), \
             patch("trcc.legacy.adapters.system.linux_platform.subprocess.run",
                   side_effect=lambda cmd, **kw: calls.append(cmd) or completed_process(0)), \
             patch("trcc.legacy.ui.cli._system._is_externally_managed", return_value=False):
            uninstall(yes=True)

        pip_calls = [c for c in calls if "pip" in c and "uninstall" in c]
        assert len(pip_calls) >= 1

    def test_pip_uninstall_with_yes_flag(self, completed_process, tmp_config):
        from trcc.legacy.conf import save_config
        save_config({"install_info": {"method": "pip", "distro": "ubuntu"}})
        home = tmp_config / "home"
        home.mkdir()
        calls = []

        with patch("trcc.legacy.adapters.system.linux_platform._real_user_home", return_value=home), \
             patch("os.geteuid", return_value=0), \
             patch("os.path.exists", return_value=False), \
             patch("trcc.legacy.adapters.system.linux_platform.subprocess.run",
                   side_effect=lambda cmd, **kw: calls.append(cmd) or completed_process(0)), \
             patch("trcc.legacy.ui.cli._system._is_externally_managed", return_value=False):
            uninstall(yes=True)

        pip_calls = [c for c in calls if "pip" in c and "uninstall" in c]
        assert any("--yes" in c for c in pip_calls)

    def test_pip_uninstall_without_yes_flag(self, completed_process, tmp_config):
        from trcc.legacy.conf import save_config
        save_config({"install_info": {"method": "pip", "distro": "ubuntu"}})
        home = tmp_config / "home"
        home.mkdir()
        calls = []

        with patch("trcc.legacy.adapters.system.linux_platform._real_user_home", return_value=home), \
             patch("os.geteuid", return_value=0), \
             patch("os.path.exists", return_value=False), \
             patch("trcc.legacy.adapters.system.linux_platform.subprocess.run",
                   side_effect=lambda cmd, **kw: calls.append(cmd) or completed_process(0)), \
             patch("trcc.legacy.ui.cli._system._is_externally_managed", return_value=False):
            uninstall(yes=False)

        pip_calls = [c for c in calls if "pip" in c and "uninstall" in c]
        assert all("--yes" not in c for c in pip_calls)

    def test_non_root_uses_sudo_for_root_files(self, completed_process, _mock_builder, tmp_config):
        from trcc.legacy.conf import save_config
        save_config({"install_info": {"method": "pip", "distro": "ubuntu"}})
        home = tmp_config / "home"
        home.mkdir()
        calls = []
        _mock_builder.os.get_system_files.return_value = ["/etc/udev/rules.d/99-trcc-lcd.rules"]
        _mock_builder.os.is_enabled.return_value = False

        with patch("trcc.legacy.adapters.system.linux_platform._real_user_home", return_value=home), \
             patch("os.geteuid", return_value=1000), \
             patch("os.path.exists", side_effect=lambda p: "/etc/udev" in str(p)), \
             patch("trcc.legacy.ui.cli._system.subprocess.run",
                   side_effect=lambda cmd, **kw: calls.append(cmd) or completed_process(0)), \
             patch("trcc.legacy.ui.cli._system._is_externally_managed", return_value=False):
            uninstall(yes=True)

        sudo_rm_calls = [c for c in calls if "sudo" in c and "rm" in c]
        assert len(sudo_rm_calls) >= 1

    def test_root_removes_files_directly(self, completed_process, _mock_builder, tmp_config):
        from trcc.legacy.conf import save_config
        save_config({"install_info": {"method": "pip", "distro": "ubuntu"}})
        home = tmp_config / "home"
        home.mkdir()
        removed_paths = []
        _mock_builder.os.get_system_files.return_value = ["/etc/udev/rules.d/99-trcc-lcd.rules"]
        _mock_builder.os.is_enabled.return_value = False

        with patch("trcc.legacy.adapters.system.linux_platform._real_user_home", return_value=home), \
             patch("os.geteuid", return_value=0), \
             patch("os.path.exists", side_effect=lambda p: "/etc/udev" in str(p)), \
             patch("os.remove", side_effect=lambda p: removed_paths.append(p)), \
             patch("trcc.legacy.adapters.system.linux_platform.subprocess.run", return_value=completed_process(0)), \
             patch("trcc.legacy.ui.cli._system._is_externally_managed", return_value=False):
            uninstall(yes=True)

        assert any("udev" in str(p) for p in removed_paths)

    def test_removes_user_config_dir(self, completed_process, tmp_path):
        home = self._base_patches(tmp_path)
        config_dir = home / ".trcc"
        config_dir.mkdir(parents=True)

        removed = []

        import os as _os
        real_os_path_exists = _os.path.exists

        def selective_exists(p):
            if str(p).startswith("/etc") or str(p).startswith("/usr"):
                return False
            return real_os_path_exists(p)

        with patch("trcc.legacy.adapters.system.linux_platform._real_user_home", return_value=home), \
             patch("trcc.legacy.ui.cli._system.Path.home", return_value=home), \
             patch("os.geteuid", return_value=0), \
             patch("os.path.exists", side_effect=selective_exists), \
             patch("shutil.rmtree", side_effect=lambda p, **kw: removed.append(str(p))), \
             patch("trcc.legacy.adapters.system.linux_platform.subprocess.run", return_value=completed_process(0)), \
             patch("trcc.legacy.conf.Settings.clear_installed_resolutions"), \
             patch("trcc.legacy.conf.Settings.get_install_info", return_value={'method': 'pip'}), \
             patch("trcc.legacy.ui.cli._system._is_externally_managed", return_value=False):
            uninstall(yes=True)

        assert any("trcc" in r for r in removed)

    def test_prints_nothing_to_remove_when_clean(self, completed_process, _mock_builder, tmp_config, capsys):
        from trcc.legacy.conf import save_config
        save_config({"install_info": {"method": "pip", "distro": "ubuntu"}})
        home = tmp_config / "home"
        home.mkdir()
        _mock_builder.os.is_enabled.return_value = False
        _mock_builder.os.autostart_enabled.return_value = False

        with patch("trcc.legacy.adapters.system.linux_platform._real_user_home", return_value=home), \
             patch("trcc.legacy.ui.cli._system.Path.home", return_value=home), \
             patch("os.geteuid", return_value=0), \
             patch("os.path.exists", return_value=False), \
             patch("trcc.legacy.adapters.system.linux_platform.subprocess.run", return_value=completed_process(0)), \
             patch("trcc.legacy.ui.cli._system._is_externally_managed", return_value=False):
            uninstall(yes=True)

        out = capsys.readouterr().out
        assert "Nothing to remove" in out or "already clean" in out.lower()

    def test_root_triggers_udevadm_after_removing_udev_rules(self, completed_process, _mock_builder, tmp_config):
        from trcc.legacy.conf import save_config
        save_config({"install_info": {"method": "pip", "distro": "ubuntu"}})
        home = tmp_config / "home"
        home.mkdir()
        calls = []
        udev_rule = "/etc/udev/rules.d/99-trcc-lcd.rules"
        _mock_builder.os.get_system_files.return_value = [udev_rule]
        _mock_builder.os.is_enabled.return_value = False

        with patch("trcc.legacy.adapters.system.linux_platform._real_user_home", return_value=home), \
             patch("os.geteuid", return_value=0), \
             patch("os.path.exists", side_effect=lambda p: str(p) == udev_rule), \
             patch("os.remove", return_value=None), \
             patch.object(Path, "exists", return_value=False), \
             patch("trcc.legacy.ui.cli._system.subprocess.run",
                   side_effect=lambda cmd, **kw: calls.append(cmd) or completed_process(0)), \
             patch("trcc.legacy.ui.cli._system._is_externally_managed", return_value=False):
            uninstall(yes=True)

        udevadm_calls = [c for c in calls if "udevadm" in c]
        assert len(udevadm_calls) >= 1

    def test_calls_clear_installed_resolutions(self, completed_process, tmp_config):
        from trcc.legacy.conf import save_config
        save_config({"install_info": {"method": "pip", "distro": "ubuntu"}})
        home = tmp_config / "home"
        home.mkdir()

        with patch("trcc.legacy.adapters.system.linux_platform._real_user_home", return_value=home), \
             patch("os.geteuid", return_value=0), \
             patch("os.path.exists", return_value=False), \
             patch("trcc.legacy.adapters.system.linux_platform.subprocess.run", return_value=completed_process(0)), \
             patch("trcc.legacy.conf.Settings.clear_installed_resolutions") as mock_clear, \
             patch("trcc.legacy.ui.cli._system._is_externally_managed", return_value=False):
            uninstall(yes=True)
        mock_clear.assert_called_once()

    # --- Install method detection & PEP 668 ---
    #
    # These tests exercise the real detection code paths:
    # - Real config.json on disk (via tmp_config fixture)
    # - Real EXTERNALLY-MANAGED marker file (via fake_stdlib fixture)
    # - Real file deletion for stale binaries
    # Only subprocess.run is mocked (can't run real pip/pacman in tests).

    def test_pacman_install_prints_instructions(self, completed_process, tmp_config, capsys):
        """System package installs print package manager command, not pip."""
        from trcc.legacy.conf import save_config
        save_config({"install_info": {"method": "pacman", "distro": "cachyos"}})
        home = tmp_config / "home"
        home.mkdir()

        with patch("trcc.legacy.adapters.system.linux_platform._real_user_home", return_value=home), \
             patch("os.geteuid", return_value=0), \
             patch("os.path.exists", return_value=False), \
             patch("trcc.legacy.adapters.system.linux_platform.subprocess.run", return_value=completed_process(0)):
            uninstall(yes=True)

        out = capsys.readouterr().out
        assert "sudo pacman -R trcc-linux" in out

    def test_dnf_install_prints_instructions(self, completed_process, tmp_config, capsys):
        from trcc.legacy.conf import save_config
        save_config({"install_info": {"method": "dnf", "distro": "fedora"}})
        home = tmp_config / "home"
        home.mkdir()

        with patch("trcc.legacy.adapters.system.linux_platform._real_user_home", return_value=home), \
             patch("os.geteuid", return_value=0), \
             patch("os.path.exists", return_value=False), \
             patch("trcc.legacy.adapters.system.linux_platform.subprocess.run", return_value=completed_process(0)):
            uninstall(yes=True)

        out = capsys.readouterr().out
        assert "sudo dnf remove trcc-linux" in out

    def test_pipx_install_uses_pipx_uninstall(self, completed_process, tmp_config):
        from trcc.legacy.conf import save_config
        save_config({"install_info": {"method": "pipx", "distro": "arch"}})
        home = tmp_config / "home"
        home.mkdir()
        calls = []

        with patch("trcc.legacy.adapters.system.linux_platform._real_user_home", return_value=home), \
             patch("os.geteuid", return_value=0), \
             patch("os.path.exists", return_value=False), \
             patch("trcc.legacy.adapters.system.linux_platform.subprocess.run",
                   side_effect=lambda cmd, **kw: calls.append(cmd) or completed_process(0)):
            uninstall(yes=True)

        assert ["pipx", "uninstall", "trcc-linux"] in calls

    def test_pip_adds_break_system_packages_on_pep668(self, completed_process, tmp_config):
        """PEP 668 distros get --break-system-packages in the actual pip command."""
        from trcc.legacy.conf import save_config
        save_config({"install_info": {"method": "pip", "distro": "arch"}})
        home = tmp_config / "home"
        home.mkdir()
        calls = []

        # _is_externally_managed is tested separately in TestIsExternallyManaged
        with patch("trcc.legacy.adapters.system.linux_platform._real_user_home", return_value=home), \
             patch("os.geteuid", return_value=0), \
             patch("os.path.exists", return_value=False), \
             patch("trcc.legacy.adapters.system.linux_platform.subprocess.run",
                   side_effect=lambda cmd, **kw: calls.append(cmd) or completed_process(0)), \
             patch("trcc.legacy.ui.cli._system._is_externally_managed", return_value=True):
            uninstall(yes=True)

        pip_calls = [c for c in calls if "pip" in c and "uninstall" in c]
        assert len(pip_calls) == 1
        assert "--break-system-packages" in pip_calls[0]

    def test_pip_no_break_system_packages_without_marker(self, completed_process, tmp_config):
        """No EXTERNALLY-MANAGED marker = no --break-system-packages flag."""
        from trcc.legacy.conf import save_config
        save_config({"install_info": {"method": "pip", "distro": "ubuntu"}})
        home = tmp_config / "home"
        home.mkdir()
        calls = []

        with patch("trcc.legacy.adapters.system.linux_platform._real_user_home", return_value=home), \
             patch("os.geteuid", return_value=0), \
             patch("os.path.exists", return_value=False), \
             patch("trcc.legacy.adapters.system.linux_platform.subprocess.run",
                   side_effect=lambda cmd, **kw: calls.append(cmd) or completed_process(0)), \
             patch("trcc.legacy.ui.cli._system._is_externally_managed", return_value=False):
            uninstall(yes=True)

        pip_calls = [c for c in calls if "pip" in c and "uninstall" in c]
        assert len(pip_calls) == 1
        assert "--break-system-packages" not in pip_calls[0]

    def test_stale_shadow_binary_removed(self, completed_process, tmp_config):
        """Old ~/.local/bin/trcc from pip/pipx gets cleaned up on real filesystem."""
        from trcc.legacy.conf import save_config
        save_config({"install_info": {"method": "pacman", "distro": "cachyos"}})
        home = tmp_config / "home"
        home.mkdir()
        stale = home / ".local" / "bin" / "trcc"
        stale.parent.mkdir(parents=True)
        stale.write_text("#!/usr/bin/env python3\n# old pip entry point")

        import os as _os
        real_exists = _os.path.exists

        with patch("trcc.legacy.adapters.system.linux_platform._real_user_home", return_value=home), \
             patch("trcc.legacy.ui.cli._system.Path.home", return_value=home), \
             patch("os.geteuid", return_value=0), \
             patch("os.path.exists",
                   side_effect=lambda p: False if str(p).startswith(("/etc", "/usr")) else real_exists(p)), \
             patch("trcc.legacy.adapters.system.linux_platform.subprocess.run", return_value=completed_process(0)):
            uninstall(yes=True)

        assert not stale.exists()


# ===========================================================================
# TestDetectInstallMethod — unit tests for _detect_install_method
# ===========================================================================

class TestDetectInstallMethod:
    """detect_install_method — exercises each detection branch."""

    def test_pipx_prefix(self):
        """Detects pipx from sys.prefix path."""
        from trcc.legacy.core.platform import detect_install_method
        with patch("trcc.legacy.core.platform.sys.prefix",
                   "/home/user/.local/pipx/venvs/trcc-linux"):
            assert detect_install_method() == "pipx"

    def test_pip_from_metadata(self):
        """Reads INSTALLER file from package metadata."""
        from trcc.legacy.core.platform import detect_install_method
        mock_dist = MagicMock()
        mock_dist.read_text.return_value = "pip\n"
        with patch("trcc.legacy.core.platform.sys.prefix", "/usr"), \
             patch("importlib.metadata.distribution", return_value=mock_dist):
            assert detect_install_method() == "pip"

    def test_falls_back_to_package_manager(self):
        """Falls back to whichever system package manager exists."""
        from importlib.metadata import PackageNotFoundError

        from trcc.legacy.core.platform import detect_install_method
        with patch("trcc.legacy.core.platform.sys.prefix", "/usr"), \
             patch("importlib.metadata.distribution",
                   side_effect=PackageNotFoundError("trcc-linux")), \
             patch("trcc.legacy.core.platform.shutil.which",
                   side_effect=lambda cmd: "/usr/bin/dnf" if cmd == "dnf" else None):
            assert detect_install_method() == "dnf"


# ===========================================================================
# TestIsExternallyManaged — unit tests for PEP 668 marker detection
# ===========================================================================

class TestIsExternallyManaged:
    """_is_externally_managed — checks real EXTERNALLY-MANAGED file on disk."""

    def test_marker_present(self, tmp_path):
        """Returns True when EXTERNALLY-MANAGED exists in stdlib dir."""
        import os as real_os

        from trcc.legacy.ui.cli._system import _is_externally_managed
        fake_stdlib = tmp_path / "lib" / "python3.14"
        fake_stdlib.mkdir(parents=True)
        (fake_stdlib / "EXTERNALLY-MANAGED").write_text(
            "[externally-managed]\nError=This is managed by pacman\n"
        )
        original = real_os.__file__
        try:
            real_os.__file__ = str(fake_stdlib / "os.py")
            assert _is_externally_managed() is True
        finally:
            real_os.__file__ = original

    def test_marker_absent(self, tmp_path):
        """Returns False when no EXTERNALLY-MANAGED in stdlib dir."""
        import os as real_os

        from trcc.legacy.ui.cli._system import _is_externally_managed
        fake_stdlib = tmp_path / "lib" / "python3.14"
        fake_stdlib.mkdir(parents=True)
        original = real_os.__file__
        try:
            real_os.__file__ = str(fake_stdlib / "os.py")
            assert _is_externally_managed() is False
        finally:
            real_os.__file__ = original


# ===========================================================================
# TestReport
# ===========================================================================

class TestReport:
    """report — generates and prints diagnostic report."""

    def test_returns_zero(self):
        mock_report = MagicMock()
        mock_report.collect.return_value = None
        mock_report.__str__ = lambda self: "REPORT_OUTPUT"

        with patch("trcc.legacy.adapters.infra.debug_report.DebugReport", return_value=mock_report):
            rc = report()
        assert rc == 0

    def test_calls_collect(self):
        mock_report = MagicMock()
        mock_report.__str__ = lambda self: "REPORT"

        with patch("trcc.legacy.adapters.infra.debug_report.DebugReport", return_value=mock_report):
            report()
        mock_report.collect.assert_called_once()

    def test_prints_report(self, capsys):
        mock_report = MagicMock()
        mock_report.collect.return_value = None
        mock_report.__str__ = lambda self: "DIAGNOSTIC_OUTPUT_HERE"

        with patch("trcc.legacy.adapters.infra.debug_report.DebugReport", return_value=mock_report), \
             patch("trcc.legacy.adapters.infra.doctor.run_doctor", return_value=0):
            report()

        out = capsys.readouterr().out
        assert "DIAGNOSTIC_OUTPUT_HERE" in out

    def test_calls_run_doctor(self):
        mock_report = MagicMock()
        mock_report.__str__ = lambda self: "REPORT"

        with patch("trcc.legacy.adapters.infra.debug_report.DebugReport", return_value=mock_report), \
             patch("trcc.legacy.adapters.infra.doctor.run_doctor", return_value=0) as mock_doctor:
            report()
        mock_doctor.assert_called_once()

    def test_prints_github_url(self, capsys):
        mock_report = MagicMock()
        mock_report.__str__ = lambda self: "REPORT"

        with patch("trcc.legacy.adapters.infra.debug_report.DebugReport", return_value=mock_report), \
             patch("trcc.legacy.adapters.infra.doctor.run_doctor", return_value=0):
            report()

        out = capsys.readouterr().out
        assert "https://github.com/Lexonight1/thermalright-trcc-linux/issues/new" in out


# ===========================================================================
# TestDownloadThemes
# ===========================================================================

class TestDownloadThemes:
    """download_themes — calls trcc().download_themes() directly."""

    def test_no_pack_calls_download(self):
        """pack=None calls download_themes('', False)."""
        from trcc import _boot
        mock_t = MagicMock()
        mock_t.download_themes.return_value = 0
        with patch.object(_boot, "_cached", mock_t):
            rc = download_themes(pack=None)
        mock_t.download_themes.assert_called_with("", False)
        assert rc == 0

    def test_show_list_calls_download_with_empty_pack(self):
        """show_list=True calls download_themes('', False)."""
        from trcc import _boot
        mock_t = MagicMock()
        mock_t.download_themes.return_value = 0
        with patch.object(_boot, "_cached", mock_t):
            rc = download_themes(pack="themes-320x320", show_list=True)
        mock_t.download_themes.assert_called_with("", False)
        assert rc == 0

    def test_show_info_calls_pack_info_directly(self):
        """show_info=True calls show_info() directly (no download needed)."""
        with patch("trcc.legacy.adapters.infra.theme_downloader.show_info") as mock_info:
            rc = download_themes(pack="themes-320x320", show_info=True)
        mock_info.assert_called_once_with("themes-320x320")
        assert rc == 0

    def test_force_clears_installed_resolutions(self):
        """force=True clears resolution cache before downloading."""
        from trcc import _boot
        mock_t = MagicMock()
        mock_t.download_themes.return_value = 0
        with patch("trcc.legacy.conf.Settings.clear_installed_resolutions") as mock_clear, \
             patch.object(_boot, "_cached", mock_t):
            download_themes(pack="themes-320x320", force=True)
        mock_clear.assert_called_once()

    def test_normal_download_calls_with_pack(self):
        """Normal download calls download_themes with the pack name."""
        from trcc import _boot
        mock_t = MagicMock()
        mock_t.download_themes.return_value = 0
        with patch.object(_boot, "_cached", mock_t):
            download_themes(pack="themes-320x320")
        mock_t.download_themes.assert_called_with("themes-320x320", False)

    def test_force_passes_force_flag(self):
        """force=True is passed through to download_themes."""
        from trcc import _boot
        mock_t = MagicMock()
        mock_t.download_themes.return_value = 0
        with patch("trcc.legacy.conf.Settings.clear_installed_resolutions"), \
             patch.object(_boot, "_cached", mock_t):
            download_themes(pack="themes-320x320", force=True)
        mock_t.download_themes.assert_called_with("themes-320x320", True)

    def test_exception_returns_one(self, capsys):
        """Exceptions propagate as rc=1 with error message."""
        from trcc import _boot
        mock_t = MagicMock()
        mock_t.download_themes.side_effect = RuntimeError("network error")
        with patch.object(_boot, "_cached", mock_t):
            rc = download_themes(pack=None)
        assert rc == 1
        out = capsys.readouterr().out
        assert "Error" in out


# ===========================================================================
# TestConfirm
# ===========================================================================

class TestConfirm:
    """_confirm — auto_yes, y, yes, empty, n, EOFError, KeyboardInterrupt."""

    def test_auto_yes_returns_true_without_input(self):
        result = _confirm("Install?", auto_yes=True)
        assert result is True

    def test_auto_yes_prints_y_auto(self, capsys):
        _confirm("Install?", auto_yes=True)
        out = capsys.readouterr().out
        assert "y (auto)" in out or "auto" in out

    def test_input_y_returns_true(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda _: "y")
        assert _confirm("Install?", auto_yes=False) is True

    def test_input_yes_returns_true(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda _: "yes")
        assert _confirm("Install?", auto_yes=False) is True

    def test_input_Y_returns_true(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda _: "Y")
        assert _confirm("Install?", auto_yes=False) is True

    def test_input_empty_returns_true(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda _: "")
        assert _confirm("Install?", auto_yes=False) is True

    def test_input_n_returns_false(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda _: "n")
        assert _confirm("Install?", auto_yes=False) is False

    def test_input_no_returns_false(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda _: "no")
        assert _confirm("Install?", auto_yes=False) is False

    def test_eoferror_returns_false(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda _: (_ for _ in ()).throw(EOFError()))
        result = _confirm("Install?", auto_yes=False)
        assert result is False

    def test_keyboardinterrupt_returns_false(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda _: (_ for _ in ()).throw(KeyboardInterrupt()))
        result = _confirm("Install?", auto_yes=False)
        assert result is False

    def test_eoferror_prints_newline(self, monkeypatch, capsys):
        monkeypatch.setattr("builtins.input", lambda _: (_ for _ in ()).throw(EOFError()))
        _confirm("Install?", auto_yes=False)
        out = capsys.readouterr().out
        assert "\n" in out or out == "\n"


# ===========================================================================
# TestRunSetup
# ===========================================================================

class TestRunSetup:
    """run_setup — interactive setup wizard."""

    @pytest.fixture(autouse=True)
    def _wire_setup_platform(self, _mock_builder):
        """Phase 9: run_setup() now calls trcc().os.run_setup(auto_yes=...).

        Replace the cached Trcc's platform with a MagicMock whose ``run_setup``
        delegates to the per-OS check chain via the existing builder.os.
        """
        from trcc import _boot
        # _real_trcc_empty has built a Trcc against MagicMock platform
        # already; we replace that platform with one whose run_setup hits
        # the diagnostics check chain via the mock builder.
        cached = _boot._cached
        assert cached is not None

        def _run_setup(auto_yes=False):
            setup = _mock_builder.os
            return setup.run(auto_yes=auto_yes)

        cached._platform.run_setup = _run_setup  # type: ignore[attr-defined]

    def _make_dep(self, name="pkg", ok=True, required=True,
                  version="1.0", note="", install_cmd=""):
        from trcc.legacy.adapters.infra.diagnostics import DepResult
        return DepResult(
            name=name, ok=ok, required=required,
            version=version, note=note, install_cmd=install_cmd,
        )

    def _make_gpu(self, vendor="nvidia", label="NVIDIA", installed=True, install_cmd=""):
        from trcc.legacy.adapters.infra.diagnostics import GpuResult
        return GpuResult(
            vendor=vendor, label=label,
            package_installed=installed, install_cmd=install_cmd,
        )

    def _make_udev(self, ok=True, message="Rules installed"):
        from trcc.legacy.adapters.infra.diagnostics import UdevResult
        return UdevResult(ok=ok, message=message)

    def _make_selinux(self, enforcing=False, ok=True, message="Not enforcing"):
        from trcc.legacy.adapters.infra.diagnostics import SelinuxResult
        return SelinuxResult(enforcing=enforcing, ok=ok, message=message)

    def _make_rapl(self, applicable=False, ok=True, message="No RAPL"):
        from trcc.legacy.adapters.infra.diagnostics import RaplResult
        return RaplResult(applicable=applicable, ok=ok, message=message)

    def _make_polkit(self, ok=True, message="Polkit installed"):
        from trcc.legacy.adapters.infra.diagnostics import PolkitResult
        return PolkitResult(ok=ok, message=message)

    def _default_patches(self, completed_process):
        """Return all needed patches for a clean run_setup call."""
        from trcc.legacy.adapters.infra.diagnostics import SetupInfo
        return {
            "trcc.legacy.adapters.infra.diagnostics.get_setup_info": MagicMock(
                return_value=SetupInfo(distro="Fedora 43", pkg_manager="dnf", python_version="3.12.0")
            ),
            "trcc.legacy.adapters.infra.diagnostics.check_system_deps": MagicMock(
                return_value=[self._make_dep("Python", ok=True)]
            ),
            "trcc.legacy.adapters.infra.diagnostics.check_gpu": MagicMock(return_value=[]),
            "trcc.legacy.adapters.infra.diagnostics.check_udev": MagicMock(
                return_value=self._make_udev(ok=True)
            ),
            "trcc.legacy.adapters.infra.diagnostics.check_rapl": MagicMock(
                return_value=self._make_rapl(applicable=False)
            ),
            "trcc.legacy.adapters.infra.diagnostics.check_selinux": MagicMock(
                return_value=self._make_selinux(enforcing=False)
            ),
            "trcc.legacy.adapters.infra.diagnostics.check_polkit": MagicMock(
                return_value=self._make_polkit(ok=True)
            ),
            "trcc.legacy.adapters.infra.diagnostics.check_desktop_entry": MagicMock(return_value=True),
            "trcc.legacy.adapters.system.linux_platform.subprocess.run": MagicMock(return_value=completed_process(0)),
        }

    def test_returns_zero_all_ok(self, completed_process, _mock_builder, capsys):
        from trcc.legacy.adapters.system.linux_platform import LinuxPlatform
        _mock_builder._platform = LinuxPlatform()
        patches = self._default_patches(completed_process)
        with patch.multiple("trcc.legacy.adapters.infra.doctor", **{
            k.replace("trcc.legacy.adapters.infra.diagnostics.", ""): v
            for k, v in patches.items()
            if k.startswith("trcc.legacy.adapters.infra.diagnostics.")
        }), patch("trcc.legacy.adapters.system.linux_platform.subprocess.run", return_value=completed_process(0)):
            rc = run_setup(auto_yes=True)
        assert rc == 0

    def test_prints_distro_name(self, completed_process, _mock_builder, capsys):
        # Patch distro_name to a synthetic value — the test asserts the
        # wizard prints whatever distro_name returns, not that the test
        # runner happens to be on Fedora. (Pre-skylar this hardcoded
        # "Fedora", which broke on NixOS / Arch / Pop!_OS / etc. — see
        # PR #123 reporter note.)
        from trcc.legacy.adapters.system.linux_platform import LinuxPlatform
        _mock_builder._platform = LinuxPlatform()
        patches = self._default_patches(completed_process)
        with patch.multiple("trcc.legacy.adapters.infra.doctor", **{
            k.replace("trcc.legacy.adapters.infra.diagnostics.", ""): v
            for k, v in patches.items()
            if k.startswith("trcc.legacy.adapters.infra.diagnostics.")
        }), patch("trcc.legacy.adapters.system.linux_platform.subprocess.run", return_value=completed_process(0)), \
             patch.object(LinuxPlatform, 'distro_name', return_value="TestDistro 99"):
            run_setup(auto_yes=True)
        out = capsys.readouterr().out
        assert "TestDistro 99" in out

    def test_prints_six_steps(self, completed_process, _mock_builder, capsys):
        from trcc.legacy.adapters.system.linux_platform import LinuxPlatform
        _mock_builder._platform = LinuxPlatform()
        patches = self._default_patches(completed_process)
        with patch.multiple("trcc.legacy.adapters.infra.doctor", **{
            k.replace("trcc.legacy.adapters.infra.diagnostics.", ""): v
            for k, v in patches.items()
            if k.startswith("trcc.legacy.adapters.infra.diagnostics.")
        }), patch("trcc.legacy.adapters.system.linux_platform.subprocess.run", return_value=completed_process(0)):
            run_setup(auto_yes=True)
        out = capsys.readouterr().out
        assert "1/6" in out
        assert "2/6" in out
        assert "3/6" in out
        assert "5/6" in out
        assert "6/6" in out

    def test_nothing_to_do_when_all_ok(self, completed_process, _mock_builder, capsys):
        from trcc.legacy.adapters.system.linux_platform import LinuxPlatform
        _mock_builder._platform = LinuxPlatform()
        patches = self._default_patches(completed_process)
        with patch.multiple("trcc.legacy.adapters.infra.doctor", **{
            k.replace("trcc.legacy.adapters.infra.diagnostics.", ""): v
            for k, v in patches.items()
            if k.startswith("trcc.legacy.adapters.infra.diagnostics.")
        }), patch("trcc.legacy.adapters.system.linux_platform.subprocess.run", return_value=completed_process(0)):
            run_setup(auto_yes=True)
        out = capsys.readouterr().out
        assert "Nothing to do" in out

    def test_missing_required_dep_offers_install(self, completed_process, _mock_builder, capsys, monkeypatch):
        from trcc.legacy.adapters.infra.diagnostics import DepResult, SetupInfo
        from trcc.legacy.adapters.system.linux_platform import LinuxPlatform
        _mock_builder._platform = LinuxPlatform()
        monkeypatch.setattr("builtins.input", lambda _: "n")

        with patch("trcc.legacy.adapters.infra.doctor.get_setup_info",
                   return_value=SetupInfo("Fedora", "dnf", "3.12")), \
             patch("trcc.legacy.adapters.infra.doctor.check_system_deps",
                   return_value=[DepResult("sg_raw", ok=False, required=True,
                                           install_cmd="sudo dnf install sg3_utils")]), \
             patch("trcc.legacy.adapters.infra.doctor.check_gpu", return_value=[]), \
             patch("trcc.legacy.adapters.infra.doctor.check_udev",
                   return_value=self._make_udev(ok=True)), \
             patch("trcc.legacy.adapters.infra.doctor.check_rapl",
                   return_value=self._make_rapl(applicable=False)), \
             patch("trcc.legacy.adapters.infra.doctor.check_selinux",
                   return_value=self._make_selinux(enforcing=False)), \
             patch("trcc.legacy.adapters.infra.doctor.check_polkit",
                   return_value=self._make_polkit(ok=True)), \
             patch("trcc.legacy.adapters.infra.doctor.check_desktop_entry", return_value=True), \
             patch("trcc.legacy.adapters.system.linux_platform.subprocess.run", return_value=completed_process(0)):
            run_setup(auto_yes=False)
        out = capsys.readouterr().out
        assert "MISSING" in out or "sg_raw" in out

    def test_auto_yes_installs_missing_required_dep(self, completed_process, _mock_builder, capsys):
        from trcc.legacy.adapters.infra.diagnostics import DepResult, SetupInfo
        from trcc.legacy.adapters.system.linux_platform import LinuxPlatform
        _mock_builder._platform = LinuxPlatform()
        calls = []

        with patch("trcc.legacy.adapters.infra.doctor.get_setup_info",
                   return_value=SetupInfo("Fedora", "dnf", "3.12")), \
             patch("trcc.legacy.adapters.infra.doctor.check_system_deps",
                   return_value=[DepResult("sg_raw", ok=False, required=True,
                                           install_cmd="sudo dnf install sg3_utils")]), \
             patch("trcc.legacy.adapters.infra.doctor.check_gpu", return_value=[]), \
             patch("trcc.legacy.adapters.infra.doctor.check_udev",
                   return_value=self._make_udev(ok=True)), \
             patch("trcc.legacy.adapters.infra.doctor.check_rapl",
                   return_value=self._make_rapl(applicable=False)), \
             patch("trcc.legacy.adapters.infra.doctor.check_selinux",
                   return_value=self._make_selinux(enforcing=False)), \
             patch("trcc.legacy.adapters.infra.doctor.check_polkit",
                   return_value=self._make_polkit(ok=True)), \
             patch("trcc.legacy.adapters.infra.doctor.check_desktop_entry", return_value=True), \
             patch("trcc.legacy.adapters.system.linux_platform.subprocess.run",
                   side_effect=lambda cmd, **kw: calls.append(cmd) or completed_process(0)):
            run_setup(auto_yes=True)

        install_calls = [c for c in calls if "dnf" in c or "sg3" in c]
        assert len(install_calls) >= 1

    def test_udev_not_ok_offers_install(self, completed_process, _mock_builder, capsys, monkeypatch):
        from trcc.legacy.adapters.infra.diagnostics import SetupInfo
        from trcc.legacy.adapters.system.linux_platform import LinuxPlatform
        _mock_builder._platform = LinuxPlatform()
        monkeypatch.setattr("builtins.input", lambda _: "n")

        with patch("trcc.legacy.adapters.infra.doctor.get_setup_info",
                   return_value=SetupInfo("Fedora", "dnf", "3.12")), \
             patch("trcc.legacy.adapters.infra.doctor.check_system_deps",
                   return_value=[self._make_dep()]), \
             patch("trcc.legacy.adapters.infra.doctor.check_gpu", return_value=[]), \
             patch("trcc.legacy.adapters.infra.doctor.check_udev",
                   return_value=self._make_udev(ok=False, message="Rules missing")), \
             patch("trcc.legacy.adapters.infra.doctor.check_rapl",
                   return_value=self._make_rapl(applicable=False)), \
             patch("trcc.legacy.adapters.infra.doctor.check_selinux",
                   return_value=self._make_selinux(enforcing=False)), \
             patch("trcc.legacy.adapters.infra.doctor.check_polkit",
                   return_value=self._make_polkit(ok=True)), \
             patch("trcc.legacy.adapters.infra.doctor.check_desktop_entry", return_value=True), \
             patch("trcc.legacy.adapters.system.linux_platform.subprocess.run", return_value=completed_process(0)):
            run_setup(auto_yes=False)
        out = capsys.readouterr().out
        assert "Rules missing" in out or "udev" in out.lower()

    def test_selinux_enforcing_shows_step_4(self, completed_process, _mock_builder, capsys):
        from trcc.legacy.adapters.infra.diagnostics import SetupInfo
        from trcc.legacy.adapters.system.linux_platform import LinuxPlatform
        _mock_builder._platform = LinuxPlatform()

        with patch("trcc.legacy.adapters.infra.doctor.get_setup_info",
                   return_value=SetupInfo("Bazzite", "rpm-ostree", "3.12")), \
             patch("trcc.legacy.adapters.infra.doctor.check_system_deps",
                   return_value=[self._make_dep()]), \
             patch("trcc.legacy.adapters.infra.doctor.check_gpu", return_value=[]), \
             patch("trcc.legacy.adapters.infra.doctor.check_udev",
                   return_value=self._make_udev(ok=True)), \
             patch("trcc.legacy.adapters.infra.doctor.check_rapl",
                   return_value=self._make_rapl(applicable=False)), \
             patch("trcc.legacy.adapters.infra.doctor.check_selinux",
                   return_value=self._make_selinux(enforcing=True, ok=True, message="Policy loaded")), \
             patch("trcc.legacy.adapters.infra.doctor.check_polkit",
                   return_value=self._make_polkit(ok=True)), \
             patch("trcc.legacy.adapters.infra.doctor.check_desktop_entry", return_value=True), \
             patch("trcc.legacy.adapters.system.linux_platform.subprocess.run", return_value=completed_process(0)):
            run_setup(auto_yes=True)
        out = capsys.readouterr().out
        assert "4/6" in out or "SELinux" in out

    def test_selinux_not_enforcing_skips_step_4(self, completed_process, _mock_builder, capsys):
        from trcc.legacy.adapters.infra.diagnostics import SetupInfo
        from trcc.legacy.adapters.system.linux_platform import LinuxPlatform
        _mock_builder._platform = LinuxPlatform()

        with patch("trcc.legacy.adapters.infra.doctor.get_setup_info",
                   return_value=SetupInfo("Ubuntu", "apt", "3.12")), \
             patch("trcc.legacy.adapters.infra.doctor.check_system_deps",
                   return_value=[self._make_dep()]), \
             patch("trcc.legacy.adapters.infra.doctor.check_gpu", return_value=[]), \
             patch("trcc.legacy.adapters.infra.doctor.check_udev",
                   return_value=self._make_udev(ok=True)), \
             patch("trcc.legacy.adapters.infra.doctor.check_rapl",
                   return_value=self._make_rapl(applicable=False)), \
             patch("trcc.legacy.adapters.infra.doctor.check_selinux",
                   return_value=self._make_selinux(enforcing=False)), \
             patch("trcc.legacy.adapters.infra.doctor.check_polkit",
                   return_value=self._make_polkit(ok=True)), \
             patch("trcc.legacy.adapters.infra.doctor.check_desktop_entry", return_value=True), \
             patch("trcc.legacy.adapters.system.linux_platform.subprocess.run", return_value=completed_process(0)):
            run_setup(auto_yes=True)
        out = capsys.readouterr().out
        # Step 4 header only shown when enforcing
        assert "SELinux policy" not in out

    def test_summary_lists_installed_actions(self, completed_process, _mock_builder, capsys):
        from trcc.legacy.adapters.infra.diagnostics import SetupInfo
        from trcc.legacy.adapters.system.linux_platform import LinuxPlatform
        _mock_builder._platform = LinuxPlatform()

        with patch("trcc.legacy.adapters.infra.doctor.get_setup_info",
                   return_value=SetupInfo("Fedora", "dnf", "3.12")), \
             patch("trcc.legacy.adapters.infra.doctor.check_system_deps",
                   return_value=[self._make_dep()]), \
             patch("trcc.legacy.adapters.infra.doctor.check_gpu", return_value=[]), \
             patch("trcc.legacy.adapters.infra.doctor.check_udev",
                   return_value=self._make_udev(ok=False, message="Missing")), \
             patch("trcc.legacy.adapters.infra.doctor.check_rapl",
                   return_value=self._make_rapl(applicable=False)), \
             patch("trcc.legacy.adapters.infra.doctor.check_selinux",
                   return_value=self._make_selinux(enforcing=False)), \
             patch("trcc.legacy.adapters.infra.doctor.check_polkit",
                   return_value=self._make_polkit(ok=True)), \
             patch("trcc.legacy.adapters.infra.doctor.check_desktop_entry", return_value=True), \
             patch("trcc.legacy.adapters.system.linux_platform.setup_udev", return_value=0), \
             patch("trcc.legacy.adapters.system.linux_platform.subprocess.run", return_value=completed_process(0)):
            run_setup(auto_yes=True)
        out = capsys.readouterr().out
        assert "Summary" in out

    def test_desktop_not_installed_offers_install(self, completed_process, _mock_builder, capsys, monkeypatch):
        from trcc.legacy.adapters.infra.diagnostics import SetupInfo
        from trcc.legacy.adapters.system.linux_platform import LinuxPlatform
        _mock_builder._platform = LinuxPlatform()
        monkeypatch.setattr("builtins.input", lambda _: "n")

        with patch("trcc.legacy.adapters.infra.doctor.get_setup_info",
                   return_value=SetupInfo("Fedora", "dnf", "3.12")), \
             patch("trcc.legacy.adapters.infra.doctor.check_system_deps",
                   return_value=[self._make_dep()]), \
             patch("trcc.legacy.adapters.infra.doctor.check_gpu", return_value=[]), \
             patch("trcc.legacy.adapters.infra.doctor.check_udev",
                   return_value=self._make_udev(ok=True)), \
             patch("trcc.legacy.adapters.infra.doctor.check_rapl",
                   return_value=self._make_rapl(applicable=False)), \
             patch("trcc.legacy.adapters.infra.doctor.check_selinux",
                   return_value=self._make_selinux(enforcing=False)), \
             patch("trcc.legacy.adapters.infra.doctor.check_polkit",
                   return_value=self._make_polkit(ok=True)), \
             patch("trcc.legacy.adapters.infra.doctor.check_desktop_entry", return_value=False), \
             patch("trcc.legacy.adapters.system.linux_platform.subprocess.run", return_value=completed_process(0)):
            run_setup(auto_yes=False)
        out = capsys.readouterr().out
        assert "No application menu entry" in out or "desktop" in out.lower()

    def test_prints_run_trcc_gui_message(self, completed_process, _mock_builder, capsys):
        from trcc.legacy.adapters.system.linux_platform import LinuxPlatform
        _mock_builder._platform = LinuxPlatform()
        patches = self._default_patches(completed_process)
        with patch.multiple("trcc.legacy.adapters.infra.doctor", **{
            k.replace("trcc.legacy.adapters.infra.diagnostics.", ""): v
            for k, v in patches.items()
            if k.startswith("trcc.legacy.adapters.infra.diagnostics.")
        }), patch("trcc.legacy.adapters.system.linux_platform.subprocess.run", return_value=completed_process(0)):
            run_setup(auto_yes=True)
        out = capsys.readouterr().out
        assert "trcc gui" in out


# ===========================================================================
# TestReportDiagnosticOutput — integration tests with real DebugReport
# ===========================================================================

class TestReportDiagnosticOutput:
    """Test report() output with mocked system state, not mocked report objects.

    These verify that real user scenarios produce the right diagnostic hints
    in the combined report + doctor output.
    """

    # Shared patches that block all external I/O (subprocess, device detection,
    # file reads to system paths, handshakes, etc.)
    @staticmethod
    def _base_patches():
        """Context managers that isolate DebugReport from the real system."""
        def _mock_cp(stdout: str = "") -> MagicMock:
            m = MagicMock()
            m.returncode = 0
            m.stdout = stdout
            m.stderr = ""
            return m

        return {
            # Block subprocess calls (lsusb, ps, getenforce)
            "sub": patch(
                "trcc.legacy.adapters.infra.diagnostics.subprocess.run",
                return_value=_mock_cp(stdout=""),
            ),
            # detect_fn injected directly into report() — no patch needed here

            # Block config loading
            "conf": patch(
                "trcc.legacy.conf.load_config",
                return_value={},
            ),
            # Block doctor's subprocess calls
            "doc_sub": patch(
                "trcc.legacy.adapters.infra.diagnostics.subprocess.run",
                return_value=_mock_cp(stdout=""),
            ),
            # Block log file read
            "log_path": patch(
                "pathlib.Path.exists",
                return_value=False,
            ),
        }

    def test_udev_rules_missing_shows_not_installed(self, completed_process, capsys, tmp_path):
        """When udev rules file doesn't exist, output says NOT INSTALLED."""
        with patch("trcc.legacy.adapters.infra.diagnostics._UDEV_PATH",
                   str(tmp_path / "nonexistent")), \
             patch("trcc.legacy.adapters.infra.diagnostics.subprocess.run",
                   return_value=completed_process(0, stdout="")), \
             patch("trcc.legacy.conf.load_config", return_value={}), \
             patch("trcc.legacy.adapters.infra.diagnostics.os.listdir",
                   return_value=[]), \
             patch("trcc.legacy.adapters.infra.doctor.run_doctor", return_value=1):
            report(detect_fn=lambda: [])

        out = capsys.readouterr().out
        assert "NOT INSTALLED" in out
        assert "setup-udev" in out

    def test_udev_rules_exist_shows_rules(self, completed_process, capsys, tmp_path):
        """When udev rules file exists, output shows the rules."""
        rules_file = tmp_path / "99-trcc-lcd.rules"
        rules_file.write_text(
            '# Thermalright\n'
            'SUBSYSTEM=="hidraw", ATTRS{idVendor}=="0416", '
            'ATTRS{idProduct}=="5302", MODE="0666"\n'
        )

        with patch("trcc.legacy.adapters.infra.diagnostics._UDEV_PATH",
                   str(rules_file)), \
             patch("trcc.legacy.adapters.infra.diagnostics.subprocess.run",
                   return_value=completed_process(0, stdout="")), \
             patch("trcc.legacy.conf.load_config", return_value={}), \
             patch("trcc.legacy.adapters.infra.diagnostics.os.listdir",
                   return_value=[]), \
             patch("trcc.legacy.adapters.infra.doctor.run_doctor", return_value=0):
            report(detect_fn=lambda: [])

        out = capsys.readouterr().out
        assert "0416" in out
        assert "5302" in out
        assert "NOT INSTALLED" not in out

    def test_no_sg_devices_shows_message(self, completed_process, capsys, tmp_path):
        """When no /dev/sg* devices exist, output says so."""
        with patch("trcc.legacy.adapters.infra.diagnostics._UDEV_PATH",
                   str(tmp_path / "nonexistent")), \
             patch("trcc.legacy.adapters.infra.diagnostics.subprocess.run",
                   return_value=completed_process(0, stdout="")), \
             patch("trcc.legacy.conf.load_config", return_value={}), \
             patch("trcc.legacy.adapters.infra.diagnostics.os.listdir",
                   return_value=["tty0", "null", "zero"]), \
             patch("trcc.legacy.adapters.infra.doctor.run_doctor", return_value=0):
            report(detect_fn=lambda: [])

        out = capsys.readouterr().out
        assert "/dev/sg*: (none found)" in out

    def test_sg_device_no_access_shows_no_access(self, completed_process, capsys, tmp_path):
        """When /dev/sg* exists but isn't accessible, output says NO ACCESS."""
        with patch("trcc.legacy.adapters.infra.diagnostics._UDEV_PATH",
                   str(tmp_path / "nonexistent")), \
             patch("trcc.legacy.adapters.infra.diagnostics.subprocess.run",
                   return_value=completed_process(0, stdout="")), \
             patch("trcc.legacy.conf.load_config", return_value={}), \
             patch("trcc.legacy.adapters.infra.diagnostics.os.listdir",
                   return_value=["sg0", "sg1"]), \
             patch("trcc.legacy.adapters.infra.diagnostics.os.stat") as mock_stat, \
             patch("trcc.legacy.adapters.infra.diagnostics.os.access",
                   return_value=False), \
             patch("trcc.legacy.adapters.infra.doctor.run_doctor", return_value=0):
            mock_stat.return_value.st_mode = 0o060660  # crw-rw---- (660)
            report(detect_fn=lambda: [])

        out = capsys.readouterr().out
        assert "NO ACCESS" in out
        assert "/dev/sg0" in out

    def test_no_devices_detected(self, completed_process, capsys, tmp_path):
        """When no devices found, output says none."""
        with patch("trcc.legacy.adapters.infra.diagnostics._UDEV_PATH",
                   str(tmp_path / "nonexistent")), \
             patch("trcc.legacy.adapters.infra.diagnostics.subprocess.run",
                   return_value=completed_process(0, stdout="")), \
             patch("trcc.legacy.conf.load_config", return_value={}), \
             patch("trcc.legacy.adapters.infra.diagnostics.os.listdir",
                   return_value=[]), \
             patch("trcc.legacy.adapters.infra.doctor.run_doctor", return_value=0):
            report(detect_fn=lambda: [])

        out = capsys.readouterr().out
        assert "(none)" in out

    def test_no_devices_to_handshake(self, completed_process, capsys, tmp_path):
        """When no devices detected, handshake section says so."""
        with patch("trcc.legacy.adapters.infra.diagnostics._UDEV_PATH",
                   str(tmp_path / "nonexistent")), \
             patch("trcc.legacy.adapters.infra.diagnostics.subprocess.run",
                   return_value=completed_process(0, stdout="")), \
             patch("trcc.legacy.conf.load_config", return_value={}), \
             patch("trcc.legacy.adapters.infra.diagnostics.os.listdir",
                   return_value=[]), \
             patch("trcc.legacy.adapters.infra.doctor.run_doctor", return_value=0):
            report(detect_fn=lambda: [])

        out = capsys.readouterr().out
        assert "no devices to handshake" in out

    def test_hid_permission_denied_shows_hint(self, completed_process, capsys, tmp_path):
        """When HID handshake gets EACCES, output says run setup-udev."""
        mock_dev = MagicMock()
        mock_dev.vid = 0x0416
        mock_dev.pid = 0x5302
        mock_dev.protocol = "hid"
        mock_dev.implementation = "hid_type2"
        mock_dev.device_type = 2
        mock_dev.product_name = "USBDISPLAY"
        mock_dev.scsi_device = None
        mock_dev.usb_path = "3-005"

        # Create a USBError-like exception with errno 13 (EACCES)
        usb_err = Exception("[Errno 13] Access denied (insufficient permissions)")
        usb_err.errno = 13  # type: ignore[attr-defined]

        mock_protocol = MagicMock()
        mock_protocol.handshake.return_value = None
        mock_protocol.last_error = usb_err

        with patch("trcc.legacy.adapters.infra.diagnostics._UDEV_PATH",
                   str(tmp_path / "nonexistent")), \
             patch("trcc.legacy.adapters.infra.diagnostics.subprocess.run",
                   return_value=completed_process(0, stdout="")), \
             patch("trcc.legacy.conf.load_config", return_value={}), \
             patch("trcc.legacy.adapters.infra.diagnostics.os.listdir",
                   return_value=[]), \
             patch("trcc.legacy.adapters.device.hid_protocol.HidProtocol",
                   return_value=mock_protocol), \
             patch("trcc.legacy.adapters.infra.doctor.run_doctor", return_value=0):
            report(detect_fn=lambda: [mock_dev])

        out = capsys.readouterr().out
        assert "Permission denied" in out
        assert "setup-udev" in out

    def test_doctor_missing_udev_in_report(self, completed_process, capsys, tmp_path):
        """When doctor finds udev missing, the hint appears in report output."""
        from trcc.legacy.adapters.infra.diagnostics import UdevResult

        with patch("trcc.legacy.adapters.infra.diagnostics._UDEV_PATH",
                   str(tmp_path / "nonexistent")), \
             patch("trcc.legacy.adapters.infra.diagnostics.subprocess.run",
                   return_value=completed_process(0, stdout="")), \
             patch("trcc.legacy.conf.load_config", return_value={}), \
             patch("trcc.legacy.adapters.infra.diagnostics.os.listdir",
                   return_value=[]), \
             patch("trcc.legacy.adapters.infra.diagnostics.check_udev",
                   return_value=UdevResult(ok=False, message="udev rules not installed")), \
             patch("trcc.legacy.adapters.infra.diagnostics._detect_pkg_manager",
                   return_value="apt"), \
             patch("trcc.legacy.adapters.infra.diagnostics._read_os_release",
                   return_value={"PRETTY_NAME": "Linux Mint 22.3"}), \
             patch("trcc.legacy.adapters.infra.diagnostics._check_python_module",
                   return_value=True), \
             patch("trcc.legacy.adapters.infra.diagnostics._check_gpu_packages"), \
             patch("trcc.legacy.adapters.infra.diagnostics._check_library",
                   return_value=True), \
             patch("trcc.legacy.adapters.infra.diagnostics._check_binary",
                   return_value=True), \
             patch("trcc.legacy.adapters.infra.diagnostics.check_selinux",
                   return_value=MagicMock(enforcing=False)), \
             patch("trcc.legacy.adapters.infra.diagnostics.check_rapl",
                   return_value=MagicMock(applicable=False)), \
             patch("trcc.legacy.adapters.infra.diagnostics.check_polkit",
                   return_value=MagicMock(ok=True, message="ok")):
            report(detect_fn=lambda: [])

        out = capsys.readouterr().out
        # Both the report section AND doctor should flag udev
        assert "NOT INSTALLED" in out
        assert "setup-udev" in out

    def test_lsusb_shows_thermalright_devices(self, completed_process, capsys, tmp_path):
        """When lsusb finds Thermalright VIDs, they appear in output."""
        lsusb_output = (
            "Bus 001 Device 003: ID 0416:8001 Winbond Electronics Corp. HID Transfer\n"
            "Bus 003 Device 006: ID 0416:8041 Winbond Electronics Corp. SLV3RX_V1.6\n"
            "Bus 002 Device 001: ID 1d6b:0003 Linux Foundation USB 3.0 root hub\n"
        )

        def _fake_subprocess(cmd, **kwargs):
            # lsusb returns device list, everything else returns empty
            if cmd and cmd[0] == "lsusb":
                return completed_process(0, stdout=lsusb_output)
            return completed_process(0, stdout="")

        with patch("trcc.legacy.adapters.infra.diagnostics._UDEV_PATH",
                   str(tmp_path / "nonexistent")), \
             patch("trcc.legacy.adapters.infra.diagnostics.subprocess.run",
                   side_effect=_fake_subprocess), \
             patch("trcc.legacy.conf.load_config", return_value={}), \
             patch("trcc.legacy.adapters.infra.diagnostics.os.listdir",
                   return_value=[]), \
             patch("trcc.legacy.adapters.infra.doctor.run_doctor", return_value=0):
            report(detect_fn=lambda: [])

        out = capsys.readouterr().out
        # Thermalright VIDs shown, non-Thermalright filtered out
        assert "0416:8001" in out
        assert "0416:8041" in out
        assert "1d6b:0003" not in out

    def test_report_github_url_after_doctor(self, completed_process, capsys, tmp_path):
        """GitHub URL appears at the very end, after doctor output."""
        with patch("trcc.legacy.adapters.infra.diagnostics._UDEV_PATH",
                   str(tmp_path / "nonexistent")), \
             patch("trcc.legacy.adapters.infra.diagnostics.subprocess.run",
                   return_value=completed_process(0, stdout="")), \
             patch("trcc.legacy.conf.load_config", return_value={}), \
             patch("trcc.legacy.adapters.infra.diagnostics.os.listdir",
                   return_value=[]), \
             patch("trcc.legacy.adapters.infra.doctor.run_doctor", return_value=0):
            report(detect_fn=lambda: [])

        out = capsys.readouterr().out
        lines = out.strip().splitlines()
        # GitHub URL is in the last few lines
        tail = "\n".join(lines[-3:])
        assert "https://github.com/Lexonight1/thermalright-trcc-linux/issues/new" in tail


# ===========================================================================
# TestPerfCommand
# ===========================================================================

class TestPerfCommand:
    """trcc perf — software and device benchmarks."""

    def test_perf_software_only(self, capsys):
        """trcc perf (no --device) runs software benchmarks."""
        from trcc.legacy.core.perf import PerfReport

        mock_report = PerfReport()
        mock_report.record_cpu("test_bench", 0.001, 0.01)

        with patch("trcc.legacy.services.perf.run_benchmarks", return_value=mock_report):
            from trcc.legacy.ui.cli import _cmd_perf
            rc = _cmd_perf(device=False)

        assert rc == 0
        out = capsys.readouterr().out
        assert "test_bench" in out

    def test_perf_device_no_devices(self, capsys):
        """trcc perf --device with no devices prints error."""
        from trcc.legacy.core.perf import PerfReport

        with patch("trcc.legacy.services.perf.run_device_benchmarks",
                    return_value=PerfReport()):
            from trcc.legacy.ui.cli import _cmd_perf
            rc = _cmd_perf(device=True)

        assert rc == 1
        out = capsys.readouterr().out
        assert "No devices found" in out

    def test_perf_device_with_results(self, capsys):
        """trcc perf --device with device data prints report."""
        from trcc.legacy.core.perf import PerfReport

        report = PerfReport()
        report.record_device("LCD handshake", 0.5, 2.0)
        report.record_device("LCD send frame", 0.02, 0.1)

        with patch("trcc.legacy.services.perf.run_device_benchmarks",
                    return_value=report):
            from trcc.legacy.ui.cli import _cmd_perf
            rc = _cmd_perf(device=True)

        assert rc == 0
        out = capsys.readouterr().out
        assert "DEVICE I/O" in out
        assert "LCD handshake" in out

    def test_perf_device_failure_returns_1(self, capsys):
        """trcc perf --device with failing benchmark returns exit 1."""
        from trcc.legacy.core.perf import PerfReport

        report = PerfReport()
        report.record_device("slow_handshake", 5.0, 2.0)

        with patch("trcc.legacy.services.perf.run_device_benchmarks",
                    return_value=report):
            from trcc.legacy.ui.cli import _cmd_perf
            rc = _cmd_perf(device=True)

        assert rc == 1
