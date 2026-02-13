"""Tests for trcc doctor — dependency health check."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from trcc.doctor import (
    _check_binary,
    _check_library,
    _check_python_module,
    _check_udev_rules,
    _detect_pkg_manager,
    _install_hint,
    _read_os_release,
    run_doctor,
)

# ── Distro detection ────────────────────────────────────────────────────────


class TestReadOsRelease(unittest.TestCase):
    """Test os-release parsing."""

    @patch('trcc.doctor.platform.freedesktop_os_release',
           return_value={'ID': 'fedora', 'PRETTY_NAME': 'Fedora 43'})
    def test_uses_platform_api(self, mock_rel):
        result = _read_os_release()
        self.assertEqual(result['ID'], 'fedora')
        mock_rel.assert_called_once()

    @patch('trcc.doctor.platform.freedesktop_os_release', side_effect=OSError)
    @patch('trcc.doctor.os.path.isfile', return_value=False)
    def test_fallback_returns_empty(self, _isfile, _rel):
        result = _read_os_release()
        self.assertEqual(result, {})


class TestDetectPkgManager(unittest.TestCase):
    """Test distro → package manager mapping."""

    @patch('trcc.doctor._read_os_release',
           return_value={'ID': 'fedora', 'ID_LIKE': ''})
    def test_fedora(self, _):
        self.assertEqual(_detect_pkg_manager(), 'dnf')

    @patch('trcc.doctor._read_os_release',
           return_value={'ID': 'ubuntu', 'ID_LIKE': 'debian'})
    def test_ubuntu(self, _):
        self.assertEqual(_detect_pkg_manager(), 'apt')

    @patch('trcc.doctor._read_os_release',
           return_value={'ID': 'arch', 'ID_LIKE': ''})
    def test_arch(self, _):
        self.assertEqual(_detect_pkg_manager(), 'pacman')

    @patch('trcc.doctor._read_os_release',
           return_value={'ID': 'pop', 'ID_LIKE': 'ubuntu debian'})
    def test_pop_os(self, _):
        self.assertEqual(_detect_pkg_manager(), 'apt')

    @patch('trcc.doctor._read_os_release',
           return_value={'ID': 'nobara', 'ID_LIKE': 'fedora'})
    def test_nobara(self, _):
        self.assertEqual(_detect_pkg_manager(), 'dnf')

    @patch('trcc.doctor._read_os_release',
           return_value={'ID': 'cachyos', 'ID_LIKE': 'arch'})
    def test_cachyos_id_like_fallback(self, _):
        self.assertEqual(_detect_pkg_manager(), 'pacman')

    @patch('trcc.doctor._read_os_release',
           return_value={'ID': 'unknowndistro', 'ID_LIKE': ''})
    def test_unknown_returns_none(self, _):
        self.assertIsNone(_detect_pkg_manager())

    @patch('trcc.doctor._read_os_release',
           return_value={'ID': 'zorin', 'ID_LIKE': 'ubuntu debian'})
    def test_zorin(self, _):
        self.assertEqual(_detect_pkg_manager(), 'apt')

    @patch('trcc.doctor._read_os_release',
           return_value={'ID': 'void', 'ID_LIKE': ''})
    def test_void(self, _):
        self.assertEqual(_detect_pkg_manager(), 'xbps')

    @patch('trcc.doctor._read_os_release',
           return_value={'ID': 'opensuse-tumbleweed', 'ID_LIKE': 'suse'})
    def test_opensuse(self, _):
        self.assertEqual(_detect_pkg_manager(), 'zypper')


# ── Install hints ────────────────────────────────────────────────────────────


class TestInstallHint(unittest.TestCase):
    """Test distro-specific install command generation."""

    def test_fedora_sg_raw(self):
        hint = _install_hint('sg_raw', 'dnf')
        self.assertIn('dnf install', hint)
        self.assertIn('sg3_utils', hint)

    def test_apt_7z(self):
        hint = _install_hint('7z', 'apt')
        self.assertIn('apt install', hint)
        self.assertIn('p7zip-full', hint)

    def test_unknown_pm_shows_all(self):
        hint = _install_hint('sg_raw', None)
        self.assertIn('install one of:', hint)

    def test_unknown_dep(self):
        hint = _install_hint('nonexistent', 'dnf')
        self.assertEqual(hint, 'install nonexistent')


# ── Check helpers ────────────────────────────────────────────────────────────


class TestCheckPythonModule(unittest.TestCase):
    """Test Python module availability checking."""

    def test_installed_module(self):
        """Existing module (os) returns True."""
        result = _check_python_module('os', 'os', required=True, pm=None)
        self.assertTrue(result)

    def test_missing_required(self):
        """Missing required module returns False."""
        result = _check_python_module(
            'nonexistent', 'nonexistent_pkg_xyz', required=True, pm=None)
        self.assertFalse(result)

    def test_missing_optional(self):
        """Missing optional module returns True (not a failure)."""
        result = _check_python_module(
            'nonexistent', 'nonexistent_pkg_xyz', required=False, pm=None)
        self.assertTrue(result)


class TestCheckBinary(unittest.TestCase):
    """Test binary availability checking."""

    @patch('shutil.which', return_value='/usr/bin/7z')
    def test_found(self, _):
        result = _check_binary('7z', required=True, pm='dnf')
        self.assertTrue(result)

    @patch('shutil.which', return_value=None)
    def test_missing_required(self, _):
        result = _check_binary('7z', required=True, pm='dnf')
        self.assertFalse(result)

    @patch('shutil.which', return_value=None)
    def test_missing_optional(self, _):
        result = _check_binary('ffmpeg', required=False, pm='apt')
        self.assertTrue(result)


class TestCheckLibrary(unittest.TestCase):
    """Test shared library checking."""

    @patch('ctypes.util.find_library', return_value='libusb-1.0.so.0')
    def test_found(self, _):
        result = _check_library(
            'libusb-1.0', 'usb-1.0', required=True, pm='dnf', dep_key='libusb')
        self.assertTrue(result)

    @patch('ctypes.util.find_library', return_value=None)
    def test_missing_required(self, _):
        result = _check_library(
            'libusb-1.0', 'usb-1.0', required=True, pm='dnf', dep_key='libusb')
        self.assertFalse(result)


class TestCheckUdevRules(unittest.TestCase):
    """Test udev rules check."""

    @patch('trcc.doctor.os.path.isfile', return_value=True)
    def test_rules_exist(self, _):
        self.assertTrue(_check_udev_rules())

    @patch('trcc.doctor.os.path.isfile', return_value=False)
    def test_rules_missing(self, _):
        self.assertFalse(_check_udev_rules())


# ── Integration: run_doctor() ────────────────────────────────────────────────


class TestRunDoctor(unittest.TestCase):
    """Test run_doctor() return codes."""

    @patch('trcc.doctor._check_udev_rules', return_value=True)
    @patch('trcc.doctor._check_library', return_value=True)
    @patch('trcc.doctor._check_binary', return_value=True)
    @patch('trcc.doctor._check_python_module', return_value=True)
    @patch('trcc.doctor._detect_pkg_manager', return_value='dnf')
    @patch('trcc.doctor._read_os_release',
           return_value={'PRETTY_NAME': 'TestOS'})
    def test_all_ok_returns_0(self, *_):
        self.assertEqual(run_doctor(), 0)

    @patch('trcc.doctor._check_udev_rules', return_value=False)
    @patch('trcc.doctor._check_library', return_value=True)
    @patch('trcc.doctor._check_binary', return_value=True)
    @patch('trcc.doctor._check_python_module', return_value=True)
    @patch('trcc.doctor._detect_pkg_manager', return_value='apt')
    @patch('trcc.doctor._read_os_release',
           return_value={'PRETTY_NAME': 'Ubuntu 24.04'})
    def test_missing_udev_returns_1(self, *_):
        self.assertEqual(run_doctor(), 1)


# ── CLI dispatch ─────────────────────────────────────────────────────────────


class TestDoctorCLI(unittest.TestCase):
    """Test doctor command dispatch from CLI."""

    @patch('trcc.doctor.run_doctor', return_value=0)
    @patch('sys.argv', ['trcc', 'doctor'])
    def test_dispatch(self, mock_doctor):
        from trcc.cli import main
        result = main()
        self.assertEqual(result, 0)
        mock_doctor.assert_called_once()
