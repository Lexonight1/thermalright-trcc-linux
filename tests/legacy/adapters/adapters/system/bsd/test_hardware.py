"""Tests for BSD hardware info (mocked — runs on Linux)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

MODULE = 'trcc.legacy.adapters.system.bsd_platform'


class TestSysctl:

    @patch(f'{MODULE}.subprocess')
    def test_reads_value(self, mock_sub):
        mock_sub.run.return_value = MagicMock(
            returncode=0, stdout='16777216000\n',
        )
        from trcc.legacy.adapters.system.bsd_platform import _sysctl
        assert _sysctl('hw.physmem') == '16777216000'

    @patch(f'{MODULE}.subprocess')
    def test_returns_empty_on_failure(self, mock_sub):
        mock_sub.run.return_value = MagicMock(returncode=1, stdout='')
        from trcc.legacy.adapters.system.bsd_platform import _sysctl
        assert _sysctl('bad.key') == ''

    @patch(f'{MODULE}.subprocess')
    def test_returns_empty_on_exception(self, mock_sub):
        # Production catches SUBPROCESS_EXC (OSError + SubprocessError + value/key/index).
        # OSError = FileNotFoundError / PermissionError — realistic sysctl failures.
        mock_sub.run.side_effect = OSError("not found")
        from trcc.legacy.adapters.system.bsd_platform import _sysctl
        assert _sysctl('hw.physmem') == ''


class TestGetMemoryInfo:

    @patch(f'{MODULE}._sysctl')
    def test_parses_physmem(self, mock_sysctl):
        mock_sysctl.side_effect = lambda k: {
            'hw.physmem': str(16 * 1024 ** 3),
            'dev.cpu.0.freq': '3200',
        }.get(k, '')
        from trcc.legacy.adapters.system.bsd_platform import get_memory_info
        slots = get_memory_info()
        assert len(slots) == 1
        assert '16 GB' in slots[0]['size']

    @patch(f'{MODULE}._sysctl')
    def test_psutil_fallback(self, mock_sysctl):
        mock_sysctl.return_value = ''
        from trcc.legacy.adapters.system.bsd_platform import get_memory_info
        slots = get_memory_info()
        assert len(slots) == 1
        assert 'GB' in slots[0]['size']


class TestGetDiskInfo:

    @patch(f'{MODULE}.subprocess')
    def test_parses_geom_output(self, mock_sub):
        mock_sub.run.return_value = MagicMock(
            returncode=0,
            stdout=(
                'Geom name: ada0\n'
                '   descr: Samsung SSD 970 EVO 500GB\n'
                '   Mediasize: 500107862016 (466G)\n'
                '   rotationrate: 0\n'
            ),
        )
        from trcc.legacy.adapters.system.bsd_platform import get_disk_info
        disks = get_disk_info()
        assert len(disks) == 1
        assert disks[0]['name'] == 'ada0'
        assert disks[0]['model'] == 'Samsung SSD 970 EVO 500GB'
        assert disks[0]['size'] == '466G'
        assert disks[0]['type'] == 'SSD'

    @patch(f'{MODULE}.subprocess')
    def test_detects_hdd(self, mock_sub):
        mock_sub.run.return_value = MagicMock(
            returncode=0,
            stdout=(
                'Geom name: da0\n'
                '   descr: WD Blue 1TB\n'
                '   Mediasize: 1000204886016 (932G)\n'
                '   rotationrate: 7200\n'
            ),
        )
        from trcc.legacy.adapters.system.bsd_platform import get_disk_info
        disks = get_disk_info()
        assert len(disks) == 1
        assert disks[0]['type'] == 'HDD'

    @patch(f'{MODULE}.subprocess')
    def test_empty_on_failure(self, mock_sub):
        mock_sub.run.return_value = MagicMock(returncode=1, stdout='')
        from trcc.legacy.adapters.system.bsd_platform import get_disk_info
        assert get_disk_info() == []

    @patch(f'{MODULE}.subprocess')
    def test_multiple_disks(self, mock_sub):
        mock_sub.run.return_value = MagicMock(
            returncode=0,
            stdout=(
                'Geom name: ada0\n'
                '   descr: SSD\n'
                '   Mediasize: 500107862016 (466G)\n'
                '   rotationrate: 0\n'
                'Geom name: nvd0\n'
                '   descr: NVMe\n'
                '   Mediasize: 1000204886016 (932G)\n'
                '   rotationrate: 0\n'
            ),
        )
        from trcc.legacy.adapters.system.bsd_platform import get_disk_info
        disks = get_disk_info()
        assert len(disks) == 2
        assert disks[0]['name'] == 'ada0'
        assert disks[1]['name'] == 'nvd0'
