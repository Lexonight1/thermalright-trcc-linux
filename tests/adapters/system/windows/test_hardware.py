"""Tests for Windows hardware info (WMI-based, mocked — runs on Linux)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

MODULE = 'trcc.legacy.adapters.system.windows_platform'


# ── Helpers ────────────────────────────────────────────────────────────


def _mock_wmi_memory(**kwargs):
    mem = MagicMock()
    mem.Manufacturer = kwargs.get('manufacturer', 'G.Skill')
    mem.PartNumber = kwargs.get('part_number', 'F5-6000J3636F16G')
    mem.ConfiguredClockSpeed = kwargs.get('clock', 6000)
    mem.Speed = kwargs.get('speed', 6000)
    mem.Capacity = kwargs.get('capacity', str(16 * 1024 ** 3))  # 16 GB
    mem.FormFactor = kwargs.get('form_factor', 8)  # DIMM
    mem.SMBIOSMemoryType = kwargs.get('mem_type', 34)  # DDR5
    mem.DeviceLocator = kwargs.get('locator', 'DIMM_A1')
    mem.Rank = kwargs.get('rank', 2)
    mem.DataWidth = kwargs.get('data_width', 64)
    mem.TotalWidth = kwargs.get('total_width', 72)
    return mem


def _mock_wmi_disk(**kwargs):
    disk = MagicMock()
    disk.DeviceID = kwargs.get('device_id', '\\\\.\\PHYSICALDRIVE0')
    disk.Model = kwargs.get('model', 'Samsung SSD 990 PRO 2TB')
    disk.Size = kwargs.get('size', str(2 * 1024 ** 4))  # 2 TB
    disk.PNPDeviceID = kwargs.get('pnp_id', 'SCSI\\DISK')
    disk.MediaType = kwargs.get('media_type', '')
    return disk


# ── Memory Info Tests ─────────────────────────────────────────────────


class TestGetMemoryInfo:

    @patch(f'{MODULE}.wmi', create=True)
    def test_returns_memory_slots(self, mock_wmi_mod):
        mock_wmi = MagicMock()
        mock_wmi_mod.WMI.return_value = mock_wmi
        mock_wmi.Win32_PhysicalMemory.return_value = [
            _mock_wmi_memory(),
            _mock_wmi_memory(locator='DIMM_A2'),
        ]

        # Need to make wmi importable
        import sys
        sys.modules['wmi'] = mock_wmi_mod

        try:
            from trcc.legacy.adapters.system.windows_platform import get_memory_info
            slots = get_memory_info()
            assert len(slots) == 2
            assert slots[0]['manufacturer'] == 'G.Skill'
            assert slots[0]['type'] == 'DDR5'
            assert slots[0]['form_factor'] == 'DIMM'
            assert slots[0]['speed'] == '6000'
            assert '16' in slots[0]['size']  # 16 GB
            assert slots[0]['locator'] == 'DIMM_A1'
        finally:
            sys.modules.pop('wmi', None)

    def test_psutil_fallback_without_wmi(self):
        """When wmi not installed, falls back to psutil total."""
        from trcc.legacy.adapters.system.windows_platform import get_memory_info

        # wmi is not installed on Linux, so ImportError path
        slots = get_memory_info()
        assert len(slots) == 1
        assert slots[0]['manufacturer'] == 'Unknown'
        assert 'GB' in slots[0]['size']


class TestGetDiskInfo:

    def test_returns_empty_without_wmi(self):
        """When wmi not installed, returns empty list."""
        from trcc.legacy.adapters.system.windows_platform import get_disk_info
        disks = get_disk_info()
        assert disks == []


# ── Helper Tests ──────────────────────────────────────────────────────


class TestFormatSize:

    def test_terabytes(self):
        from trcc.legacy.adapters.system.windows_platform import _format_size
        assert '1.0 TB' in _format_size(1024 ** 4)

    def test_gigabytes(self):
        from trcc.legacy.adapters.system.windows_platform import _format_size
        assert '16 GB' in _format_size(16 * 1024 ** 3)

    def test_megabytes(self):
        from trcc.legacy.adapters.system.windows_platform import _format_size
        assert '512 MB' in _format_size(512 * 1024 ** 2)

    def test_bytes(self):
        from trcc.legacy.adapters.system.windows_platform import _format_size
        assert _format_size(1024) == '1024 B'

    def test_none(self):
        from trcc.legacy.adapters.system.windows_platform import _format_size
        assert _format_size(None) == ''

    def test_string_input(self):
        from trcc.legacy.adapters.system.windows_platform import _format_size
        result = _format_size(str(16 * 1024 ** 3))
        assert '16 GB' in result

    def test_invalid_string(self):
        from trcc.legacy.adapters.system.windows_platform import _format_size
        assert _format_size('not_a_number') == 'not_a_number'


class TestMemoryFormFactor:

    def test_known_codes(self):
        from trcc.legacy.adapters.system.windows_platform import _memory_form_factor
        assert _memory_form_factor(8) == 'DIMM'
        assert _memory_form_factor(12) == 'SODIMM'

    def test_unknown_code(self):
        from trcc.legacy.adapters.system.windows_platform import _memory_form_factor
        assert _memory_form_factor(99) == 'Unknown'

    def test_none(self):
        from trcc.legacy.adapters.system.windows_platform import _memory_form_factor
        assert _memory_form_factor(None) == 'Unknown'


class TestMemoryType:

    def test_known_types(self):
        from trcc.legacy.adapters.system.windows_platform import _memory_type
        assert _memory_type(26) == 'DDR4'
        assert _memory_type(34) == 'DDR5'
        assert _memory_type(24) == 'DDR3'

    def test_unknown_type(self):
        from trcc.legacy.adapters.system.windows_platform import _memory_type
        assert _memory_type(99) == 'Unknown'


class TestDiskType:

    def test_ssd_from_model(self):
        from trcc.legacy.adapters.system.windows_platform import _disk_type
        disk = MagicMock()
        disk.Model = 'Samsung SSD 990 PRO'
        disk.MediaType = ''
        assert _disk_type(disk) == 'SSD'

    def test_nvme_from_model(self):
        from trcc.legacy.adapters.system.windows_platform import _disk_type
        disk = MagicMock()
        disk.Model = 'WD Black NVME 1TB'
        disk.MediaType = ''
        assert _disk_type(disk) == 'SSD'

    def test_hdd_from_model(self):
        from trcc.legacy.adapters.system.windows_platform import _disk_type
        disk = MagicMock()
        disk.Model = 'Seagate Barracuda HDD'
        disk.MediaType = ''
        assert _disk_type(disk) == 'HDD'

    def test_hdd_from_media_type(self):
        from trcc.legacy.adapters.system.windows_platform import _disk_type
        disk = MagicMock()
        disk.Model = 'Generic Disk'
        disk.MediaType = 'Fixed hard disk media'
        assert _disk_type(disk) == 'HDD'

    def test_ssd_from_media_type(self):
        from trcc.legacy.adapters.system.windows_platform import _disk_type
        disk = MagicMock()
        disk.Model = 'Generic Disk'
        disk.MediaType = 'Solid state drive'
        assert _disk_type(disk) == 'SSD'

    def test_unknown(self):
        from trcc.legacy.adapters.system.windows_platform import _disk_type
        disk = MagicMock()
        disk.Model = 'Generic'
        disk.MediaType = ''
        assert _disk_type(disk) == 'Unknown'
