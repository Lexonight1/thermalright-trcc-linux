"""Linux hwmon + DRM sysfs sensor sources.

hwmon (/sys/class/hwmon) exposes temperatures, fan speeds, voltages,
and power for CPUs, GPUs, motherboards, NVMe drives, etc.  The kernel
driver name identifies which device we're looking at:

    coretemp / k10temp / zenpower   → CPU package temperature
    amdgpu                          → AMD GPU temp/fan/power
    i915 / xe                       → Intel GPU temp
    nvme                            → NVMe SSD temp
    nct6xxx / it87*                 → motherboard super-IO (fans)

DRM sysfs (/sys/class/drm/cardN/device/) complements hwmon with GPU
utilization, clock, and VRAM info that hwmon doesn't expose.

All readings are normalized at the source:
    temp: millidegrees C → °C              power: μW → W
    clock: Hz → MHz                        memory: bytes → MB
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from ...core.logs import per_frame
from ...core.ports import CpuSource, DiskSource, DramSource, FanSource, GpuSource
from .psutil_sources import PsutilCpu

log = logging.getLogger(__name__)
#: Sensor readers run once per metrics tick — their records must never
#: be CONSTRUCTED at default verbosity.  See core.logs.per_frame.
frame_log = per_frame(__name__)


_HWMON_ROOT = Path("/sys/class/hwmon")
_DRM_ROOT = Path("/sys/class/drm")
_POWERCAP_ROOT = Path("/sys/class/powercap")

# When RAPL discovery comes up empty (driver not loaded, or energy_uj still
# root-only), re-scan at most this often so CPU power appears without an app
# restart once the user runs `trcc setup` to load the module / grant read.
_RAPL_REDISCOVER_INTERVAL_S = 30.0

_CPU_DRIVERS = ("coretemp", "k10temp", "zenpower")
_AMD_DRIVER = "amdgpu"
_NOUVEAU_DRIVER = "nouveau"
_INTEL_DRIVERS = ("i915", "xe")
_GPU_DRIVERS = (_AMD_DRIVER, _NOUVEAU_DRIVER, *_INTEL_DRIVERS)

# Label-preference order for GPU package temperature.  A fixed
# read_temp(1) assumes the die sensor lives at temp1_input, but that
# channel is driver-specific: the Intel `xe` driver (Arc) has no
# temp1_input at all — its lowest channel is temp2 labelled "pkg", with
# temp3="vram", temp4="mctrl", temp5="pcie" — so read_temp(1) reads a
# nonexistent file and _read_int returns None silently.  Resolving by
# tempN_label instead, package/die sensor first, fixes this without
# hard-coding a per-driver channel number.
_GPU_TEMP_LABELS = ("pkg", "gpu", "edge", "vram")


# ── Sysfs I/O helpers ────────────────────────────────────────────────


def _read_text(path: Path) -> str | None:
    frame_log.debug("_read_text: path=%s", path)
    try:
        return path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return None


def _read_int(path: Path) -> int | None:
    frame_log.debug("_read_int: path=%s", path)
    s = _read_text(path)
    if s is None:
        return None
    try:
        return int(s, 10)
    except ValueError:
        try:
            return int(s, 16)
        except ValueError:
            return None


def _read_float(path: Path) -> float | None:
    frame_log.debug("_read_float: path=%s", path)
    s = _read_text(path)
    if s is None:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _channel_index(input_name: str, prefix: str) -> int | None:
    """Parse the channel number N from a ``{prefix}N_input`` hwmon filename
    (``temp2_input`` → 2, ``fan1_input`` → 1), or None if it doesn't match.

    Single parse for every ``{prefix}*_input`` scan (temp/fan/…) so the
    filename convention lives in one place.
    """
    frame_log.debug("_channel_index: input_name=%s prefix=%s", input_name, prefix)
    if not (input_name.startswith(prefix) and input_name.endswith("_input")):
        return None
    try:
        return int(input_name[len(prefix):-len("_input")])
    except ValueError:
        return None


# ── HwmonDevice — a wrapper around one /sys/class/hwmon/hwmonN dir ──


class HwmonDevice:
    """One hwmon directory — reads tempN_input / fanN_input / powerN_average."""

    def __init__(self, path: Path) -> None:
        log.debug("__init__: path=%s", path)
        #: The hwmon NODE (``/sys/class/hwmon/hwmonN``) -- what the DRM-card
        #: and NVMe-serial lookups walk from.
        self.path = path
        #: Where this driver keeps its sensor files: the node itself, or its
        #: ``device/`` for older drivers (sch5636 and kin), which register the
        #: hwmon node but leave every ``*_input`` -- and ``name`` -- on the
        #: platform device.  psutil falls back the same way; our scanner did
        #: not, so such a chip showed no fans and was named "hwmon1" (#282).
        self.attrs = _attribute_dir(path)
        self.driver = _read_text(self.attrs / "name") or path.name

    def read_temp(self, idx: int = 1) -> float | None:
        """tempN_input reports millidegrees C."""
        frame_log.debug("read_temp: idx=%s", idx)
        val = _read_int(self.attrs / f"temp{idx}_input")
        return val / 1000.0 if val is not None else None

    def read_temp_labeled(
        self, prefer: tuple[str, ...] = _GPU_TEMP_LABELS
    ) -> float | None:
        """Resolve a temperature by tempN_label instead of a fixed channel.

        Some drivers don't populate temp1 — the Intel ``xe`` (Arc) driver's
        lowest channel is temp2 labelled ``pkg`` — so a hard-coded
        ``read_temp(1)`` reads a nonexistent ``temp1_input`` and returns None.
        This scans the ``tempN_label`` files, returns the ``tempN_input``
        matching the first preferred label (case-insensitive, exact match so
        ``vram`` never picks up ``vram_ch_0``), and falls back to the lowest
        readable ``tempN_input`` when no label matches (drivers that expose no
        labels at all).  Returns None only when no channel is readable.
        """
        by_label: dict[str, int] = {}
        indices: list[int] = []
        for input_path in self.attrs.glob("temp*_input"):
            idx = _channel_index(input_path.name, "temp")
            if idx is None:
                continue
            indices.append(idx)
            label = _read_text(self.attrs / f"temp{idx}_label")
            if label is not None:
                by_label[label.strip().lower()] = idx
        for want in prefer:
            idx = by_label.get(want)
            if idx is None:
                continue
            val = self.read_temp(idx)
            if val is not None:
                log.debug("read_temp_labeled(%s): label %r -> temp%d = %.1f°C",
                          self.driver, want, idx, val)
                return val
        for idx in sorted(indices):
            val = self.read_temp(idx)
            if val is not None:
                log.debug("read_temp_labeled(%s): no preferred label matched, "
                          "fell back to lowest channel temp%d = %.1f°C",
                          self.driver, idx, val)
                return val
        log.debug("read_temp_labeled(%s): no readable temp*_input", self.driver)
        return None

    def read_fan_rpm(self, idx: int = 1) -> int | None:
        frame_log.debug("read_fan_rpm: idx=%s", idx)
        return _read_int(self.attrs / f"fan{idx}_input")

    def read_pwm(self, idx: int = 1) -> float | None:
        """pwmN reports 0-255 duty cycle; normalize to 0-100."""
        frame_log.debug("read_pwm: idx=%s", idx)
        val = _read_int(self.attrs / f"pwm{idx}")
        return (val / 255.0 * 100.0) if val is not None else None

    def read_power(self, idx: int = 1) -> float | None:
        """powerN_average reports μW; return W."""
        frame_log.debug("read_power: idx=%s", idx)
        val = _read_int(self.attrs / f"power{idx}_average")
        return val / 1_000_000.0 if val is not None else None


def _attribute_dir(node: Path) -> Path:
    """*node*, or ``node/device`` when only the latter holds sensor inputs."""
    device = node / "device"
    if not any(node.glob("*_input")) and any(device.glob("*_input")):
        log.info("_attribute_dir: %s keeps its sensors in device/", node.name)
        return device
    return node


def scan_hwmon_devices() -> list[HwmonDevice]:
    """Walk /sys/class/hwmon and wrap each directory."""
    log.info("scan_hwmon_devices: called")
    if not _HWMON_ROOT.exists():
        return []
    return [HwmonDevice(d) for d in sorted(_HWMON_ROOT.iterdir()) if d.is_dir()]


# ── CPU package power (powercap RAPL energy counter) ────────────────


class _EnergyRate:
    """Watts from a monotonic microjoule counter: Δenergy / Δt.

    The first reading seeds the baseline and answers None; a negative delta
    is a counter wraparound and is dropped the same way.  RAPL's
    ``energy_uj`` and the Intel GPU's ``energy1_input`` are both such
    counters -- i915 and xe expose no ``power1_average`` at all -- so the
    arithmetic lives here once.  Locked: the MetricsLoop poll thread and a
    render tick may both read, and pairing one thread's ``now`` with the
    other's baseline would report garbage watts.
    """

    __slots__ = ("_last", "_lock")

    def __init__(self) -> None:
        log.debug("_EnergyRate.__init__")
        self._last: tuple[float, float] | None = None   # (microjoules, monotonic)
        self._lock = threading.Lock()

    def watts(self, microjoules: float) -> float | None:
        with self._lock:
            now = time.monotonic()
            watts: float | None = None
            if self._last is not None:
                prev, then = self._last
                if (dt := now - then) > 0 and (w := (microjoules - prev) / (dt * 1_000_000)) >= 0:
                    watts = w
            self._last = (microjoules, now)
        frame_log.debug("_EnergyRate.watts: %s uJ -> %s W", microjoules, watts)
        return watts


class _RaplCpuPower:
    """CPU package power from the powercap RAPL ``energy_uj`` counter.

    ``energy_uj`` is a monotonic microjoule counter, so instantaneous
    power = Δenergy / Δt.  The first read seeds the baseline and returns
    None; subsequent reads return watts.  Sums the ``package-*`` domains
    (multi-socket) and drops a negative delta — counter wraparound — the
    same way legacy did.  On hardened kernels (CVE-2020-8694) the counter
    is root-only; an unreadable file degrades to None, never a crash.

    ``read()`` mutates ``_last`` and is reachable from two threads — the
    MetricsLoop poll thread and the GUI/CLI render-tick thread both call
    ``read_all()`` — so the read-delta-update is guarded by a lock; an
    interleaving would otherwise pair one thread's ``now`` with another's
    ``prev`` and report garbage watts.
    """

    __slots__ = ("_lock", "_next_rediscover", "_paths", "_rate")

    def __init__(self) -> None:
        log.debug("__init__")
        self._paths = self._discover()
        self._rate = _EnergyRate()
        self._lock = threading.Lock()
        self._next_rediscover = 0.0

    @staticmethod
    def _discover() -> list[Path]:
        """Top-level ``package-*`` RAPL energy paths (CPU sockets)."""
        try:
            domains = sorted(_POWERCAP_ROOT.glob("intel-rapl:*"))
        except OSError as e:
            log.debug("RAPL discovery skipped: %s", e)
            return []
        paths: list[Path] = []
        for domain in domains:
            # Top-level domains only (intel-rapl:N), not subdomains
            # (intel-rapl:N:M = core / uncore / dram).
            if ":" in domain.name.split("intel-rapl:")[1]:
                continue
            # CPU package only — skip psys (platform) / dram domains.
            name = _read_text(domain / "name") or ""
            if not name.startswith("package"):
                continue
            energy = domain / "energy_uj"
            if _read_text(energy) is not None:   # readable now → keep
                paths.append(energy)
            else:
                log.debug("RAPL %s: energy_uj unreadable — skipped",
                          domain.name)
        log.info("RAPL CPU power: %d readable package domain(s)", len(paths))
        return paths

    def _maybe_rediscover(self) -> None:
        """Re-scan for RAPL paths when we have none — covers a driver load or
        permission grant that happened after construction (the user just ran
        ``trcc setup``), so CPU power appears without restarting the app
        (#194).  Throttled so the empty case stays cheap.  Caller holds
        ``_lock``."""
        log.debug("_maybe_rediscover")
        now = time.monotonic()
        if now < self._next_rediscover:
            return
        self._next_rediscover = now + _RAPL_REDISCOVER_INTERVAL_S
        self._paths = self._discover()

    def read(self) -> float | None:
        """Watts since the last read, or None (first read / wrap / locked).

        Thread-safe: the read-delta-update runs under ``_lock`` so
        concurrent callers can't interleave their energy/time samples.
        """
        frame_log.debug("read")
        with self._lock:
            if not self._paths:
                self._maybe_rediscover()
                if not self._paths:
                    return None
            total = 0.0
            for path in self._paths:
                val = _read_float(path)
                if val is None:
                    return None     # became unreadable — bail this tick
                total += val
            return self._rate.watts(total)


# ── CPU temperature (composes PsutilCpu with a hwmon temp source) ───


class HwmonCpu(CpuSource):
    """CPU with temp from hwmon coretemp/k10temp/zenpower + usage/freq from
    psutil, and package power from the powercap RAPL energy counter."""

    def __init__(self, psutil_cpu: PsutilCpu,
                 temp_device: HwmonDevice | None) -> None:
        log.debug("__init__: psutil_cpu=%s temp_device=%s", psutil_cpu, temp_device)
        self._psutil = psutil_cpu
        self._temp_device = temp_device
        self._rapl = _RaplCpuPower()

    @property
    def name(self) -> str:
        frame_log.debug("name")
        return self._psutil.name

    def temp(self) -> float | None:
        frame_log.debug("temp")
        if self._temp_device is None:
            return None
        return self._temp_device.read_temp(1)

    def usage(self) -> float | None:
        frame_log.debug("usage")
        return self._psutil.usage()

    def freq(self) -> float | None:
        frame_log.debug("freq")
        return self._psutil.freq()

    def power(self) -> float | None:
        # CPU package power via powercap RAPL — energy-delta over the poll
        # interval (None on the first read until a baseline exists).
        frame_log.debug("power")
        return self._rapl.read()


def find_cpu_temp_device(devices: list[HwmonDevice]) -> HwmonDevice | None:
    """Pick the first hwmon device whose driver is a known CPU thermal."""
    log.info("find_cpu_temp_device: devices=%d", len(devices))
    for dev in devices:
        if dev.driver in _CPU_DRIVERS:
            return dev
    return None


# ── AMD + Intel GPUs (hwmon + DRM sysfs composition) ─────────────────


def _find_drm_card_for_hwmon(hwmon_path: Path) -> Path | None:
    """Walk sysfs to match a hwmon directory to its /sys/class/drm/cardN."""
    log.debug("_find_drm_card_for_hwmon: hwmon_path=%s", hwmon_path)
    # hwmon_path -> ../../device points to the PCI device
    try:
        pci_dev = (hwmon_path / "device").resolve()
    except OSError:
        return None
    if not _DRM_ROOT.exists():
        return None
    for card in sorted(_DRM_ROOT.glob("card[0-9]*")):
        if "-" in card.name:        # card0-HDMI-A-1 etc. — skip connectors
            continue
        try:
            card_pci = (card / "device").resolve()
        except OSError:
            continue
        if card_pci == pci_dev:
            return card
    return None


class AmdGpu(GpuSource):
    """AMD Radeon/Ryzen APU — hwmon amdgpu + DRM sysfs.

    Discrete flag: VRAM total > 2 GB marks it as a real dGPU; APU iGPUs
    typically report <= 512 MB allocated VRAM.
    """

    def __init__(self, index: int, hwmon: HwmonDevice,
                 drm_card: Path | None) -> None:
        log.debug("__init__: index=%s hwmon=%s", index, hwmon)
        self._index = index
        self._hwmon = hwmon
        self._drm = drm_card
        self._name_cache: str | None = None

    @property
    def key(self) -> str:
        frame_log.debug("key")
        return f"amd:{self._index}"

    @property
    def name(self) -> str:
        frame_log.debug("name")
        if self._name_cache is not None:
            return self._name_cache
        # /sys/class/drm/cardN/device/product_name is populated by the kernel
        # on newer drivers; fall back to the PCI ID if not present.
        name = None
        if self._drm is not None:
            name = _read_text(self._drm / "device" / "product_name")
            if name is None:
                name = _read_text(self._drm / "device" / "vbios_version")
        self._name_cache = name or f"AMD GPU {self._index}"
        return self._name_cache

    @property
    def is_discrete(self) -> bool:
        frame_log.debug("is_discrete")
        total = self.vram_total()
        return total is not None and total > 2048.0

    def temp(self) -> float | None:
        frame_log.debug("temp")
        return self._hwmon.read_temp(1)

    def usage(self) -> float | None:
        frame_log.debug("usage")
        if self._drm is None:
            return None
        return _read_float(self._drm / "device" / "gpu_busy_percent")

    def clock(self) -> float | None:
        # amdgpu freq1_input reports Hz in some kernels, MHz in others
        frame_log.debug("clock")
        val = _read_int(self._hwmon.attrs / "freq1_input")
        if val is None:
            return None
        return val / 1_000_000.0 if val > 1_000_000 else float(val)

    def power(self) -> float | None:
        frame_log.debug("power")
        return self._hwmon.read_power(1)

    def fan(self) -> float | None:
        frame_log.debug("fan")
        return self._hwmon.read_pwm(1)

    def fan_rpm(self) -> float | None:
        frame_log.debug("fan_rpm")
        rpm = self._hwmon.read_fan_rpm(1)
        return float(rpm) if rpm is not None else None

    def vram_used(self) -> float | None:
        frame_log.debug("vram_used")
        if self._drm is None:
            return None
        val = _read_int(self._drm / "device" / "mem_info_vram_used")
        return val / (1024 * 1024) if val is not None else None

    def vram_total(self) -> float | None:
        frame_log.debug("vram_total")
        if self._drm is None:
            return None
        val = _read_int(self._drm / "device" / "mem_info_vram_total")
        return val / (1024 * 1024) if val is not None else None


class IntelGpu(GpuSource):
    """Intel iGPU (i915/xe) + discrete Arc (xe) via hwmon + DRM sysfs.

    Arc discretes use the `xe` driver; iGPUs use `i915`.  Discrete flag
    follows the driver name — xe = discrete Arc, i915 = iGPU.
    """

    def __init__(self, index: int, hwmon: HwmonDevice | None,
                 drm_card: Path | None, driver: str) -> None:
        log.debug("__init__: index=%s hwmon=%s", index, hwmon)
        self._index = index
        self._hwmon = hwmon
        self._drm = drm_card
        self._driver = driver
        self._energy = _EnergyRate()

    @property
    def key(self) -> str:
        frame_log.debug("key")
        return f"intel:{'arc' if self._driver == 'xe' else 'igpu'}:{self._index}"

    @property
    def name(self) -> str:
        frame_log.debug("name")
        if self._drm is not None:
            if (n := _read_text(self._drm / "device" / "product_name")) is not None:
                return n
        return f"Intel {'Arc' if self._driver == 'xe' else 'iGPU'} {self._index}"

    @property
    def is_discrete(self) -> bool:
        frame_log.debug("is_discrete")
        return self._driver == "xe"

    def temp(self) -> float | None:
        # Resolve by tempN_label — the xe (Arc) driver has no temp1_input
        # (lowest channel is temp2="pkg"), so a fixed read_temp(1) reads a
        # missing file and yields None (#gpu-temp-empty).
        frame_log.debug("temp")
        return self._hwmon.read_temp_labeled() if self._hwmon is not None else None

    def clock(self) -> float | None:
        # i915 publishes the GT clock on the card; xe (Arc) per GT, where gt0
        # is the render engine and act_freq the frequency PCODE actually
        # granted (xe_gt_freq.c).  Reading only the i915 file left Arc at None.
        frame_log.debug("clock: driver=%s", self._driver)
        if self._drm is None:
            return None
        return _read_float(self._drm / (
            "device/tile0/gt0/freq0/act_freq" if self._driver == "xe"
            else "gt_cur_freq_mhz"))

    def power(self) -> float | None:
        # Neither i915 nor xe has power1_average -- only the energy1_input
        # microjoule counter -- so this read nothing on every Intel GPU (#236).
        frame_log.debug("power")
        if self._hwmon is None or (uj := _read_float(self._hwmon.attrs / "energy1_input")) is None:
            return None
        return self._energy.watts(uj)

    def fan(self) -> float | None:
        # Intel iGPUs don't have their own fan.  Arc discrete may.
        frame_log.debug("fan")
        return self._hwmon.read_pwm(1) if self._hwmon is not None else None

    def fan_rpm(self) -> float | None:
        # xe has fan1..3_input and no pwm, so fan() is None on Arc (#236).
        frame_log.debug("fan_rpm")
        rpm = self._hwmon.read_fan_rpm(1) if self._hwmon is not None else None
        return float(rpm) if rpm is not None else None

class NouveauGpu(GpuSource):
    """NVIDIA on the open-source nouveau driver -- hwmon only.

    Units are the driver's own (``drivers/gpu/drm/nouveau/nouveau_hwmon.c``):
    temp1 in millidegrees, fan1 in RPM, **pwm1 already a percent** -- nvkm
    clamps the duty to 0-100, so the shared 0-255 ``read_pwm`` would show 30%
    as 12% -- and ``power1_input`` in µW, with no ``power1_average``.  Usage,
    clock and VRAM are never exposed, so they stay un-overridden and
    ``unsupported()`` names them.  GSP boards (Turing and later) register no
    hwmon node at all today, so in practice this reads pre-Turing cards.
    """

    def __init__(self, index: int, hwmon: HwmonDevice) -> None:
        log.debug("NouveauGpu.__init__: index=%s hwmon=%s", index, hwmon)
        self._index = index
        self._hwmon = hwmon

    @property
    def key(self) -> str:
        frame_log.debug("NouveauGpu.key")
        return f"nouveau:{self._index}"

    @property
    def name(self) -> str:
        frame_log.debug("NouveauGpu.name")
        return f"NVIDIA GPU {self._index} (nouveau)"

    @property
    def is_discrete(self) -> bool:
        frame_log.debug("NouveauGpu.is_discrete")
        return True

    def temp(self) -> float | None:
        frame_log.debug("NouveauGpu.temp")
        return self._hwmon.read_temp(1)

    def power(self) -> float | None:
        frame_log.debug("NouveauGpu.power")
        uw = _read_int(self._hwmon.attrs / "power1_input")
        return uw / 1_000_000.0 if uw is not None else None

    def fan(self) -> float | None:
        frame_log.debug("NouveauGpu.fan")
        duty = _read_int(self._hwmon.attrs / "pwm1")
        return float(duty) if duty is not None else None

    def fan_rpm(self) -> float | None:
        frame_log.debug("NouveauGpu.fan_rpm")
        rpm = self._hwmon.read_fan_rpm(1)
        return float(rpm) if rpm is not None else None


def discover_nouveau_gpus(devices: list[HwmonDevice]) -> list[GpuSource]:
    """One NouveauGpu per ``nouveau`` hwmon node."""
    gpus: list[GpuSource] = [
        NouveauGpu(i, dev)
        for i, dev in enumerate(d for d in devices if d.driver == _NOUVEAU_DRIVER)
    ]
    log.info("discover_nouveau_gpus: devices=%d -> %d gpu(s)", len(devices), len(gpus))
    return gpus


def discover_amd_gpus(devices: list[HwmonDevice]) -> list[GpuSource]:
    """Find amdgpu hwmon entries, link them to /sys/class/drm cards."""
    log.info("discover_amd_gpus: devices=%d", len(devices))
    gpus: list[GpuSource] = []
    for i, dev in enumerate(d for d in devices if d.driver == _AMD_DRIVER):
        gpus.append(AmdGpu(i, dev, _find_drm_card_for_hwmon(dev.path)))
    return gpus


def discover_intel_gpus(devices: list[HwmonDevice]) -> list[GpuSource]:
    """Find i915/xe hwmon entries.  iGPUs often have no hwmon entry at all —
    they're still listed via DRM-only probing."""
    log.info("discover_intel_gpus: devices=%d", len(devices))
    gpus: list[GpuSource] = []
    # hwmon-backed entries first (Arc discrete + newer i915)
    seen_drm: set[Path] = set()
    for i, dev in enumerate(d for d in devices if d.driver in _INTEL_DRIVERS):
        card = _find_drm_card_for_hwmon(dev.path)
        if card is not None:
            seen_drm.add(card)
        gpus.append(IntelGpu(i, dev, card, dev.driver))
    # DRM-only entries (old i915 iGPUs without hwmon)
    if _DRM_ROOT.exists():
        for card in sorted(_DRM_ROOT.glob("card[0-9]*")):
            if "-" in card.name or card in seen_drm:
                continue
            vendor = _read_text(card / "device" / "vendor")
            if vendor != "0x8086":
                continue
            gpus.append(IntelGpu(len(gpus), None, card, "i915"))
    return gpus


# ── Fans ─────────────────────────────────────────────────────────────


class HwmonFan(FanSource):
    """One fan input on a hwmon device."""

    def __init__(self, hwmon: HwmonDevice, idx: int, label: str | None) -> None:
        log.debug("__init__: hwmon=%s idx=%s", hwmon, idx)
        self._hwmon = hwmon
        self._idx = idx
        self._label = label or f"{hwmon.driver} fan{idx}"

    @property
    def key(self) -> str:
        frame_log.debug("key")
        return f"hwmon:{self._hwmon.driver}:fan{self._idx}"

    @property
    def name(self) -> str:
        frame_log.debug("name")
        return self._label

    def rpm(self) -> int | None:
        frame_log.debug("rpm")
        return self._hwmon.read_fan_rpm(self._idx)

    def percent(self) -> float | None:
        frame_log.debug("percent")
        return self._hwmon.read_pwm(self._idx)

    @property
    def on_gpu(self) -> bool:
        frame_log.debug("on_gpu: %s", self._hwmon.driver)
        return self._hwmon.driver in _GPU_DRIVERS


def discover_fans(devices: list[HwmonDevice]) -> list[FanSource]:
    log.info("discover_fans: devices=%d", len(devices))
    fans: list[FanSource] = []
    for dev in devices:
        for fan_input in sorted(dev.attrs.glob("fan*_input")):
            idx = _channel_index(fan_input.name, "fan")
            if idx is None:
                continue
            label = _read_text(dev.attrs / f"fan{idx}_label")
            fans.append(HwmonFan(dev, idx, label))
    return fans


# ── Disk temperature (NVMe / SATA SSD/HDD via the drivetemp module) ──

# hwmon drivers that expose a storage-device temperature: the kernel ``nvme``
# driver (NVMe Composite temp at temp1) and ``drivetemp`` (SATA SSD/HDD SMART
# temperature, modprobe drivetemp).  One physical drive per device → read the
# primary temp1 only (NVMe's temp2/temp3 are extra on-controller sensors of the
# SAME drive; the model carries one disk_temp, so the composite is the number).
_DISK_DRIVERS = ("nvme", "drivetemp")


class HwmonDisk(DiskSource):
    """One storage device's temp1 sensor on a hwmon ``nvme`` / ``drivetemp`` node."""

    def __init__(self, hwmon: HwmonDevice, label: str | None) -> None:
        log.debug("__init__: hwmon=%s label=%s", hwmon, label)
        self._hwmon = hwmon
        self._label = label or f"{hwmon.driver} disk"
        self._ident = self._identity(hwmon)

    @staticmethod
    def _identity(hwmon: HwmonDevice) -> str:
        """A per-DRIVE discriminator: the hardware serial, else the hwmon dir.

        Two properties are wanted and they come from different places:

        * **Unique** — required TODAY.  The key used to be
          ``hwmon:{driver}:temp1``, so two NVMe drives produced the SAME key.
          ``HwmonDram`` ten lines below already fixed exactly this for matched
          DIMMs and says why: a driver-only key "would collide across modules
          (and conflate their per-source read-failure bookkeeping)".  Disks had
          the identical exposure and were missed.
        * **Stable across boots** — required BEFORE a user's disk choice can be
          persisted.  ``hwmonN`` numbering is not, so the DRAM fix alone is not
          enough here; ``device/serial`` is.

        NVMe exposes ``device/serial``.  Where it is absent (a ``drivetemp``
        SATA node may not publish one) this falls back to the hwmon directory
        name: unique, but boot-unstable — so a caller persisting a selection
        must treat a key it can no longer find as "gone", not as an error.
        """
        serial = (_read_text(hwmon.path / "device" / "serial") or "").strip()
        if serial:
            log.debug("HwmonDisk._identity: %s -> serial", hwmon.path.name)
            return serial
        log.debug("HwmonDisk._identity: %s has no device/serial — using dir name",
                  hwmon.path.name)
        return hwmon.path.name

    @property
    def key(self) -> str:
        frame_log.debug("key")
        return f"hwmon:{self._hwmon.driver}:{self._ident}:temp1"

    @property
    def name(self) -> str:
        frame_log.debug("name")
        return self._label

    def temp(self) -> float | None:
        frame_log.debug("temp")
        return self._hwmon.read_temp(1)


def discover_disk_temp(devices: list[HwmonDevice]) -> list[DiskSource]:
    """One :class:`HwmonDisk` per ``nvme`` / ``drivetemp`` device with a temp1."""
    log.info("discover_disk_temp: devices=%d", len(devices))
    disks: list[DiskSource] = []
    for dev in devices:
        if dev.driver not in _DISK_DRIVERS:
            continue
        if dev.read_temp(1) is None:
            log.debug("discover_disk_temp: %s has no temp1_input — skip", dev.driver)
            continue
        label = _read_text(dev.attrs / "temp1_label")
        disks.append(HwmonDisk(dev, label))
    return disks


# ── Memory (DRAM SPD-hub) temperature ────────────────────────────────

# hwmon drivers that expose a DIMM thermal sensor: DDR5 modules carry an
# integrated SPD-hub sensor (``spd5118``); DDR4 modules expose the optional
# JEDEC JC-42.4 thermal sensor (``jc42``).  ``ee1004`` (the DDR4 SPD EEPROM)
# is deliberately excluded — it carries identity data, not a temperature.
_DRAM_DRIVERS = ("spd5118", "jc42")


class HwmonDram(DramSource):
    """One DIMM's temp1 sensor on a hwmon ``spd5118`` / ``jc42`` node."""

    def __init__(self, hwmon: HwmonDevice, label: str | None) -> None:
        log.debug("__init__: hwmon=%s label=%s", hwmon, label)
        self._hwmon = hwmon
        self._label = label or f"{hwmon.driver} DRAM"

    @property
    def key(self) -> str:
        # Include the hwmon dir name: matched DIMMs share a driver, so a
        # driver-only key would collide across modules (and conflate their
        # per-source read-failure bookkeeping).
        frame_log.debug("key")
        return f"hwmon:{self._hwmon.driver}:{self._hwmon.path.name}:temp1"

    @property
    def name(self) -> str:
        frame_log.debug("name")
        return self._label

    def temp(self) -> float | None:
        frame_log.debug("temp")
        return self._hwmon.read_temp(1)


def discover_dram_temp(devices: list[HwmonDevice]) -> list[DramSource]:
    """One :class:`HwmonDram` per ``spd5118`` / ``jc42`` device with a temp1."""
    log.info("discover_dram_temp: devices=%d", len(devices))
    dram: list[DramSource] = []
    for dev in devices:
        if dev.driver not in _DRAM_DRIVERS:
            continue
        if dev.read_temp(1) is None:
            log.debug("discover_dram_temp: %s has no temp1_input — skip", dev.driver)
            continue
        label = _read_text(dev.attrs / "temp1_label")
        dram.append(HwmonDram(dev, label))
    return dram


# ── Memory channel clock (what the controller RUNS, read once) ────────


class MemoryClock:
    """Memory channel clock in MHz — the speed the memory controller RUNS.

    Prefers dmidecode's ``Configured Memory Speed`` (MT/s ÷ 2, DDR), and falls
    back to the DDR5 SPD nameplate.  It used to read ONLY the nameplate, which
    is the JEDEC base profile — 4800 MT/s for every DDR5 stick by spec — so an
    MC-3 on a machine running XMP 8000 showed 4800 (#279).  The Windows side
    has always preferred the configured value (``ConfiguredClockSpeed``).

    Read on the FIRST ``clock()`` and cached: the value is static, and the
    dmidecode read goes through pkexec, which a CLI command that never shows
    the RAM speed should not pay for.
    """

    def __init__(self) -> None:
        self._mhz: float | None = None
        self._read = False
        log.debug("MemoryClock: created (read deferred to first clock())")

    def clock(self) -> float | None:
        frame_log.debug("clock")
        if not self._read:
            self._read = True
            self._mhz = self._resolve()
        return self._mhz

    @staticmethod
    def _resolve() -> float | None:
        """Configured speed if dmidecode answers, else the SPD nameplate."""
        from ..system.linux import configured_memory_mts
        from ..system.spd import read_spd_timings
        if (mts := configured_memory_mts()) is not None:
            log.info("MemoryClock: %d MT/s configured -> %.0f MHz", mts, mts / 2)
            return mts / 2
        timings = read_spd_timings()
        mhz = float(timings.mhz) if timings else None
        log.info("MemoryClock: no configured speed readable -> SPD nameplate "
                 "mhz=%s", mhz)
        return mhz
