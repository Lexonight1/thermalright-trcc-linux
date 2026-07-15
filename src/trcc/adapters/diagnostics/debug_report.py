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

import contextlib
import json
import logging
import os
import platform as py_platform
import sys
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ...core.errors import HandshakeError, TransportError
from ...core.models import Kind, ProductInfo, Wire
from ...core.ports import Platform
from ...core.registry import find_product
from ..device import DeviceFactory
from ..infra.logging import tail_log
from .health import HealthReport, run_health_checks
from .install import InstallInfo, collect_install_info

log = logging.getLogger(__name__)

# Marker for the connect-time handshake line ("BulkLcd handshake OK: PM=…",
# "HidLcd handshake OK: …", etc.).  Scraped from the FULL log as a fallback
# when a live probe can't run.
_HANDSHAKE_LOG_MARKER = "handshake OK"


@dataclass(frozen=True, slots=True)
class DebugReport:
    """Structured debug report — render via ``render_text`` for issue paste."""
    timestamp: str
    platform_info: dict[str, str]
    paths: dict[str, str]
    install: InstallInfo | None = None
    devices: list[dict[str, str]] = field(default_factory=list)
    devices_error: str = ""
    sensors: list[dict[str, str]] = field(default_factory=list)
    sensors_error: str = ""
    powercap: list[dict[str, str]] = field(default_factory=list)
    settings_json: str = ""
    settings_error: str = ""
    health: HealthReport = field(default_factory=HealthReport)
    log_tail: list[str] = field(default_factory=list)
    handshake_log: list[str] = field(default_factory=list)

    def render_text(self) -> str:
        """Render the report as a single string for clipboard/file output."""
        log.info("render_text: called")
        sections: list[str] = []
        sections.append(_header(self.timestamp))
        # Install first: every other section is worthless if this is not the
        # trcc the reporter thinks they are running.
        sections.append(_render_install(self.install))
        sections.append(_render_kv("Platform", self.platform_info))
        sections.append(_render_kv("Paths", self.paths))
        sections.append(_render_devices(self.devices, self.devices_error))
        handshake = _render_handshake_log(self.handshake_log)
        if handshake:
            sections.append(handshake)
        sections.append(_render_sensors(self.sensors, self.sensors_error))
        sections.append(_render_powercap(self.powercap))
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
    log.info("build_debug_report: gathering sections (log_tail=%d)",
             log_tail_lines)
    timestamp = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    # Best-effort like every other collector: a report from a broken install is
    # exactly when this matters, so it must degrade to "(unavailable)" rather
    # than abort the report that would have explained the breakage.
    try:
        install = collect_install_info()
    except Exception as e:
        log.exception("build_debug_report: install info failed: %s", e)
        install = None

    info = _collect_platform_info(platform)
    path_table = _collect_paths(platform)
    devices, devices_err = _collect_devices(platform)
    sensors, sensors_err = _collect_sensors(platform)
    powercap = _collect_powercap()
    settings_text, settings_err = _read_settings_file(
        settings_path or platform.paths().config_dir() / "config.json",
    )
    health = run_health_checks(platform)
    log_path = platform.paths().log_file()
    log_lines = tail_log(log_path, n_lines=log_tail_lines)
    handshake_log = _scrape_handshake_lines(log_path)

    return DebugReport(
        timestamp=timestamp,
        platform_info=info,
        paths=path_table,
        install=install,
        devices=devices,
        devices_error=devices_err,
        sensors=sensors,
        sensors_error=sensors_err,
        powercap=powercap,
        settings_json=settings_text,
        settings_error=settings_err,
        health=health,
        log_tail=log_lines,
        handshake_log=handshake_log,
    )


def write_debug_report(report: DebugReport, output_path: Path) -> Path:
    """Render *report* to *output_path*.  Returns the path."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(report.render_text(), encoding="utf-8")
    log.info("write_debug_report: wrote %s", output_path)
    return output_path


# =========================================================================
# Collectors — each one tolerates failure rather than aborting the report
# =========================================================================


def _collect_platform_info(platform: Platform) -> dict[str, str]:
    log.debug("_collect_platform_info: called")
    # Version / path / installer live in the Install section, which owns the
    # "which trcc is this?" question in one place — see _render_install.
    return {
        "distro": platform.distro_name(),
        "python_impl": py_platform.python_implementation(),
        "machine": py_platform.machine(),
        "system": py_platform.system(),
        "release": py_platform.release(),
    }


def _collect_paths(platform: Platform) -> dict[str, str]:
    log.debug("_collect_paths: called")
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
    log.debug("_collect_devices: called")
    try:
        infos = platform.scan_devices()
    except (OSError, RuntimeError) as e:
        return [], f"{type(e).__name__}: {e}"
    rows: list[dict[str, str]] = []
    for info in infos:
        product = find_product(info.vid, info.pid)
        row = {
            "key": info.key,
            "vid": f"{info.vid:04x}",
            "pid": f"{info.pid:04x}",
            "product": product.product if product else "(unregistered)",
            "vendor": product.vendor if product else "(unregistered)",
            "wire": product.wire.value if product else "(unknown)",
        }
        # Capture the live handshake so the report always carries the exact
        # PM / SUB / fbl / resolution — the bytes that decide a panel's
        # geometry.  Without this the report only has them if the connect-time
        # log line survived in the tail, which on a long session it never does.
        if product is not None:
            handshake = _probe_handshake(platform, product)
            if handshake is not None:
                row.update({f"hs_{k}": v for k, v in handshake.items()})
        rows.append(row)
    return rows, ""


def _probe_handshake(
    platform: Platform, product: ProductInfo,
) -> dict[str, str] | None:
    """Connect the device, capture its handshake bytes, then disconnect.

    The device's PM / SUB / fbl / resolution decide its whole geometry, yet the
    report otherwise loses them: the handshake fires once at connect (startup)
    and its log line scrolls out of the tail on any real session.  A fresh live
    probe captures the exact bytes every time — this is what lets us map a new
    panel (e.g. the shared ``87ad:70db`` controller) without asking the reporter
    for a byte their report can't produce.

    Returns None — the caller falls back to the log scrape — when the device
    can't be probed: an LED segment display (no frame handshake), the GUI /
    daemon already holds the device (busy), a permission error, or a handshake
    failure.  A diagnostic must never abort the report, so every failure is
    swallowed into the fallback.
    """
    key = f"{product.vid:04x}:{product.pid:04x}"
    log.info("_probe_handshake: %s wire=%s", key, product.wire.value)
    if product.kind is not Kind.LCD:
        log.info("_probe_handshake: %s is %s, not LCD — no frame handshake",
                 key, product.kind.value)
        return None
    device = None
    try:
        cls = DeviceFactory.for_wire(product.wire)
        if product.wire is Wire.SCSI:
            transport = platform.open_scsi(product.vid, product.pid)
        else:
            transport = platform.open_bulk(product.vid, product.pid)
        device = cls(product, transport)
        result = device.connect()
    except (OSError, HandshakeError, TransportError, RuntimeError) as e:
        log.info("_probe_handshake: %s probe failed (%s: %s) — scraping log",
                 key, type(e).__name__, e)
        return None
    finally:
        if device is not None:
            with contextlib.suppress(OSError, TransportError, RuntimeError):
                device.disconnect()
    handshake = {
        "pm": str(result.pm_byte),
        "sub": str(result.sub_byte),
        "fbl": str(result.fbl) if result.fbl is not None else "?",
        "resolution": f"{result.resolution[0]}x{result.resolution[1]}",
        "raw": result.raw_response[:64].hex(),
    }
    log.info("_probe_handshake: %s → PM=%s SUB=%s fbl=%s resolution=%s",
             key, handshake["pm"], handshake["sub"], handshake["fbl"],
             handshake["resolution"])
    return handshake


def _scrape_handshake_lines(log_path: Path, keep: int = 6) -> list[str]:
    """The most recent ``handshake OK`` lines from the FULL log (not the tail).

    The fallback for :func:`_probe_handshake`: when a live probe can't run
    (the GUI holds the device), the connect-time handshake line may still be
    somewhere in the log — but earlier than the tail window.  Scans the whole
    file keeping only the last *keep* matches, so it stays memory-bounded even
    on a large log.
    """
    log.debug("_scrape_handshake_lines: %s (keep=%d)", log_path, keep)
    if not log_path.is_file():
        return []
    recent: deque[str] = deque(maxlen=keep)
    try:
        with log_path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if _HANDSHAKE_LOG_MARKER in line:
                    recent.append(line.rstrip("\n"))
    except OSError as e:
        log.debug("_scrape_handshake_lines: read failed: %s", e)
        return []
    log.debug("_scrape_handshake_lines: found %d line(s)", len(recent))
    return list(recent)


def _collect_sensors(
    platform: Platform,
) -> tuple[list[dict[str, str]], str]:
    log.debug("_collect_sensors: called")
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


def _collect_powercap() -> list[dict[str, str]]:
    """Probe the powercap RAPL nodes that CPU package power comes from (#194).

    Answers the two questions a bare ``cpu:power = —`` can't: does the
    ``intel-rapl:*`` node exist at all (``intel_rapl_msr`` loaded?), and is
    its ``energy_uj`` counter readable by us (root-only since CVE-2020-8694?).
    Linux-only; other OSes get no rows (the section renders as N/A).
    """
    log.debug("_collect_powercap: called")
    if sys.platform != "linux":
        return []
    root = Path("/sys/class/powercap")
    rows: list[dict[str, str]] = []
    try:
        domains = sorted(root.glob("intel-rapl:*"))
    except OSError as e:
        log.debug("_collect_powercap: glob failed: %s", e)
        return []
    for domain in domains:
        # Top-level package domains only (intel-rapl:N), not subdomains.
        if ":" in domain.name.split("intel-rapl:")[1]:
            continue
        try:
            name = (domain / "name").read_text(encoding="utf-8").strip() \
                if (domain / "name").is_file() else "?"
            energy = domain / "energy_uj"
            if not energy.exists():
                readable = "no energy_uj"
            else:
                mode = energy.stat().st_mode & 0o777
                ok = os.access(energy, os.R_OK)
                readable = f"{'readable' if ok else 'ROOT-ONLY'} ({mode:#o})"
        except OSError as e:
            # A diagnostic must never abort the report — note the probe error.
            name, readable = "?", f"probe error: {type(e).__name__}"
        rows.append({"domain": domain.name, "name": name, "energy_uj": readable})
    return rows


def _read_settings_file(path: Path) -> tuple[str, str]:
    log.debug("_read_settings_file: path=%s", path)
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


def _render_install(info: InstallInfo | None) -> str:
    """Which trcc is running — and loud warnings when it isn't the expected one.

    The warnings are spelled out in full because the reporter reading them is
    the person who has to act, and neither of the two failures is guessable
    from the outside: a stale cache and a duplicate binary both present as
    "I upgraded and nothing changed".
    """
    if info is None:
        return "## Install\n  (unavailable)"
    rows = {
        "version": info.version,
        "installed_by": info.installer,
        "module_path": str(info.module_path or "unknown"),
        "interpreter": info.interpreter,
        "python": info.python,
    }
    lines = [f"  {k:18}  {v}" for k, v in rows.items()]
    for exe in info.executables:
        lines.append(f"  {'on PATH':18}  {exe.path}  ->  {exe.interpreter}")
    if info.bytecode_stale:
        lines.append("")
        lines.append(f"  !! STALE BYTECODE — running {info.version} but the source "
                     f"says {info.source_version}.")
        lines.append("  !! Python is serving a cached .pyc that no longer matches "
                     "the code on disk.")
        lines.append("  !! Fix: delete __pycache__ dirs, or reinstall.")
    if info.duplicates:
        lines.append("")
        lines.append(f"  !! {len(info.executables)} trcc found on PATH — an upgrade "
                     "may land on one")
        lines.append("  !! while a different one keeps running. The first listed "
                     "above is what runs.")
    return "## Install\n" + "\n".join(lines)


def _render_devices(rows: list[dict[str, str]], error: str) -> str:
    if error:
        return f"## Devices\n  Scan failed: {error}"
    if not rows:
        return "## Devices\n  No supported devices detected on USB"
    lines: list[str] = []
    for r in rows:
        lines.append(f"  {r['key']:14}  {r['product']:30}  wire={r['wire']}")
        if "hs_resolution" in r:
            lines.append(
                f"    handshake: PM={r['hs_pm']} SUB={r['hs_sub']} "
                f"fbl={r['hs_fbl']} resolution={r['hs_resolution']}"
            )
            lines.append(f"    raw[0:64]:  {r['hs_raw']}")
        else:
            lines.append(
                "    handshake: (live probe unavailable — device busy or LED; "
                "see Handshake-from-log below)"
            )
    return f"## Devices ({len(rows)})\n" + "\n".join(lines)


def _render_handshake_log(lines: list[str]) -> str:
    """The scraped-from-log handshake fallback section (omitted when empty)."""
    if not lines:
        return ""
    body = "\n".join(f"  {line}" for line in lines)
    return f"## Handshake (from log — fallback)\n{body}"


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


def _render_powercap(rows: list[dict[str, str]]) -> str:
    if not rows:
        return ("## CPU power (RAPL)\n  No intel-rapl powercap domains — "
                "intel_rapl_msr not loaded (or N/A on this OS).\n"
                "  Run `trcc setup` to load it + grant read access (#194).")
    body = "\n".join(
        f"  {r['domain']:16}  {r['name']:12}  energy_uj: {r['energy_uj']}"
        for r in rows
    )
    return f"## CPU power (RAPL, {len(rows)} package domain(s))\n{body}"


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
