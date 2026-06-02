"""macOS ``powermetrics`` sampler — CPU/GPU/ANE power + GPU usage/clock.

On Apple Silicon, SMC alone is not enough: CPU package power, GPU
usage / clock / power, ANE power, and combined SoC power are only
exposed through Apple's ``powermetrics`` tool, which requires root.

We don't fork ``powermetrics`` from the unprivileged TRCC process.
Legacy ships a tiny LaunchDaemon helper (``trcc-powermetrics``) that
opens a Unix-domain socket and returns one plist-formatted snapshot
per request — same approach next/ uses here.

  ┌──────────────────────────────────────────────────────────────┐
  │ powermetrics-helper (root)                                   │
  │   socket: /var/run/trcc-powermetrics.sock                    │
  │   reply: <TRC1 magic><status u32><body_len u32><plist XML>   │
  └──────────────────────────────────────────────────────────────┘
              │
              ▼  (snapshot owned by _PowermetricsSnapshot)
     PowermetricsCpu.power() / .freq()
     PowermetricsGpu.usage() / .clock() / .power()
              ▲
              │  (lazily refreshes when stale > TTL)

A single ``_PowermetricsSnapshot`` is shared between the CPU + GPU
sources so a metrics tick costs one helper round-trip per TTL window
(not one round-trip per source).

This is the legacy-proven path — every released macOS build ships
this helper.  Per CLAUDE.md macOS protocol, the strategy chain
gracefully no-ops when the helper isn't installed: ``_default_fetcher``
returns None, snapshot values stay empty, every read returns None,
and the chain falls through to the next source.
"""
from __future__ import annotations

import logging
import os
import plistlib
import re
import socket
import subprocess
import time
from collections.abc import Callable
from typing import Any

from ...core.ports import CpuSource, GpuSource

log = logging.getLogger(__name__)


# ── IPC (legacy ``powermetrics_ipc.py``) ─────────────────────────────

_DEFAULT_SOCKET = "/var/run/trcc-powermetrics.sock"
_MAGIC = b"TRC1"
_MAX_BODY = 2_000_000
_MAX_SAMPLERS_LEN = 256
_SAFE_SAMPLERS = re.compile(r"^[a-zA-Z0-9_,]+$")

_DEFAULT_SAMPLERS = "cpu_power,gpu_power"
_SNAPSHOT_TTL_SECONDS = 1.0


def _powermetrics_socket_path() -> str | None:
    """Helper socket path, or None when disabled via ``TRCC_POWERMETRICS_SOCKET=``."""
    raw = os.environ.get("TRCC_POWERMETRICS_SOCKET", _DEFAULT_SOCKET)
    if not raw.strip():
        return None
    return raw


def _samplers_allowed(s: str) -> bool:
    if not s or len(s) > _MAX_SAMPLERS_LEN:
        return False
    return bool(_SAFE_SAMPLERS.fullmatch(s))


def _read_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise OSError("short read from powermetrics helper")
        buf.extend(chunk)
    return bytes(buf)


def fetch_via_helper(samplers: str, *, timeout: float = 12.0) -> bytes | None:
    """One plist snapshot from the privileged helper, or None on failure."""
    log.info("fetch_via_helper: samplers=%s timeout=%s", samplers, timeout)
    path = _powermetrics_socket_path()
    if path is None:
        log.debug("powermetrics: helper disabled (TRCC_POWERMETRICS_SOCKET empty)")
        return None
    if not _samplers_allowed(samplers):
        log.warning("powermetrics: rejected unsafe samplers %r", samplers)
        return None
    payload = (samplers + "\n").encode("utf-8")
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(path)
            sock.sendall(payload)
            hdr = _read_exact(sock, 8)
            if len(hdr) < 8 or hdr[:4] != _MAGIC:
                log.debug("powermetrics: bad magic / short header")
                return None
            status = int.from_bytes(hdr[4:8], "big")
            body_len = int.from_bytes(_read_exact(sock, 4), "big")
            if body_len > _MAX_BODY:
                log.warning("powermetrics: body too large (%d)", body_len)
                return None
            body = _read_exact(sock, body_len) if body_len else b""
            if status != 0:
                msg = body.decode("utf-8", errors="replace")[:500]
                log.warning("powermetrics helper error status=%s: %s", status, msg)
                return None
            return body
    except OSError as e:
        log.debug("powermetrics helper unavailable: %s", e)
        return None


def fetch_via_subprocess(samplers: str, *, timeout: float = 12.0) -> bytes | None:
    """Fallback: run ``powermetrics`` directly (requires the process to be root)."""
    log.info("fetch_via_subprocess: samplers=%s timeout=%s", samplers, timeout)
    if not _samplers_allowed(samplers):
        log.warning("powermetrics subprocess: unsafe samplers rejected")
        return None
    try:
        result = subprocess.run(
            [
                "powermetrics",
                "-n", "1",
                "-i", "100",
                "-f", "plist",
                "--samplers", samplers,
            ],
            capture_output=True, timeout=timeout, check=False,
        )
    except (OSError, subprocess.SubprocessError) as e:
        log.debug("powermetrics subprocess failed: %s", e)
        return None
    if result.returncode != 0:
        log.debug("powermetrics subprocess exited %d: %s",
                  result.returncode,
                  result.stderr[:200].decode("utf-8", errors="replace"))
        return None
    return result.stdout


# ── Plist parsing (legacy ``powermetrics_plist.py``) ─────────────────


def _mw_to_w(mw: Any) -> float | None:
    if isinstance(mw, bool) or not isinstance(mw, (int, float)):
        return None
    v = float(mw) / 1000.0
    if v != v or v < 0.0 or v > 4000.0:
        return None
    return v


def _freq_hz_to_mhz(hz: Any) -> float | None:
    if isinstance(hz, bool) or not isinstance(hz, (int, float)):
        return None
    f = float(hz)
    if f <= 0:
        return None
    # CPU clusters report Hz (~3e9); GPU block uses MHz (~600-3000).
    mhz = f / 1e6 if f > 1e6 else f
    if mhz < 1.0 or mhz > 8000.0:
        return None
    return mhz


def _gpu_busy_percent(gpu: dict[str, Any]) -> float | None:
    """Sum of ``used_ratio`` across all GPU DVFM states → 0..100 utilization."""
    states = gpu.get("dvfm_states")
    if not isinstance(states, list) or not states:
        return None
    total = 0.0
    for st in states:
        if isinstance(st, dict):
            r = st.get("used_ratio")
            if isinstance(r, (int, float)) and not isinstance(r, bool):
                total += float(r)
    pct = 100.0 * total
    if pct < 0.0 or pct > 100.0:
        return None
    return pct


def _max_cpu_mhz_from_processor(proc: dict[str, Any]) -> float | None:
    clusters = proc.get("clusters")
    if not isinstance(clusters, list):
        return None
    mhz_vals: list[float] = []
    for cl in clusters:
        if not isinstance(cl, dict):
            continue
        cpus = cl.get("cpus")
        if not isinstance(cpus, list):
            continue
        for cpu in cpus:
            if not isinstance(cpu, dict):
                continue
            m = _freq_hz_to_mhz(cpu.get("freq_hz"))
            if m is not None:
                mhz_vals.append(m)
    return max(mhz_vals) if mhz_vals else None


def parse_powermetrics_plist(data: bytes) -> dict[str, float] | None:
    """Decode one plist snapshot → flat dict of metric → value.

    Keys (when present): ``cpu_power``, ``gpu_power``, ``ane_power``,
    ``combined_power`` (watts); ``gpu_busy`` (percent),
    ``gpu_clock`` (MHz), ``cpu_freq`` (MHz).
    """
    log.debug("parse_powermetrics_plist: bytes=%d", len(data))
    chunk = data.split(b"\x00", 1)[0].strip()
    if not chunk.startswith(b"<?xml") and not chunk.startswith(b"<plist"):
        return None
    try:
        root = plistlib.loads(chunk)
    except Exception:
        log.debug("powermetrics plist parse failed", exc_info=True)
        return None
    if not isinstance(root, dict):
        return None

    out: dict[str, float] = {}
    gpu = root.get("gpu")
    proc = root.get("processor")

    if isinstance(gpu, dict):
        busy = _gpu_busy_percent(gpu)
        if busy is not None:
            out["gpu_busy"] = busy
        gclk = _freq_hz_to_mhz(gpu.get("freq_hz"))
        if gclk is not None:
            out["gpu_clock"] = gclk

    if isinstance(proc, dict):
        for key in ("cpu_power", "gpu_power", "ane_power", "combined_power"):
            w = _mw_to_w(proc.get(key))
            if w is not None:
                out[key] = w
        mhz = _max_cpu_mhz_from_processor(proc)
        if mhz is not None:
            out["cpu_freq"] = mhz

    return out if out else None


# ── Shared snapshot ──────────────────────────────────────────────────


def _geteuid_or_minus1() -> int:
    """``os.geteuid`` is Unix-only; return -1 (≠ 0) on Windows so we don't
    accidentally fall through to the subprocess path on non-macOS."""
    return getattr(os, "geteuid", lambda: -1)()


def _default_fetcher(samplers: str) -> bytes | None:
    """Helper-first, then root-only subprocess as fallback."""
    data = fetch_via_helper(samplers)
    if data is not None:
        return data
    if _geteuid_or_minus1() != 0:
        # Subprocess only works as root; skip silently for unprivileged users
        # so we don't spam the log every metrics tick.
        return None
    return fetch_via_subprocess(samplers)


class _PowermetricsSnapshot:
    """Cached parsed result, refreshed on demand with a TTL.

    Shared by ``PowermetricsCpu`` + ``PowermetricsGpu`` so a metrics
    tick that reads both pays one helper round-trip, not two.

    DI seam: ``fetcher`` is a ``(samplers) → bytes | None`` callable.
    Production binds it to ``_default_fetcher``; tests inject canned
    plist bytes to exercise the parsing + caching from Linux.
    """

    def __init__(
        self,
        *,
        fetcher: Callable[[str], bytes | None] | None = None,
        samplers: str = _DEFAULT_SAMPLERS,
        ttl_seconds: float = _SNAPSHOT_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._fetch = fetcher if fetcher is not None else _default_fetcher
        self._samplers = samplers
        self._ttl = ttl_seconds
        self._clock = clock
        self._last_at: float = -1.0
        self._values: dict[str, float] = {}
        log.debug("PowermetricsSnapshot: samplers=%r ttl=%.1fs",
                  samplers, ttl_seconds)

    def get(self, key: str) -> float | None:
        """Return the cached value for ``key``, refreshing if stale."""
        self._refresh_if_stale()
        return self._values.get(key)

    def _refresh_if_stale(self) -> None:
        now = self._clock()
        if self._last_at >= 0 and (now - self._last_at) < self._ttl:
            return
        raw = self._fetch(self._samplers)
        self._last_at = now
        if raw is None:
            log.debug("PowermetricsSnapshot.refresh: fetcher returned None")
            self._values = {}
            return
        parsed = parse_powermetrics_plist(raw)
        if parsed is None:
            log.debug("PowermetricsSnapshot.refresh: plist parse returned None")
            self._values = {}
            return
        log.debug("PowermetricsSnapshot.refresh: %d metrics", len(parsed))
        self._values = parsed


# ── Sources ──────────────────────────────────────────────────────────


class PowermetricsCpu(CpuSource):
    """Apple Silicon CPU power + frequency via the powermetrics helper.

    Returns None for temp / usage — the chain falls through to HID
    for temp and psutil for usage.  When the helper isn't installed
    (unprivileged process + no socket), every method returns None
    and the chain naturally falls through to the next source.
    """

    def __init__(self, snapshot: _PowermetricsSnapshot) -> None:
        self._snap = snapshot

    @property
    def name(self) -> str:
        return "powermetrics (CPU)"

    def temp(self) -> float | None:
        return None

    def usage(self) -> float | None:
        return None

    def freq(self) -> float | None:
        return self._snap.get("cpu_freq")

    def power(self) -> float | None:
        return self._snap.get("cpu_power")


class PowermetricsGpu(GpuSource):
    """Apple Silicon integrated GPU.

    Vendor key ``apple:0`` collides with the HID GPU's key so the
    aggregator's vendor-key dedup picks one canonical row per GPU.
    Returns None for temp (HID covers that), fan (no fans on the
    GPU itself), and vram (unified memory — already reported by
    ``MemorySource``).
    """

    def __init__(
        self,
        snapshot: _PowermetricsSnapshot,
        *,
        model_name: str = "Apple GPU",
    ) -> None:
        self._snap = snapshot
        self._name = model_name

    @property
    def key(self) -> str:
        return "apple:0"

    @property
    def name(self) -> str:
        return self._name

    @property
    def is_discrete(self) -> bool:
        return False

    def temp(self) -> float | None:
        return None

    def usage(self) -> float | None:
        return self._snap.get("gpu_busy")

    def clock(self) -> float | None:
        return self._snap.get("gpu_clock")

    def power(self) -> float | None:
        return self._snap.get("gpu_power")

    def fan(self) -> float | None:
        return None

    def vram_used(self) -> float | None:
        return None

    def vram_total(self) -> float | None:
        return None
