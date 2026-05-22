"""Debug report bundle — text dump a reporter pastes into a GitHub issue.

Replaces legacy ``DebugReport`` (lines 1343+ of diagnostics.py) with a
focused, copy-paste-friendly format:

* platform identity (distro, Python, install method)
* path table (config / data / log / user content)
* detected USB devices (vid:pid + product names from registry)
* sensor enumeration snapshot (sensor_id + label + current value)
* persisted Settings (config.json contents)
* health check report
* log tail (last 1000 lines)

All sections degrade gracefully — if devices can't enumerate, that
section just says "scan failed: <reason>" rather than aborting the
whole report.  Reporters always get *something* useful to attach.
"""
from __future__ import annotations

import json
import logging
import platform as py_platform
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ...core.ports import Platform
from ...core.registry import find_product
from ..infra.logging import tail_log
from .health import HealthReport, run_health_checks

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DebugReport:
    """Structured debug report — render via ``render_text`` for issue paste."""
    timestamp: str
    platform_info: dict[str, str]
    paths: dict[str, str]
    devices: list[dict[str, str]] = field(default_factory=list)
    devices_error: str = ""
    sensors: list[dict[str, str]] = field(default_factory=list)
    sensors_error: str = ""
    settings_json: str = ""
    settings_error: str = ""
    health: HealthReport = field(default_factory=HealthReport)
    log_tail: list[str] = field(default_factory=list)

    def render_text(self) -> str:
        """Render the report as a single string for clipboard/file output."""
        sections: list[str] = []
        sections.append(_header(self.timestamp))
        sections.append(_render_kv("Platform", self.platform_info))
        sections.append(_render_kv("Paths", self.paths))
        sections.append(_render_devices(self.devices, self.devices_error))
        sections.append(_render_sensors(self.sensors, self.sensors_error))
        sections.append(_render_settings(self.settings_json, self.settings_error))
        sections.append(_render_health(self.health))
        sections.append(_render_log_tail(self.log_tail))
        return "\n\n".join(sections) + "\n"


# =========================================================================
# Builder
# =========================================================================


def build_debug_report(
    platform: Platform,
    *,
    settings_path: Path | None = None,
    log_tail_lines: int = 1000,
) -> DebugReport:
    """Collect every section into a DebugReport."""
    timestamp = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    info = _collect_platform_info(platform)
    path_table = _collect_paths(platform)
    devices, devices_err = _collect_devices(platform)
    sensors, sensors_err = _collect_sensors(platform)
    settings_text, settings_err = _read_settings_file(
        settings_path or platform.paths().config_dir() / "config.json",
    )
    health = run_health_checks(platform)
    log_path = platform.paths().log_file()
    log_lines = tail_log(log_path, n_lines=log_tail_lines)

    return DebugReport(
        timestamp=timestamp,
        platform_info=info,
        paths=path_table,
        devices=devices,
        devices_error=devices_err,
        sensors=sensors,
        sensors_error=sensors_err,
        settings_json=settings_text,
        settings_error=settings_err,
        health=health,
        log_tail=log_lines,
    )


def write_debug_report(report: DebugReport, output_path: Path) -> Path:
    """Render *report* to *output_path*.  Returns the path."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report.render_text(), encoding="utf-8")
    return output_path


# =========================================================================
# Collectors — each one tolerates failure rather than aborting the report
# =========================================================================


def _collect_platform_info(platform: Platform) -> dict[str, str]:
    return {
        "distro": platform.distro_name(),
        "install_method": platform.install_method(),
        "python": f"{sys.version_info.major}.{sys.version_info.minor}."
                  f"{sys.version_info.micro}",
        "python_impl": py_platform.python_implementation(),
        "machine": py_platform.machine(),
        "system": py_platform.system(),
        "release": py_platform.release(),
    }


def _collect_paths(platform: Platform) -> dict[str, str]:
    paths = platform.paths()
    return {
        "config_dir": str(paths.config_dir()),
        "data_dir": str(paths.data_dir()),
        "user_content_dir": str(paths.user_content_dir()),
        "log_file": str(paths.log_file()),
    }


def _collect_devices(
    platform: Platform,
) -> tuple[list[dict[str, str]], str]:
    """Scan + enrich with product-registry metadata; return (rows, error)."""
    try:
        infos = platform.scan_devices()
    except (OSError, RuntimeError) as e:
        return [], f"{type(e).__name__}: {e}"
    rows: list[dict[str, str]] = []
    for info in infos:
        product = find_product(info.vid, info.pid)
        rows.append({
            "key": info.key,
            "vid": f"{info.vid:04x}",
            "pid": f"{info.pid:04x}",
            "product": product.product if product else "(unregistered)",
            "vendor": product.vendor if product else "(unregistered)",
            "wire": product.wire.value if product else "(unknown)",
        })
    return rows, ""


def _collect_sensors(
    platform: Platform,
) -> tuple[list[dict[str, str]], str]:
    try:
        enum = platform.sensors()
        descriptors = enum.discover()
        readings = enum.read_all()
    except (OSError, RuntimeError) as e:
        return [], f"{type(e).__name__}: {e}"
    rows: list[dict[str, str]] = []
    for desc in descriptors:
        value = readings.get(desc.sensor_id)
        rows.append({
            "sensor_id": desc.sensor_id,
            "label": desc.label,
            "category": desc.category,
            "value": f"{value:.2f} {desc.unit}" if value is not None else "—",
        })
    return rows, ""


def _read_settings_file(path: Path) -> tuple[str, str]:
    if not path.is_file():
        return "", f"No settings file at {path}"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        return "", f"Cannot read {path}: {e}"
    # Re-pretty-print so the report is consistent regardless of how the
    # file was written.  Fall back to raw text if it's not JSON.
    try:
        parsed = json.loads(text)
        return json.dumps(parsed, indent=2, ensure_ascii=False), ""
    except json.JSONDecodeError:
        return text, "(file is not valid JSON; showing raw text)"


# =========================================================================
# Renderers
# =========================================================================


def _header(timestamp: str) -> str:
    return (
        f"=========================================\n"
        f" TRCC debug report — {timestamp}\n"
        f"=========================================\n"
        f"\n"
        f"Paste this whole report into the GitHub issue.\n"
        f"Sections degrade gracefully — empty / errored sections are\n"
        f"normal when a feature isn't applicable on this OS / machine."
    )


def _render_kv(title: str, table: dict[str, str]) -> str:
    body = "\n".join(f"  {k:18}  {v}" for k, v in table.items())
    return f"## {title}\n{body}" if body else f"## {title}\n  (empty)"


def _render_devices(rows: list[dict[str, str]], error: str) -> str:
    if error:
        return f"## Devices\n  Scan failed: {error}"
    if not rows:
        return "## Devices\n  No supported devices detected on USB"
    body = "\n".join(
        f"  {r['key']:14}  {r['product']:30}  wire={r['wire']}"
        for r in rows
    )
    return f"## Devices ({len(rows)})\n{body}"


def _render_sensors(rows: list[dict[str, str]], error: str) -> str:
    if error:
        return f"## Sensors\n  Enumeration failed: {error}"
    if not rows:
        return "## Sensors\n  No sensors discovered"
    body = "\n".join(
        f"  {r['sensor_id']:28}  {r['value']:>14}  ({r['category']})"
        for r in rows
    )
    return f"## Sensors ({len(rows)})\n{body}"


def _render_settings(text: str, error: str) -> str:
    if error and not text:
        return f"## Settings\n  {error}"
    indented = "\n".join(f"  {line}" for line in text.splitlines())
    note = f"  ({error})\n" if error else ""
    return f"## Settings\n{note}{indented}"


def _render_health(report: HealthReport) -> str:
    if not report.checks:
        return "## Health\n  No checks ran"
    lines: list[str] = []
    for c in report.checks:
        lines.append(f"  [{c.severity:4}] {c.name:22}  {c.message}")
        if c.fix_hint and c.severity != "OK":
            lines.append(f"         hint: {c.fix_hint}")
    summary = (
        f"({report.fail_count} fail / {report.warn_count} warn / "
        f"{len(report.checks)} total)"
    )
    return f"## Health {summary}\n" + "\n".join(lines)


def _render_log_tail(lines: list[str]) -> str:
    if not lines:
        return "## Log tail\n  (no log file yet)"
    body = "\n".join(f"  {line}" for line in lines)
    return f"## Log tail ({len(lines)} lines)\n{body}"
