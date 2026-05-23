"""Tests for macOS hardware info (mocked — runs on Linux)."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

MODULE = 'trcc.legacy.adapters.system.macos.hardware'


class TestRunProfiler:

    @patch(f'{MODULE}.subprocess')
    def test_returns_parsed_json(self, mock_sub):
        mock_sub.run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps({'SPMemoryDataType': [{'_name': 'test'}]}),
        )
        from trcc.legacy.adapters.system.macos.hardware import _run_profiler
        result = _run_profiler('SPMemoryDataType')
        assert result == {'SPMemoryDataType': [{'_name': 'test'}]}

    @patch(f'{MODULE}.subprocess')
    def test_returns_empty_on_failure(self, mock_sub):
        mock_sub.run.return_value = MagicMock(returncode=1, stdout='')
        from trcc.legacy.adapters.system.macos.hardware import _run_profiler
        assert _run_profiler('SPMemoryDataType') == {}

    @patch(f'{MODULE}.subprocess')
    def test_returns_empty_on_exception(self, mock_sub):
        mock_sub.run.side_effect = RuntimeError("timeout")
        from trcc.legacy.adapters.system.macos.hardware import _run_profiler
        assert _run_profiler('SPMemoryDataType') == {}


class TestGetMemoryInfo:

    @patch(f'{MODULE}._run_profiler')
    def test_parses_dimm_info(self, mock_profiler):
        mock_profiler.return_value = {
            'SPMemoryDataType': [{
                '_items': [{
                    'dimm_manufacturer': 'Samsung',
                    'dimm_part_number': 'M471A1K43DB1',
                    'dimm_type': 'DDR4',
                    'dimm_speed': '3200 MHz',
                    'dimm_size': '8 GB',
                    'dimm_form_factor': 'SODIMM',
                    '_name': 'BANK 0',
                }],
            }],
        }
        from trcc.legacy.adapters.system.macos.hardware import get_memory_info
        slots = get_memory_info()
        assert len(slots) == 1
        assert slots[0]['manufacturer'] == 'Samsung'
        assert slots[0]['type'] == 'DDR4'
        assert slots[0]['size'] == '8 GB'

    @patch(f'{MODULE}._run_profiler')
    def test_psutil_fallback(self, mock_profiler):
        mock_profiler.return_value = {'SPMemoryDataType': []}
        from trcc.legacy.adapters.system.macos.hardware import get_memory_info
        slots = get_memory_info()
        assert len(slots) == 1
        assert 'GB' in slots[0]['size']


class TestGetDiskInfo:

    @patch(f'{MODULE}._run_profiler')
    def test_parses_disk_info(self, mock_profiler):
        mock_profiler.return_value = {
            'SPStorageDataType': [{
                'bsd_name': 'disk0',
                'physical_drive': {
                    'device_name': 'APPLE SSD AP0512Q',
                    'medium_type': 'Solid State',
                },
                'size_in_bytes': str(512 * 1024 ** 3),
                'smart_status': 'Verified',
            }],
        }
        from trcc.legacy.adapters.system.macos.hardware import get_disk_info
        disks = get_disk_info()
        assert len(disks) == 1
        assert disks[0]['model'] == 'APPLE SSD AP0512Q'
        assert disks[0]['type'] == 'SSD'
        assert '512' in disks[0]['size']
        assert disks[0]['health'] == 'Verified'

    @patch(f'{MODULE}._run_profiler')
    def test_empty_without_data(self, mock_profiler):
        mock_profiler.return_value = {'SPStorageDataType': []}
        from trcc.legacy.adapters.system.macos.hardware import get_disk_info
        assert get_disk_info() == []


class TestIsAppleSilicon:

    @patch(f'{MODULE}.sys.platform', 'linux')
    def test_false_off_darwin(self):
        from trcc.legacy.adapters.system.macos.hardware import _is_apple_silicon
        assert _is_apple_silicon() is False

    @patch(f'{MODULE}.sys.platform', 'darwin')
    @patch(f'{MODULE}.platform.machine', return_value='arm64')
    def test_native_arm64(self, _mock_machine):
        from trcc.legacy.adapters.system.macos.hardware import _is_apple_silicon
        assert _is_apple_silicon() is True

    @patch(f'{MODULE}.sys.platform', 'darwin')
    @patch(f'{MODULE}.platform.machine', return_value='x86_64')
    def test_x86_64_darwin_not_apple_silicon(self, _mock_machine):
        """Intel Mac or Rosetta x86_64 — we only enable AS paths for native arm64."""
        from trcc.legacy.adapters.system.macos.hardware import _is_apple_silicon
        assert _is_apple_silicon() is False
