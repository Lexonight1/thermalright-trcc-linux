"""Performance report domain object — pure data + formatting, zero deps."""
from __future__ import annotations

from dataclasses import dataclass, field


def _neg_actual(entry: PerfEntry) -> float:
    """Sort key: descending by `actual` (slowest / largest first)."""
    return -entry.actual


@dataclass
class PerfEntry:
    """Single performance measurement."""
    label: str
    actual: float
    limit: float

    @property
    def headroom_pct(self) -> float:
        return (1 - self.actual / self.limit) * 100 if self.limit > 0 else 0

    @property
    def passed(self) -> bool:
        return self.actual < self.limit


@dataclass
class PerfReport:
    """Collects performance measurements and formats a Valgrind-style report."""

    cpu: list[PerfEntry] = field(default_factory=list)
    mem: list[PerfEntry] = field(default_factory=list)
    scale: list[PerfEntry] = field(default_factory=list)
    device: list[PerfEntry] = field(default_factory=list)
    failed: int = 0
    total: int = 0

    def record_cpu(self, label: str, actual_s: float, limit_s: float) -> None:
        self.cpu.append(PerfEntry(label, actual_s, limit_s))

    def record_mem(self, label: str, actual_bytes: float, limit_bytes: float) -> None:
        self.mem.append(PerfEntry(label, actual_bytes, limit_bytes))

    def record_scale(self, label: str, ratio: float, limit: float) -> None:
        self.scale.append(PerfEntry(label, ratio, limit))

    def record_device(self, label: str, actual: float, limit: float) -> None:
        self.device.append(PerfEntry(label, actual, limit))

    @property
    def has_data(self) -> bool:
        return bool(self.cpu or self.mem or self.scale or self.device)

    @property
    def all_passed(self) -> bool:
        entries = self.cpu + self.mem + self.scale + self.device
        return all(e.passed for e in entries)

    def to_dict(self) -> dict:
        """Serialize for API JSON response."""
        def _entry(e: PerfEntry) -> dict:
            return {
                "label": e.label,
                "actual": e.actual,
                "limit": e.limit,
                "headroom_pct": round(e.headroom_pct, 1),
                "passed": e.passed,
            }
        return {
            "cpu": [_entry(e) for e in sorted(self.cpu, key=_neg_actual)],
            "memory": [_entry(e) for e in sorted(self.mem, key=_neg_actual)],
            "scaling": [_entry(e) for e in sorted(self.scale, key=_neg_actual)],
            "device": [_entry(e) for e in sorted(self.device, key=_neg_actual)],
            "summary": {
                "total": len(self.cpu) + len(self.mem) + len(self.scale) + len(self.device),
                "cpu_count": len(self.cpu),
                "memory_count": len(self.mem),
                "scaling_count": len(self.scale),
                "device_count": len(self.device),
                "all_passed": self.all_passed,
            },
        }

    def format_report(self) -> list[str]:
        """Format as Valgrind-style terminal report."""
        lines: list[str] = []
        w = 78

        lines.append("=" * w)
        lines.append("TRCC PERFORMANCE REPORT")
        lines.append("=" * w)

        if self.cpu:
            lines.append("")
            lines.append("-" * w)
            lines.append(f"{'CPU TIME':^{w}}")
            lines.append("-" * w)
            lines.append(
                f"  {'Test':<44} {'Actual':>10} {'Limit':>10} {'Headroom':>10}")
            lines.append(
                f"  {'----':<44} {'------':>10} {'-----':>10} {'-------':>10}")
            for e in sorted(self.cpu, key=_neg_actual):
                a_ms = e.actual * 1000
                l_ms = e.limit * 1000
                a_str = (f"{a_ms:.2f} ms" if a_ms >= 0.1
                         else f"{a_ms * 1000:.0f} us")
                l_str = (f"{l_ms:.2f} ms" if l_ms >= 0.1
                         else f"{l_ms * 1000:.0f} us")
                bar = _bar(e.actual, e.limit)
                lines.append(
                    f"  {e.label:<44} {a_str:>10} {l_str:>10}"
                    f" {e.headroom_pct:>7.0f}%  {bar}")

        if self.mem:
            lines.append("")
            lines.append("-" * w)
            lines.append(f"{'MEMORY':^{w}}")
            lines.append("-" * w)
            lines.append(
                f"  {'Test':<44} {'Growth':>10} {'Limit':>10} {'Headroom':>10}")
            lines.append(
                f"  {'----':<44} {'------':>10} {'-----':>10} {'-------':>10}")
            for e in sorted(self.mem, key=_neg_actual):
                a_str = _fmt_bytes(int(e.actual))
                l_str = _fmt_bytes(int(e.limit))
                bar = _bar(e.actual, e.limit)
                lines.append(
                    f"  {e.label:<44} {a_str:>10} {l_str:>10}"
                    f" {e.headroom_pct:>7.0f}%  {bar}")

        if self.scale:
            lines.append("")
            lines.append("-" * w)
            lines.append(f"{'SCALING (lower ratio = more linear)':^{w}}")
            lines.append("-" * w)
            lines.append(
                f"  {'Test':<44} {'Ratio':>10} {'Limit':>10} {'Headroom':>10}")
            lines.append(
                f"  {'----':<44} {'------':>10} {'-----':>10} {'-------':>10}")
            for e in sorted(self.scale, key=_neg_actual):
                bar = _bar(e.actual, e.limit)
                lines.append(
                    f"  {e.label:<44} {e.actual:>9.1f}x {e.limit:>9.1f}x"
                    f" {e.headroom_pct:>7.0f}%  {bar}")

        if self.device:
            lines.append("")
            lines.append("-" * w)
            lines.append(f"{'DEVICE I/O':^{w}}")
            lines.append("-" * w)
            lines.append(
                f"  {'Test':<44} {'Actual':>10} {'Limit':>10} {'Headroom':>10}")
            lines.append(
                f"  {'----':<44} {'------':>10} {'-----':>10} {'-------':>10}")
            for e in sorted(self.device, key=_neg_actual):
                a_ms = e.actual * 1000
                l_ms = e.limit * 1000
                a_str = (f"{a_ms:.1f} ms" if a_ms >= 1.0
                         else f"{a_ms * 1000:.0f} us")
                l_str = (f"{l_ms:.1f} ms" if l_ms >= 1.0
                         else f"{l_ms * 1000:.0f} us")
                bar = _bar(e.actual, e.limit)
                lines.append(
                    f"  {e.label:<44} {a_str:>10} {l_str:>10}"
                    f" {e.headroom_pct:>7.0f}%  {bar}")

        lines.append("")
        lines.append("=" * w)
        total = len(self.cpu) + len(self.mem) + len(self.scale) + len(self.device)
        parts = []
        if self.cpu:
            parts.append(f"{len(self.cpu)} cpu")
        if self.mem:
            parts.append(f"{len(self.mem)} memory")
        if self.scale:
            parts.append(f"{len(self.scale)} scaling")
        if self.device:
            parts.append(f"{len(self.device)} device")
        status = "ALL PASSED" if self.all_passed else "FAILURES DETECTED"
        lines.append(
            f"  {total} measurements: {', '.join(parts)} — {status}")
        lines.append("=" * w)
        return lines


def _bar(actual: float, limit: float, width: int = 10) -> str:
    """Render a usage bar: [####------]."""
    if limit <= 0:
        return ""
    fill = min(int(actual / limit * width), width)
    return "[" + "#" * fill + "-" * (width - fill) + "]"


def _fmt_bytes(n: int) -> str:
    """Format bytes as human-readable."""
    if abs(n) < 1024:
        return f"{n} B"
    if abs(n) < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 * 1024):.2f} MB"
