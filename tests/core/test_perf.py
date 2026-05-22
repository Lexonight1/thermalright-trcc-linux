"""Tests for trcc.core.perf — PerfReport domain object."""
from __future__ import annotations

from trcc.legacy.core.perf import PerfEntry, PerfReport, _bar, _fmt_bytes


class TestPerfEntry:
    """PerfEntry — headroom calculation and pass/fail logic."""

    def test_passed_under_limit(self):
        e = PerfEntry("test", 0.5, 1.0)
        assert e.passed is True

    def test_failed_over_limit(self):
        e = PerfEntry("test", 1.5, 1.0)
        assert e.passed is False

    def test_headroom_50_percent(self):
        e = PerfEntry("test", 0.5, 1.0)
        assert e.headroom_pct == 50.0

    def test_headroom_zero_limit(self):
        e = PerfEntry("test", 0.5, 0.0)
        assert e.headroom_pct == 0


class TestPerfReportDevice:
    """PerfReport device section — record, serialize, format."""

    def test_record_device(self):
        r = PerfReport()
        r.record_device("handshake", 0.5, 2.0)
        assert len(r.device) == 1
        assert r.device[0].label == "handshake"

    def test_has_data_with_device(self):
        r = PerfReport()
        assert r.has_data is False
        r.record_device("test", 1.0, 2.0)
        assert r.has_data is True

    def test_all_passed_includes_device(self):
        r = PerfReport()
        r.record_cpu("cpu_test", 0.001, 0.01)
        r.record_device("dev_test", 0.5, 2.0)
        assert r.all_passed is True

    def test_device_failure_fails_report(self):
        r = PerfReport()
        r.record_device("slow_handshake", 3.0, 2.0)
        assert r.all_passed is False

    def test_to_dict_includes_device(self):
        r = PerfReport()
        r.record_device("handshake", 0.5, 2.0)
        d = r.to_dict()
        assert "device" in d
        assert len(d["device"]) == 1
        assert d["device"][0]["label"] == "handshake"
        assert d["summary"]["device_count"] == 1
        assert d["summary"]["total"] == 1

    def test_to_dict_device_sorted_descending(self):
        r = PerfReport()
        r.record_device("fast", 0.1, 2.0)
        r.record_device("slow", 0.9, 2.0)
        d = r.to_dict()
        assert d["device"][0]["label"] == "slow"
        assert d["device"][1]["label"] == "fast"

    def test_format_report_includes_device_section(self):
        r = PerfReport()
        r.record_device("LCD handshake", 0.5, 2.0)
        lines = r.format_report()
        text = "\n".join(lines)
        assert "DEVICE I/O" in text
        assert "LCD handshake" in text

    def test_format_report_device_only(self):
        """Report with only device entries shows correct summary."""
        r = PerfReport()
        r.record_device("test", 0.1, 1.0)
        lines = r.format_report()
        text = "\n".join(lines)
        assert "1 device" in text
        assert "cpu" not in text.split("measurements:")[1].split("—")[0]

    def test_format_report_mixed(self):
        """Report with cpu + device shows both in summary."""
        r = PerfReport()
        r.record_cpu("cpu_test", 0.001, 0.01)
        r.record_device("dev_test", 0.5, 2.0)
        lines = r.format_report()
        text = "\n".join(lines)
        assert "1 cpu" in text
        assert "1 device" in text
        assert "2 measurements" in text


class TestHelpers:
    """_bar and _fmt_bytes formatting helpers."""

    def test_bar_half_fill(self):
        assert _bar(5.0, 10.0) == "[#####-----]"

    def test_bar_zero_limit(self):
        assert _bar(5.0, 0.0) == ""

    def test_bar_overflow(self):
        assert _bar(15.0, 10.0) == "[##########]"

    def test_fmt_bytes_small(self):
        assert _fmt_bytes(512) == "512 B"

    def test_fmt_bytes_kb(self):
        assert _fmt_bytes(2048) == "2.0 KB"

    def test_fmt_bytes_mb(self):
        assert _fmt_bytes(2 * 1024 * 1024) == "2.00 MB"
