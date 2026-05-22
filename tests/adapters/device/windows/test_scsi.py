"""Tests for Windows SCSI passthrough transport (mocked — runs on Linux).

Tests verify the ctypes structure layout and transport logic without
requiring Windows. Actual DeviceIoControl calls are mocked.
"""
from __future__ import annotations

import ctypes
from unittest.mock import MagicMock, patch

import pytest

MODULE = 'trcc.legacy.adapters.device.windows.scsi'


# ── Structure Layout Tests ────────────────────────────────────────────


class TestScsiStructures:
    """Verify ctypes structure sizes and field offsets."""

    @pytest.fixture(autouse=True)
    def _skip_on_linux(self):
        """Skip structure tests on Linux (ctypes.wintypes not available)."""
        try:
            import ctypes.wintypes  # noqa: F401
        except (ImportError, ValueError):
            pytest.skip("ctypes.wintypes not available on Linux")

    def test_sptd_has_cdb_field(self):
        from trcc.legacy.adapters.device.windows.scsi import SCSI_PASS_THROUGH_DIRECT
        sptd = SCSI_PASS_THROUGH_DIRECT()
        assert hasattr(sptd, 'Cdb')
        assert len(sptd.Cdb) == 16

    def test_sptd_with_buffer_has_sense(self):
        from trcc.legacy.adapters.device.windows.scsi import (
            SCSI_PASS_THROUGH_DIRECT_WITH_BUFFER,
        )
        sptdwb = SCSI_PASS_THROUGH_DIRECT_WITH_BUFFER()
        assert hasattr(sptdwb, 'sense')
        assert len(sptdwb.sense) == 32

    def test_spt_buffered_has_offset_fields(self):
        from trcc.legacy.adapters.device.windows.scsi import SCSI_PASS_THROUGH
        spt = SCSI_PASS_THROUGH()
        assert hasattr(spt, 'DataBufferOffset')
        assert hasattr(spt, 'SenseInfoOffset')
        assert hasattr(spt, 'Cdb')
        assert len(spt.Cdb) == 16


# ── Transport Logic Tests (all platforms) ─────────────────────────────


class TestWindowsScsiTransport:
    """Test transport logic with mocked Windows API."""

    @pytest.fixture(autouse=True)
    def _wintypes_mock(self):
        """Inject ctypes.wintypes for the whole test — kept alive for runtime calls."""
        import importlib
        import sys

        wintypes = MagicMock()
        wintypes.USHORT = ctypes.c_ushort
        wintypes.ULONG = ctypes.c_ulong
        wintypes.DWORD = ctypes.c_ulong

        # Python 3.14: reload() no longer auto-sets parent attribute from sys.modules.
        # Set both so module-level `import ctypes.wintypes` and runtime `ctypes.wintypes.X`
        # both work.
        sys.modules['ctypes.wintypes'] = wintypes
        ctypes.wintypes = wintypes  # type: ignore[attr-defined]

        sys.modules.pop(MODULE, None)
        importlib.import_module(MODULE)

        yield

        sys.modules.pop('ctypes.wintypes', None)
        sys.modules.pop(MODULE, None)
        try:
            del ctypes.wintypes  # type: ignore[attr-defined]
        except AttributeError:
            pass

    def _make_transport(self):
        """Create transport (wintypes already mocked by fixture)."""
        import importlib
        mod = importlib.import_module(MODULE)
        return mod.WindowsScsiTransport('\\\\.\\PhysicalDrive2')

    def test_init_stores_path(self):
        transport = self._make_transport()
        assert transport._device_path == '\\\\.\\PhysicalDrive2'
        assert transport._handle is None

    def test_send_cdb_fails_when_not_open(self):
        transport = self._make_transport()
        result = transport.send_cdb(b'\xef\x01', b'\x00' * 512)
        assert result is False

    def test_read_cdb_fails_when_not_open(self):
        transport = self._make_transport()
        result = transport.read_cdb(b'\xf5\x00', 64)
        assert result == b''

    def test_read_cdb_uses_buffered_ioctl(self):
        """read_cdb must use IOCTL_SCSI_PASS_THROUGH (0x4D004), not DIRECT."""
        transport = self._make_transport()
        transport._handle = 42  # fake open handle

        mock_kernel32 = MagicMock()
        mock_kernel32.DeviceIoControl.return_value = 1  # success
        mock_windll = MagicMock()
        mock_windll.kernel32 = mock_kernel32

        with patch('ctypes.windll', mock_windll, create=True), \
             patch('ctypes.GetLastError', return_value=0, create=True):
            transport.read_cdb(b'\xf5' + b'\x00' * 15, 64)

        ioctl_code = mock_kernel32.DeviceIoControl.call_args[0][1]
        assert ioctl_code == 0x4D004, (
            f"read_cdb must use IOCTL_SCSI_PASS_THROUGH (0x4D004), got {ioctl_code:#x}"
        )

    def test_close_noop_when_not_open(self):
        transport = self._make_transport()
        transport.close()  # Should not raise
        assert transport._handle is None

    def test_context_manager(self):
        transport = self._make_transport()
        with patch.object(transport, 'open') as mock_open, \
             patch.object(transport, 'close') as mock_close:
            with transport:
                pass
            mock_open.assert_called_once()
            mock_close.assert_called_once()

    def test_open_sets_handle_none_on_failure(self):
        transport = self._make_transport()
        # Simulate CreateFileW returning -1 (INVALID_HANDLE_VALUE)
        mock_kernel32 = MagicMock()
        mock_kernel32.CreateFileW.return_value = -1

        mock_windll = MagicMock()
        mock_windll.kernel32 = mock_kernel32

        with patch('ctypes.windll', mock_windll, create=True):
            result = transport.open()

        assert result is False
        assert transport._handle is None
