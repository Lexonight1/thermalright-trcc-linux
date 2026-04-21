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
from pathlib import Path
from typing import List, Optional

from ...core.ports import CpuSource, FanSource, GpuSource
from .psutil_sources import PsutilCpu

log = logging.getLogger(__name__)


_HWMON_ROOT = Path("/sys/class/hwmon")
_DRM_ROOT = Path("/sys/class/drm")

_CPU_DRIVERS = ("coretemp", "k10temp", "zenpower")
_AMD_DRIVER = "amdgpu"
_INTEL_DRIVERS = ("i915", "xe")


# ── Sysfs I/O helpers ────────────────────────────────────────────────


def _read_text(path: Path) -> Optional[str]:
    try:
        return path.read_text().strip()
    except (OSError, UnicodeDecodeError):
        return None


def _read_int(path: Path) -> Optional[int]:
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


def _read_float(path: Path) -> Optional[float]:
    s = _read_text(path)
    if s is None:
        return None
    try:
        return float(s)
    except ValueError:
        return None


# ── HwmonDevice — a wrapper around one /sys/class/hwmon/hwmonN dir ──


class HwmonDevice:
    """One hwmon directory — reads tempN_input / fanN_input / powerN_average."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.driver = _read_text(path / "name") or path.name

    def read_temp(self, idx: int = 1) -> Optional[float]:
        """tempN_input reports millidegrees C."""
        val = _read_int(self.path / f"temp{idx}_input")
        return val / 1000.0 if val is not None else None

    def read_fan_rpm(self, idx: int = 1) -> Optional[int]:
        return _read_int(self.path / f"fan{idx}_input")

    def read_pwm(self, idx: int = 1) -> Optional[float]:
        """pwmN reports 0-255 duty cycle; normalize to 0-100."""
        val = _read_int(self.path / f"pwm{idx}")
        return (val / 255.0 * 100.0) if val is not None else None

    def read_power(self, idx: int = 1) -> Optional[float]:
        """powerN_average reports μW; return W."""
        val = _read_int(self.path / f"power{idx}_average")
        return val / 1_000_000.0 if val is not None else None


def scan_hwmon_devices() -> List[HwmonDevice]:
    """Walk /sys/class/hwmon and wrap each directory."""
    if not _HWMON_ROOT.exists():
        return []
    return [HwmonDevice(d) for d in sorted(_HWMON_ROOT.iterdir()) if d.is_dir()]


# ── CPU temperature (composes PsutilCpu with a hwmon temp source) ───


class HwmonCpu(CpuSource):
    """CPU with temp from hwmon coretemp/k10temp/zenpower + usage/freq from psutil."""

    def __init__(self, psutil_cpu: PsutilCpu,
                 temp_device: Optional[HwmonDevice]) -> None:
        self._psutil = psutil_cpu
        self._temp_device = temp_device

    @property
    def name(self) -> str:
        return self._psutil.name

    def temp(self) -> Optional[float]:
        if self._temp_device is None:
            return None
        return self._temp_device.read_temp(1)

    def usage(self) -> Optional[float]:
        return self._psutil.usage()

    def freq(self) -> Optional[float]:
        return self._psutil.freq()

    def power(self) -> Optional[float]:
        # Intel RAPL / AMD package power lives outside hwmon; add later.
        return None


def find_cpu_temp_device(devices: List[HwmonDevice]) -> Optional[HwmonDevice]:
    """Pick the first hwmon device whose driver is a known CPU thermal."""
    for dev in devices:
        if dev.driver in _CPU_DRIVERS:
            return dev
    return None


# ── AMD + Intel GPUs (hwmon + DRM sysfs composition) ─────────────────


def _find_drm_card_for_hwmon(hwmon_path: Path) -> Optional[Path]:
    """Walk sysfs to match a hwmon directory to its /sys/class/drm/cardN."""
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
                 drm_card: Optional[Path]) -> None:
        self._index = index
        self._hwmon = hwmon
        self._drm = drm_card
        self._name_cache: Optional[str] = None

    @property
    def key(self) -> str:
        return f"amd:{self._index}"

    @property
    def name(self) -> str:
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
        total = self.vram_total()
        return total is not None and total > 2048.0

    def temp(self) -> Optional[float]:
        return self._hwmon.read_temp(1)

    def usage(self) -> Optional[float]:
        if self._drm is None:
            return None
        return _read_float(self._drm / "device" / "gpu_busy_percent")

    def clock(self) -> Optional[float]:
        # amdgpu freq1_input reports Hz in some kernels, MHz in others
        val = _read_int(self._hwmon.path / "freq1_input")
        if val is None:
            return None
        return val / 1_000_000.0 if val > 1_000_000 else float(val)

    def power(self) -> Optional[float]:
        return self._hwmon.read_power(1)

    def fan(self) -> Optional[float]:
        rpm = self._hwmon.read_fan_rpm(1)
        if rpm is None:
            # Try PWM duty cycle as a percentage fallback
            return self._hwmon.read_pwm(1)
        # Approximate %: amdgpu fan1_max isn't always exposed; skip rpm→%
        return None

    def vram_used(self) -> Optional[float]:
        if self._drm is None:
            return None
        val = _read_int(self._drm / "device" / "mem_info_vram_used")
        return val / (1024 * 1024) if val is not None else None

    def vram_total(self) -> Optional[float]:
        if self._drm is None:
            return None
        val = _read_int(self._drm / "device" / "mem_info_vram_total")
        return val / (1024 * 1024) if val is not None else None


class IntelGpu(GpuSource):
    """Intel iGPU (i915/xe) + discrete Arc (xe) via hwmon + DRM sysfs.

    Arc discretes use the `xe` driver; iGPUs use `i915`.  Discrete flag
    follows the driver name — xe = discrete Arc, i915 = iGPU.
    """

    def __init__(self, index: int, hwmon: Optional[HwmonDevice],
                 drm_card: Optional[Path], driver: str) -> None:
        self._index = index
        self._hwmon = hwmon
        self._drm = drm_card
        self._driver = driver

    @property
    def key(self) -> str:
        return f"intel:{'arc' if self._driver == 'xe' else 'igpu'}:{self._index}"

    @property
    def name(self) -> str:
        if self._drm is not None:
            if (n := _read_text(self._drm / "device" / "product_name")) is not None:
                return n
        return f"Intel {'Arc' if self._driver == 'xe' else 'iGPU'} {self._index}"

    @property
    def is_discrete(self) -> bool:
        return self._driver == "xe"

    def temp(self) -> Optional[float]:
        return self._hwmon.read_temp(1) if self._hwmon is not None else None

    def usage(self) -> Optional[float]:
        # i915 exposes gt busy as `gt_cur_freq_mhz` / max ratio — approximate;
        # proper util requires `intel_gpu_top` which isn't sysfs.  Skip for now.
        return None

    def clock(self) -> Optional[float]:
        if self._drm is None:
            return None
        return _read_float(self._drm / "gt_cur_freq_mhz")

    def power(self) -> Optional[float]:
        return self._hwmon.read_power(1) if self._hwmon is not None else None

    def fan(self) -> Optional[float]:
        # Intel iGPUs don't have their own fan.  Arc discrete may.
        return self._hwmon.read_pwm(1) if self._hwmon is not None else None

    def vram_used(self) -> Optional[float]:
        return None  # Intel GPUs don't expose VRAM accounting through sysfs

    def vram_total(self) -> Optional[float]:
        return None


def discover_amd_gpus(devices: List[HwmonDevice]) -> List[GpuSource]:
    """Find amdgpu hwmon entries, link them to /sys/class/drm cards."""
    gpus: List[GpuSource] = []
    for i, dev in enumerate(d for d in devices if d.driver == _AMD_DRIVER):
        gpus.append(AmdGpu(i, dev, _find_drm_card_for_hwmon(dev.path)))
    return gpus


def discover_intel_gpus(devices: List[HwmonDevice]) -> List[GpuSource]:
    """Find i915/xe hwmon entries.  iGPUs often have no hwmon entry at all —
    they're still listed via DRM-only probing."""
    gpus: List[GpuSource] = []
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

    def __init__(self, hwmon: HwmonDevice, idx: int, label: Optional[str]) -> None:
        self._hwmon = hwmon
        self._idx = idx
        self._label = label or f"{hwmon.driver} fan{idx}"

    @property
    def key(self) -> str:
        return f"hwmon:{self._hwmon.driver}:fan{self._idx}"

    @property
    def name(self) -> str:
        return self._label

    def rpm(self) -> Optional[int]:
        return self._hwmon.read_fan_rpm(self._idx)

    def percent(self) -> Optional[float]:
        return self._hwmon.read_pwm(self._idx)


def discover_fans(devices: List[HwmonDevice]) -> List[FanSource]:
    fans: List[FanSource] = []
    for dev in devices:
        for fan_input in sorted(dev.path.glob("fan*_input")):
            try:
                idx = int(fan_input.name.replace("fan", "").replace("_input", ""))
            except ValueError:
                continue
            label = _read_text(dev.path / f"fan{idx}_label")
            fans.append(HwmonFan(dev, idx, label))
    return fans
