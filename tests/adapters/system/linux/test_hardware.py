"""Tests for hardware info queries — dmidecode, lsblk, smartctl wrappers.

Tests cover:
- _privileged_cmd: root vs non-root, polkit presence, pkexec availability
- get_memory_info: dmidecode parsing, slot filtering, psutil fallback, exceptions
- get_disk_info: lsblk JSON parsing, type filtering, SMART health integration
- _get_smart_health: PASSED/FAILED extraction, missing health line, exceptions
"""

import json
import os
import shutil
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src'))

from trcc.legacy.adapters.system.linux_platform import (
    _DMI_MEMORY_FIELDS,
    _POLKIT_POLICY,
    _get_smart_health,
    _privileged_cmd,
    get_disk_info,
    get_memory_info,
)

# ---------------------------------------------------------------------------
# Realistic dmidecode output fixtures
# ---------------------------------------------------------------------------

DMIDECODE_TWO_SLOTS = """\
# dmidecode 3.5
Getting SMBIOS data from sysfs.
SMBIOS 3.3.0 present.

Handle 0x0040, DMI type 17, 92 bytes
Memory Device
\tTotal Width: 64 bits
\tData Width: 64 bits
\tSize: 16 GB
\tForm Factor: DIMM
\tLocator: DIMM_A1
\tType: DDR5
\tSpeed: 5600 MT/s
\tManufacturer: Samsung
\tPart Number: M425R2GA3BB0-CQKOL
\tRank: 1
\tConfigured Memory Speed: 4800 MT/s
\tConfigured Voltage: 1.1 V
\tMinimum Voltage: 1.1 V
\tMaximum Voltage: 1.1 V
\tMemory Technology: DRAM

Handle 0x0041, DMI type 17, 92 bytes
Memory Device
\tTotal Width: 64 bits
\tData Width: 64 bits
\tSize: 16 GB
\tForm Factor: DIMM
\tLocator: DIMM_B1
\tType: DDR5
\tSpeed: 5600 MT/s
\tManufacturer: Samsung
\tPart Number: M425R2GA3BB0-CQKOL
\tRank: 1
\tConfigured Memory Speed: 4800 MT/s
\tConfigured Voltage: 1.1 V
\tMinimum Voltage: 1.1 V
\tMaximum Voltage: 1.1 V
\tMemory Technology: DRAM
"""

DMIDECODE_EMPTY_AND_POPULATED = """\
Handle 0x0040, DMI type 17, 92 bytes
Memory Device
\tSize: 16 GB
\tType: DDR4
\tSpeed: 3200 MT/s
\tManufacturer: G.Skill
\tPart Number: F4-3200C16-16GVK

Handle 0x0041, DMI type 17, 92 bytes
Memory Device
\tSize: No Module Installed
\tType: Unknown

Handle 0x0042, DMI type 17, 92 bytes
Memory Device
\tSize: 16 GB
\tType: DDR4
\tSpeed: 3200 MT/s
\tManufacturer: G.Skill
\tPart Number: F4-3200C16-16GVK
"""

DMIDECODE_ALL_EMPTY = """\
Handle 0x0040, DMI type 17, 92 bytes
Memory Device
\tSize: No Module Installed
\tType: Unknown

Handle 0x0041, DMI type 17, 92 bytes
Memory Device
\tSize: No Module Installed
\tType: Unknown
"""

LSBLK_JSON = json.dumps({
    "blockdevices": [
        {
            "name": "sda",
            "model": "Samsung SSD 970 EVO Plus  ",
            "size": "465.8G",
            "type": "disk",
            "rota": False,
        },
        {
            "name": "sdb",
            "model": "WDC WD10EZEX-00W  ",
            "size": "931.5G",
            "type": "disk",
            "rota": True,
        },
        {
            "name": "sda1",
            "model": None,
            "size": "465.8G",
            "type": "part",
            "rota": False,
        },
        {
            "name": "loop0",
            "model": None,
            "size": "100M",
            "type": "loop",
            "rota": False,
        },
    ]
})

SMARTCTL_PASSED = """\
smartctl 7.4 2023-08-01 r5530
=== START OF READ SMART DATA SECTION ===
SMART overall-health self-assessment test result: PASSED
"""

SMARTCTL_FAILED = """\
smartctl 7.4 2023-08-01 r5530
=== START OF READ SMART DATA SECTION ===
SMART overall-health self-assessment test result: FAILED
"""

SMARTCTL_NVME_PASSED = """\
smartctl 7.4 2023-08-01 r5530
=== START OF SMART DATA SECTION ===
SMART/Health Information (NVMe Log 0x02)
Critical Warning:                   0x00
SMART Health Status: PASSED
"""

SMARTCTL_NO_HEALTH = """\
smartctl 7.4 2023-08-01 r5530
=== START OF INFORMATION SECTION ===
Device Model:     Samsung SSD 860 EVO
"""


def _make_run_result(stdout: str = '', returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr='')


# ===================================================================
# _privileged_cmd
# ===================================================================

class TestPrivilegedCmd:
    """Tests for _privileged_cmd privilege escalation logic."""

    def test_as_root_returns_direct_command(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Running as root (euid 0) bypasses pkexec entirely."""
        monkeypatch.setattr(os, 'geteuid', lambda: 0)
        result = _privileged_cmd('dmidecode', ['-t', 'memory'])
        assert result == ['dmidecode', '-t', 'memory']

    def test_nonroot_with_polkit_and_pkexec(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Non-root + polkit policy + pkexec available = pkexec prefix."""
        monkeypatch.setattr(os, 'geteuid', lambda: 1000)
        monkeypatch.setattr(os.path, 'isfile', lambda p: p == _POLKIT_POLICY)
        monkeypatch.setattr(shutil, 'which', lambda b: f'/usr/sbin/{b}')
        result = _privileged_cmd('dmidecode', ['-t', 'memory'])
        assert result == ['pkexec', '/usr/sbin/dmidecode', '-t', 'memory']

    def test_nonroot_no_polkit_policy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Non-root without polkit policy falls back to direct command."""
        monkeypatch.setattr(os, 'geteuid', lambda: 1000)
        monkeypatch.setattr(os.path, 'isfile', lambda p: False)
        monkeypatch.setattr(shutil, 'which', lambda b: f'/usr/bin/{b}')
        result = _privileged_cmd('smartctl', ['-H', '/dev/sda'])
        assert result == ['smartctl', '-H', '/dev/sda']

    def test_nonroot_polkit_exists_but_no_pkexec(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Non-root + polkit policy exists but pkexec not installed."""
        monkeypatch.setattr(os, 'geteuid', lambda: 1000)
        monkeypatch.setattr(os.path, 'isfile', lambda p: p == _POLKIT_POLICY)

        def which_no_pkexec(b: str) -> str | None:
            if b == 'pkexec':
                return None
            return f'/usr/sbin/{b}'

        monkeypatch.setattr(shutil, 'which', which_no_pkexec)
        result = _privileged_cmd('dmidecode', ['-t', 'memory'])
        assert result == ['dmidecode', '-t', 'memory']

    def test_nonroot_binary_not_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Non-root + binary not in PATH (which returns None) → direct command."""
        monkeypatch.setattr(os, 'geteuid', lambda: 1000)
        monkeypatch.setattr(os.path, 'isfile', lambda p: True)
        monkeypatch.setattr(shutil, 'which', lambda b: None)
        result = _privileged_cmd('dmidecode', ['-t', 'memory'])
        assert result == ['dmidecode', '-t', 'memory']

    def test_empty_args(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Empty args list produces just the binary."""
        monkeypatch.setattr(os, 'geteuid', lambda: 0)
        result = _privileged_cmd('lsblk', [])
        assert result == ['lsblk']


# ===================================================================
# get_memory_info
# ===================================================================

class TestGetMemoryInfo:
    """Tests for get_memory_info dmidecode parser."""

    @patch('trcc.legacy.adapters.system.linux_platform.subprocess.run')
    @patch('trcc.legacy.adapters.system.linux_platform._privileged_cmd',
           return_value=['dmidecode', '-t', 'memory'])
    def test_two_populated_slots(self, mock_cmd: MagicMock, mock_run: MagicMock) -> None:
        """Two populated DIMMs are both returned with all parsed fields."""
        mock_run.return_value = _make_run_result(DMIDECODE_TWO_SLOTS)
        slots = get_memory_info()
        assert len(slots) == 2
        for slot in slots:
            assert slot['size'] == '16 GB'
            assert slot['type'] == 'DDR5'
            assert slot['speed'] == '5600 MT/s'
            assert slot['manufacturer'] == 'Samsung'
            assert slot['part_number'] == 'M425R2GA3BB0-CQKOL'
            assert slot['form_factor'] == 'DIMM'
            assert slot['rank'] == '1'
            assert slot['configured_memory_speed'] == '4800 MT/s'
            assert slot['configured_voltage'] == '1.1 V'
            assert slot['memory_technology'] == 'DRAM'

    @patch('trcc.legacy.adapters.system.linux_platform.subprocess.run')
    @patch('trcc.legacy.adapters.system.linux_platform._privileged_cmd',
           return_value=['dmidecode', '-t', 'memory'])
    def test_empty_slots_filtered(self, mock_cmd: MagicMock, mock_run: MagicMock) -> None:
        """'No Module Installed' slots are excluded, populated slots kept."""
        mock_run.return_value = _make_run_result(DMIDECODE_EMPTY_AND_POPULATED)
        slots = get_memory_info()
        assert len(slots) == 2
        assert all(s['manufacturer'] == 'G.Skill' for s in slots)

    @patch('trcc.legacy.adapters.system.linux_platform.subprocess.run')
    @patch('trcc.legacy.adapters.system.linux_platform._privileged_cmd',
           return_value=['dmidecode', '-t', 'memory'])
    def test_all_empty_falls_back_to_psutil(self, mock_cmd: MagicMock, mock_run: MagicMock) -> None:
        """All slots empty → psutil fallback provides basic total."""
        mock_run.return_value = _make_run_result(DMIDECODE_ALL_EMPTY)
        mem_mock = SimpleNamespace(total=34359738368)  # 32 GB
        psutil_mock = MagicMock()
        psutil_mock.virtual_memory.return_value = mem_mock
        with patch('trcc.legacy.adapters.system.linux_platform.psutil', psutil_mock):
            slots = get_memory_info()
        assert len(slots) == 1
        assert slots[0]['size'] == '32.0 GB'
        assert slots[0]['type'] == 'Unknown'

    @patch('trcc.legacy.adapters.system.linux_platform.subprocess.run')
    @patch('trcc.legacy.adapters.system.linux_platform._privileged_cmd',
           return_value=['dmidecode', '-t', 'memory'])
    def test_dmidecode_nonzero_returncode_falls_back(self, mock_cmd: MagicMock, mock_run: MagicMock) -> None:
        """Non-zero returncode from dmidecode triggers psutil fallback."""
        mock_run.return_value = _make_run_result(stdout='', returncode=1)
        mem_mock = SimpleNamespace(total=17179869184)  # 16 GB
        psutil_mock = MagicMock()
        psutil_mock.virtual_memory.return_value = mem_mock
        with patch('trcc.legacy.adapters.system.linux_platform.psutil', psutil_mock):
            slots = get_memory_info()
        assert len(slots) == 1
        assert slots[0]['size'] == '16.0 GB'

    @patch('trcc.legacy.adapters.system.linux_platform.subprocess.run')
    @patch('trcc.legacy.adapters.system.linux_platform._privileged_cmd',
           return_value=['dmidecode', '-t', 'memory'])
    def test_subprocess_exception_returns_empty(self, mock_cmd: MagicMock, mock_run: MagicMock) -> None:
        """subprocess.run raising an exception returns empty list when psutil also fails."""
        mock_run.side_effect = FileNotFoundError('dmidecode not found')
        with patch.dict('sys.modules', {'psutil': None}):
            slots = get_memory_info()
        assert slots == []

    @patch('trcc.legacy.adapters.system.linux_platform.subprocess.run')
    @patch('trcc.legacy.adapters.system.linux_platform._privileged_cmd',
           return_value=['dmidecode', '-t', 'memory'])
    def test_timeout_exception_returns_empty(self, mock_cmd: MagicMock, mock_run: MagicMock) -> None:
        """subprocess.TimeoutExpired is caught gracefully."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd='dmidecode', timeout=5)
        with patch.dict('sys.modules', {'psutil': None}):
            slots = get_memory_info()
        assert slots == []

    @patch('trcc.legacy.adapters.system.linux_platform.subprocess.run')
    @patch('trcc.legacy.adapters.system.linux_platform._privileged_cmd',
           return_value=['dmidecode', '-t', 'memory'])
    def test_both_dmidecode_and_psutil_fail(self, mock_cmd: MagicMock, mock_run: MagicMock) -> None:
        """Both dmidecode exception and psutil failure → empty list."""
        mock_run.side_effect = OSError('permission denied')

        # Phase 9: psutil is module-level imported in linux_platform; patch its
        # virtual_memory to raise the same exception type as PSUTIL_EXC catches.
        psutil_mock = MagicMock()
        psutil_mock.virtual_memory.side_effect = OSError('no psutil')
        with patch('trcc.legacy.adapters.system.linux_platform.psutil', psutil_mock):
            slots = get_memory_info()
        assert slots == []

    @patch('trcc.legacy.adapters.system.linux_platform.subprocess.run')
    @patch('trcc.legacy.adapters.system.linux_platform._privileged_cmd',
           return_value=['dmidecode', '-t', 'memory'])
    def test_fields_not_in_dmi_fields_ignored(self, mock_cmd: MagicMock, mock_run: MagicMock) -> None:
        """Lines with keys not in _DMI_MEMORY_FIELDS are skipped."""
        output = """\
Handle 0x0040, DMI type 17, 92 bytes
Memory Device
\tSize: 8 GB
\tType: DDR4
\tBank Locator: BANK 0
\tAsset Tag: Not Specified
\tSerial Number: 12345678
"""
        mock_run.return_value = _make_run_result(output)
        slots = get_memory_info()
        assert len(slots) == 1
        assert 'bank_locator' not in slots[0]
        assert 'asset_tag' not in slots[0]
        assert 'serial_number' not in slots[0]
        assert slots[0]['size'] == '8 GB'
        assert slots[0]['type'] == 'DDR4'

    @patch('trcc.legacy.adapters.system.linux_platform.subprocess.run')
    @patch('trcc.legacy.adapters.system.linux_platform._privileged_cmd',
           return_value=['dmidecode', '-t', 'memory'])
    def test_single_slot_no_trailing_header(self, mock_cmd: MagicMock, mock_run: MagicMock) -> None:
        """A single Memory Device block at end of output is still captured."""
        output = """\
Memory Device
\tSize: 32 GB
\tType: DDR5
\tManufacturer: Crucial
"""
        mock_run.return_value = _make_run_result(output)
        slots = get_memory_info()
        assert len(slots) == 1
        assert slots[0]['manufacturer'] == 'Crucial'
        assert slots[0]['size'] == '32 GB'

    @patch('trcc.legacy.adapters.system.linux_platform.subprocess.run')
    @patch('trcc.legacy.adapters.system.linux_platform._privileged_cmd',
           return_value=['dmidecode', '-t', 'memory'])
    def test_empty_stdout(self, mock_cmd: MagicMock, mock_run: MagicMock) -> None:
        """Empty dmidecode stdout (no Memory Device sections) → psutil fallback."""
        mock_run.return_value = _make_run_result('')
        mem_mock = SimpleNamespace(total=8589934592)  # 8 GB
        psutil_mock = MagicMock()
        psutil_mock.virtual_memory.return_value = mem_mock
        with patch('trcc.legacy.adapters.system.linux_platform.psutil', psutil_mock):
            slots = get_memory_info()
        assert len(slots) == 1
        assert slots[0]['size'] == '8.0 GB'


# ===================================================================
# get_disk_info
# ===================================================================

class TestGetDiskInfo:
    """Tests for get_disk_info lsblk parser."""

    @patch('trcc.legacy.adapters.system.linux_platform._get_smart_health')
    @patch('trcc.legacy.adapters.system.linux_platform.subprocess.run')
    def test_two_disks_with_smart(self, mock_run: MagicMock, mock_health: MagicMock) -> None:
        """Two physical disks returned with SMART health data."""
        mock_run.return_value = _make_run_result(LSBLK_JSON)
        mock_health.side_effect = ['PASSED', 'PASSED']
        disks = get_disk_info()
        assert len(disks) == 2
        assert disks[0]['name'] == 'sda'
        assert disks[0]['model'] == 'Samsung SSD 970 EVO Plus'
        assert disks[0]['size'] == '465.8G'
        assert disks[0]['type'] == 'SSD'
        assert disks[0]['health'] == 'PASSED'
        assert disks[1]['name'] == 'sdb'
        assert disks[1]['type'] == 'HDD'

    @patch('trcc.legacy.adapters.system.linux_platform._get_smart_health')
    @patch('trcc.legacy.adapters.system.linux_platform.subprocess.run')
    def test_partitions_and_loops_filtered(self, mock_run: MagicMock, mock_health: MagicMock) -> None:
        """Non-disk entries (partitions, loops) are excluded."""
        mock_run.return_value = _make_run_result(LSBLK_JSON)
        mock_health.return_value = None
        disks = get_disk_info()
        names = [d['name'] for d in disks]
        assert 'sda1' not in names
        assert 'loop0' not in names

    @patch('trcc.legacy.adapters.system.linux_platform._get_smart_health')
    @patch('trcc.legacy.adapters.system.linux_platform.subprocess.run')
    def test_disk_without_model_filtered(self, mock_run: MagicMock, mock_health: MagicMock) -> None:
        """Disk entries with model=None are excluded."""
        data = json.dumps({"blockdevices": [
            {"name": "zram0", "model": None, "size": "8G", "type": "disk", "rota": False},
        ]})
        mock_run.return_value = _make_run_result(data)
        mock_health.return_value = None
        disks = get_disk_info()
        assert disks == []

    @patch('trcc.legacy.adapters.system.linux_platform._get_smart_health')
    @patch('trcc.legacy.adapters.system.linux_platform.subprocess.run')
    def test_rotational_hdd_detected(self, mock_run: MagicMock, mock_health: MagicMock) -> None:
        """rota=True marks disk as HDD."""
        data = json.dumps({"blockdevices": [
            {"name": "sda", "model": "WDC WD10EZEX", "size": "1T", "type": "disk", "rota": True},
        ]})
        mock_run.return_value = _make_run_result(data)
        mock_health.return_value = None
        disks = get_disk_info()
        assert len(disks) == 1
        assert disks[0]['type'] == 'HDD'

    @patch('trcc.legacy.adapters.system.linux_platform._get_smart_health')
    @patch('trcc.legacy.adapters.system.linux_platform.subprocess.run')
    def test_ssd_detected_rota_false(self, mock_run: MagicMock, mock_health: MagicMock) -> None:
        """rota=False marks disk as SSD."""
        data = json.dumps({"blockdevices": [
            {"name": "nvme0n1", "model": "Samsung 980 PRO", "size": "1T", "type": "disk", "rota": False},
        ]})
        mock_run.return_value = _make_run_result(data)
        mock_health.return_value = 'PASSED'
        disks = get_disk_info()
        assert disks[0]['type'] == 'SSD'

    @patch('trcc.legacy.adapters.system.linux_platform._get_smart_health')
    @patch('trcc.legacy.adapters.system.linux_platform.subprocess.run')
    def test_smart_none_omits_health_key(self, mock_run: MagicMock, mock_health: MagicMock) -> None:
        """When SMART returns None, 'health' key is not present in disk dict."""
        data = json.dumps({"blockdevices": [
            {"name": "sda", "model": "Generic SSD", "size": "256G", "type": "disk", "rota": False},
        ]})
        mock_run.return_value = _make_run_result(data)
        mock_health.return_value = None
        disks = get_disk_info()
        assert 'health' not in disks[0]

    @patch('trcc.legacy.adapters.system.linux_platform.subprocess.run')
    def test_lsblk_nonzero_returncode(self, mock_run: MagicMock) -> None:
        """Non-zero lsblk returncode returns empty list."""
        mock_run.return_value = _make_run_result(stdout='', returncode=1)
        disks = get_disk_info()
        assert disks == []

    @patch('trcc.legacy.adapters.system.linux_platform.subprocess.run')
    def test_lsblk_exception_returns_empty(self, mock_run: MagicMock) -> None:
        """lsblk subprocess exception returns empty list."""
        mock_run.side_effect = FileNotFoundError('lsblk not found')
        disks = get_disk_info()
        assert disks == []

    @patch('trcc.legacy.adapters.system.linux_platform.subprocess.run')
    def test_lsblk_timeout_returns_empty(self, mock_run: MagicMock) -> None:
        """lsblk timeout returns empty list."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd='lsblk', timeout=5)
        disks = get_disk_info()
        assert disks == []

    @patch('trcc.legacy.adapters.system.linux_platform.subprocess.run')
    def test_lsblk_invalid_json_returns_empty(self, mock_run: MagicMock) -> None:
        """Malformed JSON from lsblk is caught and returns empty list."""
        mock_run.return_value = _make_run_result(stdout='not json{{{')
        disks = get_disk_info()
        assert disks == []

    @patch('trcc.legacy.adapters.system.linux_platform._get_smart_health')
    @patch('trcc.legacy.adapters.system.linux_platform.subprocess.run')
    def test_empty_blockdevices(self, mock_run: MagicMock, mock_health: MagicMock) -> None:
        """Empty blockdevices list returns no disks."""
        data = json.dumps({"blockdevices": []})
        mock_run.return_value = _make_run_result(data)
        disks = get_disk_info()
        assert disks == []


# ===================================================================
# _get_smart_health
# ===================================================================

class TestGetSmartHealth:
    """Tests for _get_smart_health smartctl parser."""

    @patch('trcc.legacy.adapters.system.linux_platform.subprocess.run')
    @patch('trcc.legacy.adapters.system.linux_platform._privileged_cmd',
           return_value=['smartctl', '-H', '/dev/sda'])
    def test_returns_passed(self, mock_cmd: MagicMock, mock_run: MagicMock) -> None:
        """SATA disk with 'overall-health' PASSED."""
        mock_run.return_value = _make_run_result(SMARTCTL_PASSED)
        assert _get_smart_health('sda') == 'PASSED'

    @patch('trcc.legacy.adapters.system.linux_platform.subprocess.run')
    @patch('trcc.legacy.adapters.system.linux_platform._privileged_cmd',
           return_value=['smartctl', '-H', '/dev/sda'])
    def test_returns_failed(self, mock_cmd: MagicMock, mock_run: MagicMock) -> None:
        """Disk with FAILED SMART status."""
        mock_run.return_value = _make_run_result(SMARTCTL_FAILED)
        assert _get_smart_health('sda') == 'FAILED'

    @patch('trcc.legacy.adapters.system.linux_platform.subprocess.run')
    @patch('trcc.legacy.adapters.system.linux_platform._privileged_cmd',
           return_value=['smartctl', '-H', '/dev/nvme0n1'])
    def test_nvme_health_status_passed(self, mock_cmd: MagicMock, mock_run: MagicMock) -> None:
        """NVMe disk with 'Health Status: PASSED' (different format)."""
        mock_run.return_value = _make_run_result(SMARTCTL_NVME_PASSED)
        assert _get_smart_health('nvme0n1') == 'PASSED'

    @patch('trcc.legacy.adapters.system.linux_platform.subprocess.run')
    @patch('trcc.legacy.adapters.system.linux_platform._privileged_cmd',
           return_value=['smartctl', '-H', '/dev/sda'])
    def test_no_health_line_returns_none(self, mock_cmd: MagicMock, mock_run: MagicMock) -> None:
        """No health-related line in output returns None."""
        mock_run.return_value = _make_run_result(SMARTCTL_NO_HEALTH)
        assert _get_smart_health('sda') is None

    @patch('trcc.legacy.adapters.system.linux_platform.subprocess.run')
    @patch('trcc.legacy.adapters.system.linux_platform._privileged_cmd',
           return_value=['smartctl', '-H', '/dev/sda'])
    def test_health_line_without_passed_or_failed(self, mock_cmd: MagicMock, mock_run: MagicMock) -> None:
        """Health line present but with unexpected result string returns None."""
        output = "SMART overall-health self-assessment test result: UNKNOWN\n"
        mock_run.return_value = _make_run_result(output)
        assert _get_smart_health('sda') is None

    @patch('trcc.legacy.adapters.system.linux_platform.subprocess.run')
    @patch('trcc.legacy.adapters.system.linux_platform._privileged_cmd',
           return_value=['smartctl', '-H', '/dev/sda'])
    def test_empty_stdout_returns_none(self, mock_cmd: MagicMock, mock_run: MagicMock) -> None:
        """Empty smartctl output returns None."""
        mock_run.return_value = _make_run_result('')
        assert _get_smart_health('sda') is None

    @patch('trcc.legacy.adapters.system.linux_platform.subprocess.run')
    @patch('trcc.legacy.adapters.system.linux_platform._privileged_cmd',
           return_value=['smartctl', '-H', '/dev/sda'])
    def test_exception_returns_none(self, mock_cmd: MagicMock, mock_run: MagicMock) -> None:
        """smartctl subprocess exception returns None."""
        mock_run.side_effect = FileNotFoundError('smartctl not found')
        assert _get_smart_health('sda') is None

    @patch('trcc.legacy.adapters.system.linux_platform.subprocess.run')
    @patch('trcc.legacy.adapters.system.linux_platform._privileged_cmd',
           return_value=['smartctl', '-H', '/dev/sda'])
    def test_timeout_returns_none(self, mock_cmd: MagicMock, mock_run: MagicMock) -> None:
        """smartctl timeout returns None."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd='smartctl', timeout=5)
        assert _get_smart_health('sda') is None


# ===================================================================
# Module-level constants
# ===================================================================

class TestModuleConstants:
    """Verify module-level constants are correctly defined."""

    def test_dmi_memory_fields_is_set(self) -> None:
        assert isinstance(_DMI_MEMORY_FIELDS, set)
        assert 'manufacturer' in _DMI_MEMORY_FIELDS
        assert 'part_number' in _DMI_MEMORY_FIELDS
        assert 'type' in _DMI_MEMORY_FIELDS
        assert 'speed' in _DMI_MEMORY_FIELDS
        assert 'size' in _DMI_MEMORY_FIELDS
        assert 'form_factor' in _DMI_MEMORY_FIELDS

    def test_polkit_policy_path(self) -> None:
        assert _POLKIT_POLICY == '/usr/share/polkit-1/actions/com.github.lexonight1.trcc.policy'
