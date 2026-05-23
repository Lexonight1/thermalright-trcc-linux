"""Tests for trcc.services.perf — device benchmark service.

All device I/O is mocked — no real USB operations.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

# Patch DeviceService at the source module — run_device_benchmarks imports it
_DS = "trcc.legacy.services.DeviceService"


def _run(mock_svc, mock_factory=None):
    """Call run_device_benchmarks with injected mocks."""
    factory = mock_factory or MagicMock()
    detect_fn = MagicMock()
    probe_led_fn = MagicMock()

    with patch(_DS, return_value=mock_svc):
        from trcc.legacy.services.perf import run_device_benchmarks
        return run_device_benchmarks(
            detect_fn=detect_fn,
            get_protocol=factory.get_protocol,
            get_protocol_info=factory.get_protocol_info,
            probe_led_fn=probe_led_fn,
        )


class TestRunDeviceBenchmarks:
    """run_device_benchmarks — LCD and LED hardware benchmarking."""

    def _mock_lcd_device(self):
        from trcc.legacy.core.models import FBL_PROFILES
        profile = FBL_PROFILES[100]   # canonical SCSI 320x320 big-endian device
        dev = MagicMock()
        dev.implementation = 'scsi'
        dev.resolution = profile.resolution
        dev.encoding_params = ('scsi', profile.resolution, 100, not profile.jpeg)
        return dev

    def _mock_led_device(self):
        dev = MagicMock()
        dev.implementation = 'hid_led'
        dev.resolution = None
        return dev

    def _mock_protocol(self):
        proto = MagicMock()
        proto.handshake.return_value = MagicMock()
        proto.send_image.return_value = True
        proto.send_data.return_value = True
        proto.close.return_value = None
        proto.handshake_info = None
        return proto

    @pytest.fixture()
    def _renderer(self):
        """Ensure renderer is available."""
        from trcc.legacy.services.image import ImageService
        if ImageService._renderer is None:
            import os
            os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
            from PySide6.QtWidgets import QApplication
            if QApplication.instance() is None:
                QApplication([])
            from trcc.legacy.adapters.render.qt import QtRenderer
            ImageService.set_renderer(QtRenderer())

    def test_no_devices_returns_empty(self):
        """No devices detected — empty report."""
        mock_svc = MagicMock()
        mock_svc.devices = []

        report = _run(mock_svc)

        assert not report.has_data
        assert len(report.device) == 0

    @pytest.mark.usefixtures("_renderer")
    def test_lcd_benchmarks_recorded(self):
        """LCD device benchmarks produce device entries."""
        lcd_dev = self._mock_lcd_device()
        proto = self._mock_protocol()
        mock_factory = MagicMock()
        mock_factory.get_protocol.return_value = proto

        mock_svc = MagicMock()
        mock_svc.devices = [lcd_dev]

        report = _run(mock_svc, mock_factory)

        assert report.has_data
        labels = [e.label for e in report.device]
        assert any("handshake" in lbl for lbl in labels)
        assert any("encode" in lbl for lbl in labels)
        assert any("send" in lbl for lbl in labels)
        assert any("sustained" in lbl for lbl in labels)

    @pytest.mark.usefixtures("_renderer")
    def test_lcd_handshake_measured(self):
        """Handshake timing is recorded as a device entry."""
        lcd_dev = self._mock_lcd_device()
        proto = self._mock_protocol()
        mock_factory = MagicMock()
        mock_factory.get_protocol.return_value = proto

        mock_svc = MagicMock()
        mock_svc.devices = [lcd_dev]

        report = _run(mock_svc, mock_factory)

        handshake = next(e for e in report.device if "handshake" in e.label)
        assert handshake.limit == 2.0
        assert handshake.actual >= 0

    @pytest.mark.usefixtures("_renderer")
    def test_led_benchmarks_recorded(self):
        """LED device benchmarks produce device entries."""
        led_dev = self._mock_led_device()
        proto = self._mock_protocol()
        mock_factory = MagicMock()
        mock_factory.get_protocol.return_value = proto

        mock_svc = MagicMock()
        mock_svc.devices = [led_dev]

        report = _run(mock_svc, mock_factory)

        labels = [e.label for e in report.device]
        assert any("LED handshake" in lbl for lbl in labels)
        assert any("LED send" in lbl for lbl in labels)
        assert any("LED sustained" in lbl for lbl in labels)

    @pytest.mark.usefixtures("_renderer")
    def test_both_devices_benchmarked(self):
        """Both LCD and LED devices are benchmarked when present."""
        lcd_dev = self._mock_lcd_device()
        led_dev = self._mock_led_device()
        proto = self._mock_protocol()
        mock_factory = MagicMock()
        mock_factory.get_protocol.return_value = proto

        mock_svc = MagicMock()
        mock_svc.devices = [lcd_dev, led_dev]

        report = _run(mock_svc, mock_factory)

        labels = [e.label for e in report.device]
        assert any("LCD" in lbl for lbl in labels)
        assert any("LED" in lbl for lbl in labels)

    @pytest.mark.usefixtures("_renderer")
    def test_led_count_from_handshake_info(self):
        """LED count is extracted from handshake info when available."""
        led_dev = self._mock_led_device()
        proto = self._mock_protocol()
        info = MagicMock()
        info.style.led_count = 128
        proto.handshake_info = info
        mock_factory = MagicMock()
        mock_factory.get_protocol.return_value = proto

        mock_svc = MagicMock()
        mock_svc.devices = [led_dev]

        _run(mock_svc, mock_factory)

        calls = proto.send_data.call_args_list
        assert any(len(c[0][0]) == 128 for c in calls)

    @pytest.mark.usefixtures("_renderer")
    def test_report_serializable(self):
        """Device benchmark report serializes to dict correctly."""
        lcd_dev = self._mock_lcd_device()
        proto = self._mock_protocol()
        mock_factory = MagicMock()
        mock_factory.get_protocol.return_value = proto

        mock_svc = MagicMock()
        mock_svc.devices = [lcd_dev]

        report = _run(mock_svc, mock_factory)

        d = report.to_dict()
        assert "device" in d
        assert len(d["device"]) > 0
        assert d["summary"]["device_count"] > 0


@pytest.mark.skip(reason="Phase 9: _ipc_pause/_ipc_resume helpers removed from services.perf — daemon coordination moved to TrccProxy + manifold IPC.")
class TestIPCPauseResume:
    """IPC pause/resume integration in run_device_benchmarks."""

    def test_pauses_and_resumes_when_daemon_available(self):
        """Device benchmarks pause GUI daemon before, resume after."""
        mock_svc = MagicMock()
        mock_svc.devices = []

        with patch(_DS, return_value=mock_svc), \
             patch("trcc.legacy.services.perf._ipc_pause", return_value=True) as m_pause, \
             patch("trcc.legacy.services.perf._ipc_resume") as m_resume:
            from trcc.legacy.services.perf import run_device_benchmarks
            run_device_benchmarks(
                detect_fn=MagicMock(),
                get_protocol=MagicMock(),
                get_protocol_info=MagicMock(),
                probe_led_fn=MagicMock(),
            )

        m_pause.assert_called_once()
        m_resume.assert_called_once()

    def test_no_resume_when_daemon_not_running(self):
        """No resume call when daemon was not running."""
        mock_svc = MagicMock()
        mock_svc.devices = []

        with patch(_DS, return_value=mock_svc), \
             patch("trcc.legacy.services.perf._ipc_pause", return_value=False) as m_pause, \
             patch("trcc.legacy.services.perf._ipc_resume") as m_resume:
            from trcc.legacy.services.perf import run_device_benchmarks
            run_device_benchmarks(
                detect_fn=MagicMock(),
                get_protocol=MagicMock(),
                get_protocol_info=MagicMock(),
                probe_led_fn=MagicMock(),
            )

        m_pause.assert_called_once()
        m_resume.assert_not_called()

    @pytest.fixture()
    def _renderer(self):
        from trcc.legacy.services.image import ImageService
        if ImageService._renderer is None:
            import os
            os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
            from PySide6.QtWidgets import QApplication
            if QApplication.instance() is None:
                QApplication([])
            from trcc.legacy.adapters.render.qt import QtRenderer
            ImageService.set_renderer(QtRenderer())

    @pytest.mark.usefixtures("_renderer")
    def test_resumes_even_on_error(self):
        """GUI resumes even if benchmarks crash."""
        mock_svc = MagicMock()
        from trcc.legacy.core.models import FBL_PROFILES
        profile = FBL_PROFILES[100]
        lcd_dev = MagicMock()
        lcd_dev.implementation = 'scsi'
        lcd_dev.resolution = profile.resolution
        lcd_dev.encoding_params = ('scsi', profile.resolution, 100, not profile.jpeg)
        mock_svc.devices = [lcd_dev]

        mock_factory = MagicMock()
        mock_factory.get_protocol.side_effect = RuntimeError("USB exploded")

        with patch(_DS, return_value=mock_svc), \
             patch("trcc.legacy.services.perf._ipc_pause", return_value=True), \
             patch("trcc.legacy.services.perf._ipc_resume") as m_resume:
            from trcc.legacy.services.perf import run_device_benchmarks
            with pytest.raises(RuntimeError, match="USB exploded"):
                run_device_benchmarks(
                    detect_fn=MagicMock(),
                    get_protocol=mock_factory.get_protocol,
                    get_protocol_info=mock_factory.get_protocol_info,
                    probe_led_fn=MagicMock(),
                )

        m_resume.assert_called_once()
