"""Ports — ABCs that adapters implement.

Pure contract definitions.  Adapter implementations live in
`trcc.adapters.*`.  Services and App depend on these ABCs, never on
concrete implementations.
"""
from __future__ import annotations

import builtins
import logging
from abc import ABC, abstractmethod
from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path
from typing import TYPE_CHECKING, Any, Generic, TypeVar

from .errors import DeviceDisconnectedError, UnsupportedOperationError
from .logs import per_frame

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)

# Any buffer a bulk write accepts.  Kept 3.10-safe (``collections.abc.Buffer``
# is 3.12+, but the install gate is >=3.10) so callers can hand a zero-copy
# ``memoryview`` slice of a large frame without a per-chunk copy.
WriteBuffer = bytes | bytearray | memoryview

if TYPE_CHECKING:
    from .diagnostics import DoctorResult, GpuReaderState, HealthReport
    from .events import EventBus
    from .models import (
        CloudCategory,
        CloudThemeEntry,
        DeviceInfo,
        DeviceQuirks,
        DiscoveredMask,
        HandshakeResult,
        HardwareMetrics,
        LedHandshakeResult,
        ProductInfo,
        RawFrame,
        RenderContent,
        SensorReading,
        Theme,
        UsbPowerState,
        WebPreviewInfo,
        Wire,
    )
    from .protocol import DeviceProfile


# =========================================================================
# Transports — byte movers, one ABC per wire family
# =========================================================================
#
# Two transport families cover every protocol:
#
#   BulkTransport  — raw USB bulk/interrupt read/write (HID, BULK, LY, LED)
#   ScsiTransport  — SCSI CDB + data phase, kernel-native where possible
#                    (Linux SG_IO, Windows DeviceIoControl, macOS/BSD BOT)
#
# Protocols hold one of these; they don't care which OS subclass is
# injected.  Platform.open(vid, pid, wire) returns the right transport
# for (OS, wire).


class BulkTransport(ABC):
    """Abstract USB bulk/interrupt transport.  One per open device handle."""

    @abstractmethod
    def open(self) -> bool:
        """Open the device and claim interface.  True on success."""

    @abstractmethod
    def close(self) -> None:
        """Release interface and close the handle."""

    @property
    @abstractmethod
    def is_open(self) -> bool:
        """Whether the transport currently holds an open handle."""

    @abstractmethod
    def write(self, endpoint: int, data: WriteBuffer,
              timeout_ms: int = 100) -> int:
        """Bulk-write a buffer to an OUT endpoint.  Returns bytes transferred.

        Accepts any buffer (``bytes``/``bytearray``/``memoryview``) so callers
        can hand a zero-copy ``memoryview`` slice of a large frame — the HID
        frame path chunks a 154 KB packet ~300×/frame and must not re-copy it.
        """

    @abstractmethod
    def read(self, endpoint: int, length: int,
             timeout_ms: int = 100) -> bytes:
        """Bulk-read up to *length* bytes from an IN endpoint."""


class ScsiTransport(ABC):
    """Abstract SCSI transport.  One per open device handle.

    Uses CDB-level primitives so the kernel (Linux SG_IO, Windows
    DeviceIoControl) can bundle CDB + data + status in a single syscall
    where the OS supports it.  macOS/BSD fall back to userspace BOT.
    """

    @abstractmethod
    def open(self) -> bool:
        """Open the device.  True on success."""

    @abstractmethod
    def close(self) -> None:
        """Release resources."""

    @property
    @abstractmethod
    def is_open(self) -> bool:
        """Whether the transport currently holds an open handle."""

    @abstractmethod
    def send_cdb(self, cdb: bytes, data: bytes,
                 timeout_ms: int = 5000) -> bool:
        """Send a 16-byte CDB with a data-out payload.  True on CSW status 0."""

    @abstractmethod
    def read_cdb(self, cdb: bytes, length: int,
                 timeout_ms: int = 5000) -> bytes:
        """Send a 16-byte CDB and read *length* bytes of data-in."""


# Transport type variable — constrained to the two transport ABCs.
# Each Device subclass binds T to the transport it needs, so
# `self._transport.write(...)` narrows correctly per device.
T = TypeVar("T", BulkTransport, ScsiTransport)

# Any transport a Platform can hand back.  The two ABCs describe genuinely
# different protocols and deliberately share no base — this union is what
# `Platform.open_transport` returns, so the port stays wire-agnostic while
# the caller still gets a precisely-typed object.
Transport = BulkTransport | ScsiTransport


# =========================================================================
# Device — one per physical device, knows its wire protocol
# =========================================================================


class Device(ABC, Generic[T]):
    """A physical USB device we control.

    Concrete subclasses (ScsiLcd, HidLcd, BulkLcd, LyLcd, Led) own their
    wire protocol and declare the transport they need via the type
    parameter: `class ScsiLcd(Device[ScsiTransport])`.  The transport
    is DI'd at construction — devices never build their own.

    All devices share the same outward contract: connect / send /
    disconnect.  They know nothing about the OS, Platform, or other
    devices.
    """

    def __init__(self, info: ProductInfo, transport: T) -> None:
        from .models import DeviceQuirks
        self.info = info
        self._transport: T = transport
        self._handshake: HandshakeResult | None = None
        # Firmware-specific behavior overrides (empty = family default).
        # Injected by the composition root via ``set_quirks`` once the live
        # fingerprint (incl. bcdDevice) is known.  (#228)
        self._quirks: DeviceQuirks = DeviceQuirks()
        # Auto-recovery state — tracks consecutive disconnect-class
        # send failures + rate-limits the warning log.  Reset on every
        # successful send via ``_recovery.note_success``.
        from .device_recovery import RecoveryTracker
        self._recovery = RecoveryTracker(self.info.key)

    def set_permission_hint(self, hint: str) -> None:
        """Inject the OS-specific EACCES remediation hint (a pre-resolved
        string from ``Platform.permission_denied_hint``).

        The device receives a string, never a ``Platform`` — it still knows
        nothing about the OS; the composition root resolves the hint and hands
        it down for the recovery tracker's permission-denied warning.
        """
        self._recovery.set_permission_hint(hint)

    @property
    def quirks(self) -> DeviceQuirks:
        """The firmware overrides resolved for this exact device.

        Read by the composition root's connect path to decide whether a failed
        handshake is worth retrying on a firmware's overriding transport.
        """
        log.debug("Device %s: quirks read", self.info.key)
        return self._quirks

    def set_quirks(self, quirks: DeviceQuirks) -> None:
        """Inject firmware-specific behavior overrides for this exact device.

        Resolved from the live ``(vid, pid, bcdDevice)`` fingerprint at the
        composition root and handed down, so the device honors its firmware's
        divergences (transport, handshake, rotation, streaming) without any
        subclass knowing about USB enumeration.  (#228)
        """
        self._quirks = quirks
        log.debug("Device %s: quirks=%s", self.info.key, quirks)

    @abstractmethod
    def connect(self) -> HandshakeResult:
        """Open the transport and perform the wire-protocol handshake."""

    @abstractmethod
    def send(self, payload: Any) -> bool:
        """Send a payload in device-native format.  Protocol-specific shape."""

    @abstractmethod
    def disconnect(self) -> None:
        """Close the transport and release state."""

    @property
    def is_connected(self) -> bool:
        connected = self._handshake is not None
        frame_log.debug("Device.is_connected: %s (%s)", connected, self.info.key)
        return connected

    @property
    def is_led(self) -> bool:
        """True for LED-control devices; False for LCD-frame devices."""
        return False

    @property
    def key(self) -> str:
        frame_log.debug("Device.key: %s", self.info.key)
        return self.info.key

    @property
    def needs_keepalive(self) -> bool:
        """True if this device's firmware drops frames and needs a periodic
        resend.  Drives the send worker's keepalive.

        Two per-device sources, never the wire: the panel's own
        ``volatile_frames`` (registry) and the per-firmware ``keepalive_stream``
        quirk (resolved from ``bcdDevice``).  The first used to be a set of
        Wires, which made a protocol choice decide whether we kept a screen
        alive — see ``ProductInfo.volatile_frames``.
        """
        return (self.info.volatile_frames or self._quirks.keepalive_stream)

    @property
    def profile(self) -> DeviceProfile | None:
        """Handshake-derived geometry + encoding profile.

        Set by LCD subclasses when ``connect()`` parses the PM/FBL bytes.
        LED devices and pre-handshake state both return None — callers
        that build frames must fall back to ``info.native_resolution``.
        """
        return None

    @property
    def handshake(self) -> HandshakeResult | None:
        """Raw LCD handshake result — PM/SUB bytes, serial, reported resolution.

        Populated on ``connect()`` by every LCD wire (stored on the base as
        ``self._handshake``); None pre-handshake or for LED devices (which use
        ``led_handshake``).  Exposes the PM/SUB bytes for diagnostics — the
        developer device inspector and ``trcc report`` read them here rather
        than re-deriving from the profile (PM isn't recoverable from FBL alone).
        """
        return self._handshake

    @property
    def led_handshake(self) -> LedHandshakeResult | None:
        """LED handshake result (PM byte → style + sub), or None.

        Set by the LED subclass after ``connect()`` resolves the PM
        byte.  LCD devices and pre-handshake state return None, so a
        single ``if device.led_handshake is None`` covers both "not an
        LED device" and "LED not yet handshaken" — callers gate on it
        instead of ``isinstance(device, Led)``.
        """
        return None

    @property
    def can_boot_animate(self) -> bool:
        """True if this device accepts a flash boot animation (SCSI only).

        Lets a Command gate on capability *before* the connection check
        (boot anim is SCSI-only regardless of connection state), instead
        of ``isinstance(device, ScsiLcd)``.  SCSI LCDs override to True.
        """
        return False

    def send_boot_animation(self, frames: list[bytes],
                            delays_ds: list[int]) -> int:
        """Upload a multi-frame boot animation to device flash.

        SCSI LCDs override this; every other device declines with
        ``UnsupportedOperationError`` (the boot-anim flash region only
        exists on the SCSI firmware).  Gated by ``can_boot_animate`` at
        the call site, so this base raise is defensive — no ``isinstance``
        needed.  Returns the number of frames uploaded.
        """
        raise UnsupportedOperationError(
            f"{self.key} does not support boot animation (SCSI-only)"
        )

    # ── Send recovery (Template Method shared by every wire) ─────────────
    #
    # The reconnect + consecutive-failure policy is invariant across wires;
    # only the bytes written vary.  Subclasses build their payload and hand
    # the wire-specific write as a thunk to ``_send_with_recovery`` — the
    # base owns the retry/escalation so each ``send()`` carries one copy of
    # the policy, not five.

    def _reconnect(self) -> None:
        """Close, re-open, and re-handshake the transport (best-effort).

        The in-place recovery step every wire shares: a stale USB handle —
        e.g. after the kernel re-enumerates the device on resume from suspend
        (writes start returning ``EIO``) — is healed by reopening and re-running
        ``connect()``, the same effect a reboot has without the reboot.  Swallows
        and logs its own failure; the caller's retry surfaces a persistent
        problem to the recovery tracker.
        """
        log.info("%s: reconnecting transport (close → open → handshake)", self.key)
        try:
            self._transport.close()
            self._transport.open()
            self.connect()
        except Exception as e:
            log.warning("%s: reconnect failed: %s", self.key, e)

    def _send_with_recovery(self, write: Callable[[], bool]) -> bool:
        """Run a wire write under the shared reconnect + recovery policy.

        Template Method: ``write`` is the subclass's wire-specific write thunk.
        Its outcome drives the policy every wire shares:

        * **returns ``True``** — a completed send.  Resets the recovery counter
          and returns ``True``.
        * **returns ``False``** — a soft, protocol-level failure (short write,
          empty ACK, ``send_cdb`` declined).  Returns ``False`` immediately so
          the caller retries on the next tick — no reconnect, counter untouched.
        * **raises** — a transport error.  One in-place reconnect-and-retry
          (covers transient hub/KVM NAKs AND the stale-handle-after-resume case,
          #189); a persistent failure escalates to the per-device recovery
          tracker, raising :class:`DeviceDisconnectedError` once it hits the
          consecutive-failure threshold so the device is marked disconnected.
        """
        for attempt in range(2):
            try:
                ok = write()
            except Exception as e:
                if attempt == 0 and not self._quirks.keepalive_stream:
                    log.warning(
                        "%s: send attempt 1 failed (%s) — reconnecting and retrying",
                        self.key, e,
                    )
                    self._reconnect()
                    continue
                if self._quirks.keepalive_stream:
                    # Single-session firmware: a close→reopen wedges the panel
                    # until a physical replug (#228), so NEVER reconnect — soft-
                    # fail and let the next keepalive tick resend the frame.
                    log.debug("%s: send failed (%s) — single-session, will resend",
                              self.key, e)
                    return False
                verdict = self._recovery.note_error(e)
                if verdict == "threshold":
                    try:
                        self._transport.close()
                    except OSError as close_err:
                        log.debug("%s: close raised: %s", self.key, close_err)
                    raise DeviceDisconnectedError(
                        f"{self.key} disconnected after "
                        f"{self._recovery.consecutive_failures} consecutive failures",
                    ) from e
                return False
            else:
                if ok:
                    recovered = self._recovery.note_success()
                    if recovered:
                        log.info("%s: send recovered after %d disconnect failure(s)",
                                 self.key, recovered)
                return ok
        return False  # pragma: no cover — loop always returns or raises


# =========================================================================
# Sensor sources — one ABC per hardware role
# =========================================================================
#
# Every reading is Optional[float].  None means "this hardware doesn't
# expose it" — a headless VM has no CPU temp, an APU has no discrete
# GPU, a server has no fans.  Overlays skip None silently so barebones
# and $5k rigs use the same themes, show what they have.
#
# Units are normalized at the source:
#     temp → °C     clock → MHz     power → W
#     memory → MB   percent → 0-100
#
# Overlay keys use normalized, vendor-neutral names:
#     cpu:temp  cpu:usage  cpu:freq  cpu:power
#     gpu:primary:temp  gpu:0:temp  gpu:nvidia:0:temp
#     memory:used  memory:percent
#     fan:cpu:rpm  fan:gpu:percent


class CpuSource(ABC):
    """Primary CPU.  usage/freq nearly always present; temp/power may be None."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def temp(self) -> float | None:
        """CPU package temperature in °C, or None."""

    @abstractmethod
    def usage(self) -> float | None:
        """CPU utilization 0-100, or None."""

    @abstractmethod
    def freq(self) -> float | None:
        """Current CPU frequency in MHz, or None."""

    @abstractmethod
    def power(self) -> float | None:
        """Package power draw in W, or None."""


class MemorySource(ABC):
    """System RAM."""

    @abstractmethod
    def used(self) -> float | None:
        """Used RAM in MB, or None."""

    @abstractmethod
    def available(self) -> float | None:
        """Available RAM in MB, or None."""

    @abstractmethod
    def total(self) -> float | None:
        """Total RAM in MB, or None."""

    @abstractmethod
    def percent(self) -> float | None:
        """Used fraction 0-100, or None."""


class GpuSource(ABC):
    """One GPU — NVIDIA/AMD/Intel/Apple, discrete or integrated."""

    @property
    @abstractmethod
    def key(self) -> str:
        """Stable ID, e.g. 'nvidia:0', 'amd:0', 'intel:igpu'."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable model name."""

    @property
    @abstractmethod
    def is_discrete(self) -> bool:
        """True for dedicated cards, False for iGPUs sharing CPU memory."""

    @abstractmethod
    def temp(self) -> float | None:
        """Core temperature in °C, or None."""

    @abstractmethod
    def usage(self) -> float | None:
        """Utilization 0-100, or None."""

    @abstractmethod
    def clock(self) -> float | None:
        """Core clock in MHz, or None."""

    @abstractmethod
    def power(self) -> float | None:
        """Board power draw in W, or None."""

    @abstractmethod
    def fan(self) -> float | None:
        """Fan speed 0-100, or None."""

    @abstractmethod
    def vram_used(self) -> float | None:
        """VRAM used in MB, or None."""

    @abstractmethod
    def vram_total(self) -> float | None:
        """VRAM total in MB, or None."""


class FanSource(ABC):
    """One fan — may be role-mapped (cpu/gpu/sys1) or anonymous."""

    @property
    @abstractmethod
    def key(self) -> str:
        """Stable ID, e.g. 'cpu', 'gpu', 'sys1', 'hwmon:nct6798:fan1'."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable label."""

    @abstractmethod
    def rpm(self) -> int | None:
        """Current RPM, or None."""

    @abstractmethod
    def percent(self) -> float | None:
        """Duty cycle 0-100, or None."""


class DiskSource(ABC):
    """One storage device's thermal sensor (NVMe / SATA SSD / HDD).

    Every OS reads drive temperature differently — Linux from hwmon
    ``nvme`` / ``drivetemp`` nodes, Windows from LibreHardwareMonitor /
    HWiNFO or SMART attribute 0xC2, macOS from SMC / ``smartctl``, BSD
    from ``sysctl dev.nvme.*.temp`` — so each ``Platform`` discovers its
    own ``DiskSource`` list and the OS-neutral aggregator just folds the
    hottest into ``disk:temp``.  Mirrors :class:`FanSource`.
    """

    @property
    @abstractmethod
    def key(self) -> str:
        """Stable ID, e.g. 'hwmon:nvme:temp1', 'nvme0'."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable label."""

    @abstractmethod
    def temp(self) -> float | None:
        """Current temperature in °C, or None."""


class DramSource(ABC):
    """One memory module's SPD-hub thermal sensor.

    DDR5 DIMMs carry an integrated SPD-hub temperature sensor (Linux
    ``spd5118`` hwmon); DDR4 modules expose an optional JEDEC JC-42.4
    thermal sensor (``jc42``).  Each ``Platform`` discovers its own
    ``DramSource`` list and the OS-neutral aggregator folds the hottest
    into ``memory:temp``.  Mirrors :class:`DiskSource`.
    """

    @property
    @abstractmethod
    def key(self) -> str:
        """Stable ID, e.g. 'hwmon:spd5118:hwmon2:temp1'."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable label."""

    @abstractmethod
    def temp(self) -> float | None:
        """Current temperature in °C, or None."""


# =========================================================================
# SensorEnumerator — the aggregate: composes one CPU + one memory + N GPUs + N fans
# =========================================================================


def _or_zero(value: float | None) -> float:
    """None-coalesce a possibly-absent sensor reading to 0.0."""
    return 0.0 if value is None else float(value)


def _safe(fn: Callable[[], float | None]) -> float:
    """Read one sensor for the snapshot, degrading a RAISING source to 0.0.

    Mirrors :meth:`BaselineSensors._read`: a flaky or permission-locked
    sensor (root-only RAPL ``energy_uj``, a wedged hwmon node, an NVML
    driver/userspace mismatch) must never take down ``snapshot()`` — which
    would take down the per-tick ``SensorsUpdated`` publish and blank every
    metric in the UI.  ``_or_zero`` alone only handles ``None``; a raise
    needs catching too.  The same sources are read via the guarded
    ``read_all`` path on the same tick (which warn-once-logs the failure),
    so this stays at DEBUG to avoid a duplicate warning.
    """
    try:
        return _or_zero(fn())
    except Exception as e:
        log.debug("snapshot: sensor read failed (%s) — degrading to 0.0", e)
        return 0.0


class SensorEnumerator(ABC):
    """OS-level sensor root.  Each OS has one implementation.

    Exposes structured access (cpu, memory, gpus, fans) AND a flat
    dict view for overlays keyed by normalized names.
    """

    # User's GPU choice (sensor key, e.g. 'nvidia:0'); ``None`` = auto-pick.
    # Set by ``SetGpuDevice`` and seeded at boot by the App composition root
    # from ``settings.active_gpu`` — the universal path every UI shares, so the
    # selected GPU drives the metric regardless of which UI made the choice.
    _preferred_gpu_key: str | None = None

    # Dedupe key for the "preferred GPU absent" warning: ``primary_gpu`` runs
    # every tick, so we warn ONCE per distinct missing key (reset when the
    # preferred GPU reappears or the preference changes) instead of per poll —
    # otherwise one stale preference floods the log with identical lines.
    _warned_missing_gpu_key: str | None = None

    # ── Structured access ───────────────────────────────────────────
    @abstractmethod
    def cpu(self) -> CpuSource: ...

    @abstractmethod
    def memory(self) -> MemorySource: ...

    @abstractmethod
    def gpus(self) -> list[GpuSource]:
        """All detected GPUs, sorted discrete-first.  Empty if no GPU."""

    @abstractmethod
    def fans(self) -> list[FanSource]:
        """All detected fans.  Empty if none."""

    def set_preferred_gpu(self, gpu_key: str | None) -> None:
        """Pin which GPU ``primary_gpu()`` returns (``''``/``None`` = auto)."""
        normalized = gpu_key or None
        log.info("set_preferred_gpu: %s -> %s",
                 self._preferred_gpu_key, normalized)
        self._preferred_gpu_key = normalized
        # A fresh choice re-arms the missing-GPU warning.
        self._warned_missing_gpu_key = None

    def primary_gpu(self) -> GpuSource | None:
        """The user-preferred GPU if one is set and still present, else the
        first discrete GPU, else first integrated, else None."""
        gpus = self.gpus()
        if self._preferred_gpu_key is not None:
            for gpu in gpus:
                if gpu.key == self._preferred_gpu_key:
                    # Preferred is back — re-arm the warning for a future drop.
                    self._warned_missing_gpu_key = None
                    return gpu
            # Runs every tick; warn once per distinct missing key (see field).
            if self._warned_missing_gpu_key != self._preferred_gpu_key:
                log.warning(
                    "primary_gpu: preferred %s not among %s — auto-picking",
                    self._preferred_gpu_key, [g.key for g in gpus])
                self._warned_missing_gpu_key = self._preferred_gpu_key
        for gpu in gpus:
            if gpu.is_discrete:
                return gpu
        return gpus[0] if gpus else None

    def snapshot(self) -> HardwareMetrics:
        """Typed metrics snapshot — one fresh object per tick, raw °C.

        Concrete template method: reads the TYPED sources directly
        (``cpu()`` / ``primary_gpu()`` / ``memory()``) so the metrics a
        cooler displays never go through a fragile ``sensor_id``→attr
        string table, and folds disk/net (computed-IO, no typed source)
        in from ``read_all()``.  Every OS inherits this unchanged.

        Returns RAW canonical units (°C); callers apply user prefs via
        :func:`trcc.services.metrics_personalize.personalize_metrics`.
        ``cpus``/``gpus`` carry every detected unit (single-element on
        consumer hardware); the scalar fields collapse them — ``cpu_temp``
        is the hottest socket, ``cpu_percent`` the average — so the one
        number a cooler shows stays correct when sources widen to plural.
        """
        from .models import CpuMetrics, GpuMetrics, HardwareMetrics

        readings = self.read_all()
        cpu = self.cpu()
        # _safe (not _or_zero(fn())): a raising source degrades to 0.0 instead of
        # taking down snapshot() → the SensorsUpdated publish → every UI metric.
        cpus = [CpuMetrics(
            name=cpu.name,
            temp=_safe(cpu.temp), usage=_safe(cpu.usage),
            freq=_safe(cpu.freq), power=_safe(cpu.power),
        )]
        gpus = [GpuMetrics(
            name=g.name,
            temp=_safe(g.temp), usage=_safe(g.usage),
            clock=_safe(g.clock), power=_safe(g.power),
        ) for g in self.gpus()]
        primary = self.primary_gpu()
        mem = self.memory()
        # Fan slots the DC can show (CPUFAN / GPUFAN / SSDFAN / FAN2).
        # snapshot() populated every other field but never called self.fans(),
        # so all four defaulted to 0.0 — every theme showed 0 RPM on every
        # board (#145/#207).
        #
        # The GPU fan is the one slot we can identify with certainty: it belongs
        # to the GPU the user already picked, so it FOLLOWS the GPU picker —
        # ``primary_gpu().fan()`` (a duty-cycle percent, all the driver exposes;
        # not RPM).  Linux has no ``fanN_label`` for the motherboard headers, so
        # CPU/SSD/SYS2 fill from the device's still-spinning fans in discovery
        # order (a 0-RPM header is an empty header, skipped).  The GPU's own
        # hwmon fan (e.g. ``amdgpu``) is excluded from that pool so it is never
        # double-counted as a case fan.
        fan_gpu = _safe(primary.fan) if primary else 0.0
        pool = iter(
            rpm for f in self.fans()
            if "gpu" not in f.key.lower() and (rpm := f.rpm())
        )
        fan_cpu = next(pool, 0)
        fan_ssd = next(pool, 0)
        fan_sys2 = next(pool, 0)
        metrics = HardwareMetrics(
            cpu_temp=max((c.temp for c in cpus), default=0.0),
            cpu_percent=(sum(c.usage for c in cpus) / len(cpus)) if cpus else 0.0,
            cpu_freq=max((c.freq for c in cpus), default=0.0),
            cpu_power=sum(c.power for c in cpus),
            gpu_temp=_safe(primary.temp) if primary else 0.0,
            gpu_usage=_safe(primary.usage) if primary else 0.0,
            gpu_clock=_safe(primary.clock) if primary else 0.0,
            gpu_power=_safe(primary.power) if primary else 0.0,
            mem_percent=_safe(mem.percent),
            mem_available=_safe(mem.available),
            mem_used=readings.get("memory:used", 0.0),
            mem_temp=readings.get("memory:temp", 0.0),
            mem_clock=readings.get("memory:clock", 0.0),
            disk_temp=readings.get("disk:temp", 0.0),
            disk_activity=readings.get("disk:activity", 0.0),
            disk_read=readings.get("disk:read", 0.0),
            disk_write=readings.get("disk:write", 0.0),
            net_up=readings.get("net:up", 0.0),
            net_down=readings.get("net:down", 0.0),
            net_total_up=readings.get("net:total_up", 0.0),
            net_total_down=readings.get("net:total_down", 0.0),
            fan_cpu=fan_cpu, fan_gpu=fan_gpu,
            fan_ssd=fan_ssd, fan_sys2=fan_sys2,
            readings=readings,
            cpus=cpus,
            gpus=gpus,
        )
        log.debug(
            "snapshot: cpus=%d gpus=%d cpu_temp=%.1f cpu_pct=%.1f "
            "gpu_temp=%.1f fans(rpm)=%s", len(cpus), len(gpus),
            metrics.cpu_temp, metrics.cpu_percent, metrics.gpu_temp,
            (fan_cpu, fan_gpu, fan_ssd, fan_sys2),
        )
        return metrics

    # ── Flat dict view (for overlay lookups) ────────────────────────
    @abstractmethod
    def discover(self) -> list[SensorReading]:
        """One SensorReading per normalized key.  Snapshot at call time."""

    @abstractmethod
    def read_all(self) -> dict[str, float]:
        """Current readings keyed by normalized name.  Omits None values."""

    @abstractmethod
    def read_one(self, sensor_id: str) -> float | None:
        """Read a single normalized key."""

    @abstractmethod
    def start_polling(self, interval_s: float = 2.0) -> None: ...

    @abstractmethod
    def stop_polling(self) -> None: ...


# =========================================================================
# Paths — where user data lives on this OS
# =========================================================================


class Paths(ABC):
    """Filesystem locations.  Each OS resolves these differently.

    Resolution-aware helpers (`theme_dir`, `cloud_theme_dir`,
    `cloud_mask_dir`, `user_mask_dir`) are concrete on the ABC because
    every OS uses the same subpath layout — only the root differs.

    Layout convention: ``data_dir()`` is the package + cloud-downloaded
    content root (``~/.trcc/data/``); ``user_data_dir()`` is the
    user-saved content root (``~/.trcc-user/data/``).  Both carry the
    identical per-resolution sub-tree — resolving a default vs a user
    asset differs only by which root you start from:

        <root>/theme{w}{h}/<name>/      (themes)
        <root>/web/{w}{h}/              (backgrounds)
        <root>/web/zt{w}{h}/<id>/       (masks + their config1.dc)

    ``user_content_dir()`` is the parent (``~/.trcc-user/``); user data
    lives under its ``data/`` child so the two trees mirror exactly.
    """

    @abstractmethod
    def config_dir(self) -> Path: ...

    @abstractmethod
    def data_dir(self) -> Path: ...

    @abstractmethod
    def user_content_dir(self) -> Path: ...

    @abstractmethod
    def log_file(self) -> Path: ...

    def user_data_dir(self) -> Path:
        """User-saved content root — mirrors :meth:`data_dir`'s ``/data``
        layout under :meth:`user_content_dir`.

        Concrete on the ABC: every OS roots user content at
        ``user_content_dir() / "data"``, so the user sub-tree is the
        byte-for-byte twin of the default one and resolution differs
        only by which root you start from.
        """
        frame_log.debug("user_data_dir: called")
        return self.user_content_dir() / "data"

    def theme_dir(self, width: int, height: int, variant: str = "") -> Path:
        """Themes shipped with the app or downloaded from GitHub releases.

        *variant* is the per-SKU artwork suffix from
        ``core.protocol.artwork_variant`` -- ``""`` for every panel except the
        1600x720 pair, which has separate libraries at SUB 2/3/4.  It defaults
        to the unsuffixed library, so a caller that does not know the device's
        SUB gets exactly what it got before.
        """
        log.debug("theme_dir: %dx%d variant=%r", width, height, variant)
        return self.data_dir() / f"theme{width}{height}{variant}"

    def user_theme_dir(self, width: int, height: int) -> Path:
        """Per-resolution user-saved theme dir.

        Same subpath as :meth:`theme_dir`, rooted at :meth:`user_data_dir`.
        """
        log.debug("user_theme_dir: %dx%d", width, height)
        return self.user_data_dir() / f"theme{width}{height}"

    def cloud_theme_dir(self, width: int, height: int,
                        variant: str = "") -> Path:
        """Cloud-catalog themes (backgrounds) downloaded at runtime.

        Same *variant* suffix as :meth:`theme_dir`.
        """
        log.debug("cloud_theme_dir: %dx%d variant=%r", width, height, variant)
        return self.data_dir() / "web" / f"{width}{height}{variant}"

    def user_background_dir(self, width: int, height: int) -> Path:
        """Per-resolution user-saved backgrounds.

        Same subpath as :meth:`cloud_theme_dir`, rooted at
        :meth:`user_data_dir` — user backgrounds mirror cloud ones.
        """
        log.debug("user_background_dir: %dx%d", width, height)
        return self.user_data_dir() / "web" / f"{width}{height}"

    def cloud_mask_dir(self, width: int, height: int,
                       variant: str = "") -> Path:
        """Cloud-catalog masks downloaded at runtime.

        Takes ``core.protocol.mask_variant`` rather than ``artwork_variant``:
        masks have one arm the other libraries do not (480x480 at PM 3).
        """
        log.debug("cloud_mask_dir: %dx%d variant=%r", width, height, variant)
        return self.data_dir() / "web" / f"zt{width}{height}{variant}"

    def user_mask_dir(self, width: int, height: int) -> Path:
        """User-created masks — survives uninstall + redownload.

        Same subpath as :meth:`cloud_mask_dir`, rooted at
        :meth:`user_data_dir` — user masks mirror cloud ones.
        """
        log.debug("user_mask_dir: %dx%d", width, height)
        return self.user_data_dir() / "web" / f"zt{width}{height}"

    def user_screencast_dir(self) -> Path:
        """User screencast configs (captured region + params) that a saved
        theme references.  Not resolution-keyed — a screencast is a live
        region descriptor, not a per-resolution asset."""
        log.debug("user_screencast_dir")
        return self.user_data_dir() / "screencast"

    def user_media_player_dir(self) -> Path:
        """User media-player configs (source URI) that a saved theme
        references.  Not resolution-keyed — the source is a path/URL, scaled
        at play time."""
        log.debug("user_media_player_dir")
        return self.user_data_dir() / "media_player"


# =========================================================================
# ContentStore — the filesystem port
# =========================================================================
#
# ``Paths`` answers *where* a thing belongs; nothing answered *put it there*,
# so the inner rings called ``shutil`` / ``zipfile`` / ``pathlib`` directly —
# 72 times.  Storage was the one outbound dependency of the 23 declared here
# with no port, and it drifted exactly where it was unmeasured.
#
# The surface below is EXTRACTED, not designed: every method is a call the
# real consumers already make (``core/commands/theme.py``, ``services/
# display.py``, ``core/commands/_helpers.py``).  ONE port, not a read/write/
# archive triple — three ABCs that one class implements is not ISP, and no
# partial implementor exists.  The day one does, split it then.
#
# The read side returns ``Path``, deliberately.  ``display.py`` hands
# ``background_path`` straight to the renderer and video goes to ffmpeg, an
# external process that needs a real file; a bytes-only port would force a
# rewrite of render + decode and still lose.  ``CloudCatalog.download_theme``
# already sets that precedent.


class SingleFileTheme(ABC):
    """A one-file theme directory being assembled — yielded by
    :meth:`ContentStore.single_file_theme`.

    ``LoadImage`` and ``LoadVideo`` each turn an arbitrary file the user
    picked into a minimal theme so it lists like any other and the ordinary
    ``LoadTheme`` path renders it.  Both hand-rolled the same twenty lines;
    this is the seam those lines collapsed into.
    """

    __slots__ = ()

    path: Path
    name: str

    @abstractmethod
    def install(self, source: Path, filename: str) -> Path:
        """Copy *source* in as *filename*, skipping an unchanged re-run."""

    @abstractmethod
    def adopt(self, produced: Path, filename: str) -> Path:
        """Move an already-produced file (a transcoder output) in as
        *filename*, and clear the temp directory it came from."""


class ContentStore(ABC):
    """Where themes, masks, backgrounds and capture configs are kept.

    Concrete: ``FileContentStore`` (``adapters/theme/filesystem.py``) — a filesystem store
    under ``data_dir()`` / ``user_data_dir()``.
    """

    # ── Writers ───────────────────────────────────────────────────────

    @abstractmethod
    def stage(self, target: Path) -> AbstractContextManager[Path]:
        """Build a content unit in a sibling dir, then swap it over *target*.

        Yields the staging directory.  A clean exit swaps it into place; ANY
        exception discards it and leaves *target* exactly as it was, so the
        caller writes its files and does not think about rollback.

        Staging rather than writing into the target is load-bearing: the unit
        being saved is usually the target ITSELF (a prior save re-points the
        active theme at the saved dir), so clearing it first would destroy the
        source's own background and mask before they had been read.
        """

    @abstractmethod
    def single_file_theme(
        self, source: Path, kind: str,
    ) -> AbstractContextManager[SingleFileTheme]:
        """A theme directory wrapping ONE file — an image or a video.

        Yields the unit; the caller installs its one payload file.  The theme
        marker is written LAST, on a clean exit, so a payload that fails
        half-way leaves a markerless directory the listing skips.
        """

    @abstractmethod
    def store_background(
        self, data: bytes, ext: str, width: int, height: int,
    ) -> str:
        """Store a background in the user library; return its manifest ref.

        Identical bytes dedup to one asset.  *ext* must name a shippable
        background container; anything else raises ``ThemeError``.
        """

    @abstractmethod
    def store_mask(
        self, image: bytes, width: int, height: int,
        *, dc: bytes | None = None, name: str | None = None,
    ) -> str:
        """Store a mask unit (``01.png`` + preview + optional DC); return its ref.

        Two keying modes, because two callers mean different things:

        * *name* given — the user NAMED this mask by uploading a file, and the
          name is what they will see in the browser.  It is not a promise about
          the bytes, so a re-store under the same name REPLACES.
        * *name* omitted — the mask was captured implicitly (saving a theme
          copies a loose mask into the library).  There is no user-facing
          identity, so it is content-addressed and identical bytes dedup.

        The id hashes the image **plus its DC**, so two themes sharing a mask
        image but carrying different metrics get distinct units each with its
        own layout — hashing the image alone would collapse them and the
        first DC would win.
        """

    @abstractmethod
    def store_screencast(
        self, region: tuple[int, int, int, int, bool],
    ) -> str:
        """Store a screencast region config; return its ref.

        Not resolution-keyed — a screencast is a live region descriptor, not
        a per-resolution asset.
        """

    @abstractmethod
    def store_media_player(self, uri: str) -> str:
        """Store a media-player source URI; return its ref.  The URI may be a
        local path or a URL/stream — it is stored verbatim."""

    # ── Readers ───────────────────────────────────────────────────────

    @abstractmethod
    def load(self, path: Path) -> Theme:
        """Load a theme directory into a ``Theme``.

        Raises ``ThemeError`` if the directory is missing, unreadable, or its
        config is invalid.
        """

    @abstractmethod
    def list(self, directory: Path) -> builtins.list[Theme]:
        """Every theme directly under *directory*.

        An invalid theme is skipped with a warning, never raised — listing
        never fails on one bad theme.
        """

    @abstractmethod
    def list_web_previews(
        self, web_dir: Path,
    ) -> builtins.list[WebPreviewInfo]:
        """The downloaded cloud-theme previews under *web_dir*."""

    @abstractmethod
    def discover_masks(
        self,
        cloud_masks_dir: Path | None = None,
        user_masks_dir: Path | None = None,
    ) -> builtins.list[DiscoveredMask]:
        """Mask metadata from the cloud + user mask dirs.

        Cloud (shipped) first, then user — neither hides the other.  Deduped
        by resolved path, so a same-id user + cloud pair both list.
        """

    @abstractmethod
    def resolve_ref(self, ref: str) -> Path | None:
        """Resolve a manifest ref minted by a ``store_*`` method to a path.

        The store mints refs, so the store resolves them — a caller that has
        just stored something must not re-derive where it went by spelling the
        library layout a second time.
        """

    @abstractmethod
    def is_theme_dir(self, path: Path) -> bool:
        """True iff *path* is a directory carrying a theme config.

        The question ``_search_theme_by_name`` and the listing both ask.  It
        is a store question, not a caller one: which marker files count is
        this store's layout knowledge.
        """

    @abstractmethod
    def screencast_region(
        self, theme: Theme,
    ) -> tuple[int, int, int, int, bool] | None:
        """Resolve *theme*'s screencast ref → ``(x, y, w, h, audio)``."""

    @abstractmethod
    def media_player_uri(self, theme: Theme) -> str | None:
        """Resolve *theme*'s media-player ref → its source URI."""

    @abstractmethod
    def background_path(self, theme: Theme) -> Path | None:
        """*theme*'s background — a referenced library asset, or the in-dir
        static/video file.

        Never the panel thumbnail: returning it would ship the tile to the
        device.
        """

    @abstractmethod
    def video_path(self, theme: Theme) -> Path | None:
        """*theme*'s video, bundled or referenced.

        Separate from :meth:`background_path` so ``LoadTheme`` can choose
        between playing a video and rendering a static frame without
        inspecting a suffix.
        """

    @abstractmethod
    def mask_path(self, theme: Theme) -> Path | None:
        """*theme*'s mask overlay — referenced library unit or in-dir."""

    @abstractmethod
    def preview_path(self, theme: Theme) -> Path | None:
        """*theme*'s panel thumbnail — the browser tile, distinct from what
        the renderer ships to the LCD."""

    # ── Whole units in and out ────────────────────────────────────────

    @abstractmethod
    def export(self, theme_path: Path, archive_path: Path) -> None:
        """Archive a theme as a self-contained, shareable zip.

        DEREFERENCES: a saved theme references its assets in the user
        library, so the resolved bytes are bundled and the ref keys stripped
        — the recipient needs nothing from the sender's library.
        """

    @abstractmethod
    def import_(self, archive_path: Path, into_dir: Path) -> Theme:
        """Unpack a theme archive into *into_dir*.

        Rejects zip-slip; a failed extraction cleans up the partial
        destination rather than leaving a half-written theme.
        """

    @abstractmethod
    def export_dc(
        self, theme_dir: Path, output_path: Path,
        *, elements: list[dict] | None = None,
    ) -> Path:
        """Write *theme_dir*'s config out in the legacy binary layout — for
        sharing with Windows TRCC users.

        *elements* REPLACES the theme's own layout when given: the caller
        passes what the device is actually showing.
        """

    @abstractmethod
    def delete(self, directory: Path, name: str) -> Path:
        """Delete the theme ``directory / name``.

        Confined to *directory* — callers pass the trusted root and the
        target is verified to stay inside it.
        """


# =========================================================================
# Renderer — pixel operations (PySide6 on all OSes today)
# =========================================================================


class Renderer(ABC):
    """Rendering backend.  Concrete: QtRenderer (adapters/render/qt.py)."""

    # ── Surfaces ──────────────────────────────────────────────────────
    @abstractmethod
    def create_surface(self, width: int, height: int,
                       color: tuple[int, ...] | None = None) -> Any: ...

    @abstractmethod
    def open_image(self, path: Path) -> Any: ...

    @abstractmethod
    def surface_size(self, surface: Any) -> tuple[int, int]: ...

    # ── Compositing ───────────────────────────────────────────────────
    @abstractmethod
    def composite(self, base: Any, overlay: Any,
                  position: tuple[int, int],
                  mask: Any | None = None) -> Any: ...

    @abstractmethod
    def resize(self, surface: Any, width: int, height: int) -> Any: ...

    @abstractmethod
    def rotate(self, surface: Any, degrees: int) -> Any: ...

    @abstractmethod
    def flip_horizontal(self, surface: Any) -> Any:
        """Return a horizontally-mirrored copy of *surface*.

        Used by the split-mode (Dynamic Island) overlay path:
        authored assets cover the left side of the canvas, so the
        renderer flips them when the device's PanelCutout sits on
        the right.
        """
        ...

    # ── Adjustments ───────────────────────────────────────────────────
    @abstractmethod
    def apply_brightness(self, surface: Any, percent: int) -> Any: ...

    # ── Text ──────────────────────────────────────────────────────────
    # NOTE: (x, y) is the text CENTER, not top-left.  Matches TRCC 2.1.6
    # (``UCScreenImage.cs:1137`` and ``:1544``), where every overlay element
    # is drawn into ``RectangleF(myX - w/2, myY - h/2, w, h)``.  Re-verified
    # against 2.1.6 on 2026-08-19; the previous citation pointed at the
    # wiped single-file 2.0.3 extraction and could not be checked.
    # DC files store element coordinates as centers; the renderer is
    # the only layer that knows font metrics, so the center-to-baseline
    # math lives here, not in OverlayService.
    @abstractmethod
    def draw_text(self, surface: Any, x: int, y: int, text: str,
                  color: str, size: int, bold: bool = False,
                  italic: bool = False, family: str = "") -> None: ...

    # ── Encoding ──────────────────────────────────────────────────────
    @abstractmethod
    def encode_rgb565(self, surface: Any, byte_order: str = ">") -> bytes: ...

    @abstractmethod
    def encode_jpeg(self, surface: Any, quality: int = 95,
                    max_size: int = 0) -> bytes: ...

    def encode_png(self, surface: Any) -> bytes:
        """Encode the surface as PNG bytes.

        Used by ``GET /devices/{key}/display/preview`` to return a
        dashboard-friendly frame snapshot.  Lossless so screenshots
        + overlay text stay legible, unlike ``encode_jpeg``.

        Non-abstract — concrete Renderers (only QtRenderer in next/
        today) override; the default raises so test fakes that don't
        exercise the preview path stay minimal.
        """
        del surface
        raise NotImplementedError("encode_png not implemented on this Renderer")

    def get_pixels_rgb(
        self, surface: Any, cols: int, rows: int,
    ) -> list[list[tuple[int, int, int]]]:
        """Sample the surface into a ``rows × cols`` RGB grid.

        Used by ANSI terminal previews (``trcc display test-lcd``) and
        the future "screen LED" feature (sample LCD content → LED
        zone colors).  The grid is row-major: ``out[y][x]`` is the
        ``(r, g, b)`` for column *x* on row *y*.

        Non-abstract — test fakes that don't exercise CLI ANSI
        previews stay minimal.
        """
        del surface, cols, rows
        raise NotImplementedError(
            "get_pixels_rgb not implemented on this Renderer",
        )

    # ── Frame assembly (Template Method) ──────────────────────────────
    # Consolidation increment 2c: the single compose→encode skeleton, shared
    # by every wire.  Concrete here (reuses the abstract primitives above);
    # `DisplayService` resolves the `RenderContent`, owns caching / sensors /
    # brightness / split-mode / preview capture, and wraps this core.  Returns
    # the encoded PAYLOAD — the device adapter adds its own wire header.
    def build_frame(
        self,
        profile: DeviceProfile,
        content: RenderContent,
        orientation: int,
        content_is_portrait: bool,
    ) -> bytes:
        """Compose one oriented frame and encode its wire payload.

        Fixed skeleton (Template Method): pick the oriented compose canvas, fit
        the background (the C# native-or-black width test — increment 2b),
        composite the overlay, apply the single wire rotation, encode.  The
        geometry decision (:func:`plan_orientation`) and wire angle
        (:func:`wire_angle`) are the shared pure functions the live
        ``DisplayService.build_frame`` already keys on, so this is behaviour-
        preserving by construction.
        """
        from .geometry import plan_orientation
        from .protocol import wire_angle

        plan = plan_orientation(profile, orientation, content_is_portrait)
        canvas = self.create_surface(
            plan.canvas[0], plan.canvas[1], color=(0, 0, 0, 255),
        )
        canvas = self.bg_fit(canvas, content)
        if content.overlay is not None:
            canvas = self.composite(canvas, content.overlay, position=(0, 0))
        if plan.post_rotate:
            canvas = self.rotate(canvas, plan.post_rotate)
        else:
            angle = wire_angle(profile, orientation, plan.is_portrait_content)
            if angle % 360:
                canvas = self.rotate(canvas, angle)
        return self.encode_payload(canvas, profile)

    def bg_fit(self, canvas: Any, content: RenderContent) -> Any:
        """Draw the background onto ``canvas`` — the C# native-or-black rule.

        Program/cloud content (``UCScreenImage.cs:824-834``): native at (0,0)
        when it fits the canvas width, else the canvas stays solid black — never
        letterboxed.  User uploads arrive pre-fitted from ``DisplayService`` and
        are composited as-is.  ``None`` background → solid black.
        """
        if content.background is None:
            log.debug("bg_fit: no background source → solid black canvas")
            return canvas
        src_w, src_h = self.surface_size(content.background)
        dst_w, dst_h = self.surface_size(canvas)
        if content.background_is_user:
            frame_log.debug("bg_fit: user background %dx%d composited as-is",
                            src_w, src_h)
            return self.composite(canvas, content.background, position=(0, 0))
        if src_w <= dst_w + 2:
            frame_log.debug("bg_fit: program background %dx%d ≤ canvas %dx%d → "
                            "native at (0, 0)", src_w, src_h, dst_w, dst_h)
            return self.composite(canvas, content.background, position=(0, 0))
        log.warning("bg_fit: program background %dx%d exceeds canvas %dx%d → "
                    "solid black (C# width test, no letterbox); bg not shown "
                    "at this orientation", src_w, src_h, dst_w, dst_h)
        return canvas

    def encode_payload(self, surface: Any, profile: DeviceProfile) -> bytes:
        """Encode the composed surface to the wire payload.

        Mirrors ``DisplayService._encode_for_wire``: a fixed hardware-mount
        baseline (``encode_baseline`` — FW360 PM=6 → 180°) pre-rotates the wire
        frame, then JPEG or RGB565 per the profile.
        """
        if profile.encode_baseline:
            frame_log.debug("encode_payload: hardware-mount baseline %d° "
                            "pre-rotate", profile.encode_baseline)
            surface = self.rotate(surface, profile.encode_baseline)
        if profile.jpeg:
            frame_log.debug("encode_payload: JPEG, max_size=%s",
                            profile.max_frame_bytes)
            # max_frame_bytes drives encode_jpeg's shrink-quality loop, and is
            # the C#'s 450000 ceiling for EVERY JPEG panel — the test in
            # ImageToJpg carries no device condition.  It used to default to 0
            # (uncapped) with only LY setting it, which left every other JPEG
            # panel able to ship a frame the firmware silently discards (#251).
            return self.encode_jpeg(surface, max_size=profile.max_frame_bytes)
        frame_log.debug("encode_payload: RGB565, byte order %s",
                        profile.byte_order)
        return self.encode_rgb565(surface, profile.byte_order)

    # ── Fonts ─────────────────────────────────────────────────────────
    def list_fonts(self) -> list[str]:
        """Enumerate the font families the renderer can draw with.

        The source the GUI font picker reads.  Non-abstract — the default
        returns ``[]`` ("none enumerable"), the headless-safe degradation,
        so minimal test fakes inherit it.  The concrete Qt renderer
        overrides with the real font database.  Lives behind the port so
        core never imports a GUI toolkit to ask "what fonts exist?".
        """
        return []

    # ── Legacy boundary (video frames) ────────────────────────────────
    @abstractmethod
    def from_raw_rgb24(self, frame: RawFrame) -> Any: ...

    @abstractmethod
    def to_raw_rgb24(self, surface: Any) -> RawFrame:
        """A surface back to packed RGB24 — the inverse of the above.

        Exists because only half the pair did.  A surface is opaque to core,
        so a caller holding one and needing to hand it to something that
        speaks ``RawFrame`` had nowhere to convert — and the gui screencast
        simply passed the surface, where the attribute access on ``.data`` /
        ``.width`` blew up every frame.
        """

    @abstractmethod
    def surface_nbytes(self, surface: Any) -> int:
        """How many bytes of pixel data *surface* occupies.

        A surface is opaque to core, so a caller that wants to BOUND how
        many it retains cannot measure one — it can only count them, and a
        count is not a size when the same count costs 59 MB on a 320x320
        panel and 3,964 MB on a 1600x720 one (#264).  Asking the adapter,
        which knows its own pixel format, is the difference between a cache
        capped in bytes and a cache capped in wishes.
        """


    @abstractmethod
    def decode_image(self, data: bytes) -> Any:
        """Decode encoded image *bytes* (JPEG/PNG) to a surface.

        The counterpart to :meth:`encode_jpeg` / :meth:`encode_png`, and the
        reason a video playback can hold its frames compressed: one frame is
        decoded per tick instead of the whole animation being held as raw
        pixels.  Lives behind the port because ``services/`` must not import
        an imaging toolkit.
        """


# =========================================================================
# Diagnostics — health / doctor / debug-report / package-mgr / gpu-reader
# =========================================================================


class Diagnostics(ABC):
    """Port for system diagnostics.  Concrete: ``DiagnosticsAdapter``
    (``adapters/diagnostics/adapter.py``).

    The diagnostics adapters consume the ``Platform`` port to probe the
    machine; this port lets core Commands (``RunHealthCheck``, ``RunDoctor``,
    ``GenerateDebugReport``, ``RunUpgrade``) and the ``QuickstartService`` reach
    that work through an injected interface instead of importing the adapter —
    so core stays pure.  Debug reports cross as rendered text, not a struct, so
    the ``DebugReport`` bundle stays an adapter implementation detail.
    """

    @abstractmethod
    def health(self) -> HealthReport:
        """Run the full health-check suite."""
        ...

    @abstractmethod
    def doctor(self) -> DoctorResult:
        """Run health checks + the exit-code verdict."""
        ...

    @abstractmethod
    def render_doctor(self, report: HealthReport) -> str:
        """Render a health report as the CLI-friendly doctor summary."""
        ...

    @abstractmethod
    def debug_report(self, log_tail_lines: int) -> str:
        """Build the debug bundle and return its rendered, paste-ready text."""
        ...

    @abstractmethod
    def write_debug_report(self, rendered: str, path: Path) -> Path:
        """Write already-rendered debug text to *path*; return the path."""
        ...

    @abstractmethod
    def package_manager(self) -> str | None:
        """Detect the system package manager (``apt``/``dnf``/…), or ``None``."""
        ...

    @abstractmethod
    def gpu_reader_state(self) -> GpuReaderState:
        """NVIDIA NVML reader presence / init state for the install prompt."""
        ...


# =========================================================================
# DataInstaller — fetch + extract per-resolution data archives
# =========================================================================


class DataInstaller(ABC):
    """Port for installing on-demand data archives (themes / web / masks).

    Concrete: ``HttpDataInstaller`` (``adapters/repo/data_install.py``), which
    downloads from GitHub releases and extracts.  ``DataInstallService`` depends
    on this port so the service layer never names the HTTP/extraction adapter.
    """

    @abstractmethod
    def install(
        self, archive_name: str, target_dir: Path, *, subpath: str = "",
    ) -> bool:
        """Fetch *archive_name* and extract into *target_dir*; True if populated."""
        ...


# =========================================================================
# CloudCatalog — read side of the hosted theme catalog
# =========================================================================


class CloudCatalog(ABC):
    """Port for the hosted cloud theme catalog.

    Concrete: ``CzhordeCatalog`` (``adapters/theme/cloud.py``).  ``CloudTheme
    Service`` depends on this port (+ the ``CloudCategory`` / ``CloudThemeEntry``
    DTOs in ``core.models``) so the service never names the catalog adapter.
    """

    @abstractmethod
    def categories(self) -> tuple[CloudCategory, ...]:
        """The catalog's category table."""
        ...

    @abstractmethod
    def list_themes(self, category: str = "all") -> list[CloudThemeEntry]:
        """Enumerate theme entries in *category* (or all categories)."""
        ...

    @abstractmethod
    def download_theme(self, theme_id: str, resolution: str | None = None) -> Path:
        """Fetch ``<theme_id>.mp4`` (cached); return its local path."""
        ...

    @abstractmethod
    def download_preview(self, theme_id: str, resolution: str | None = None) -> Path:
        """Fetch ``<theme_id>.png`` (cached); return its local path."""
        ...


# =========================================================================
# ScreenCapture — grab a region of the user's desktop as raw RGB bytes
# =========================================================================


class ScreenCapture(ABC):
    """Port for "grab a rectangle off the desktop right now".

    Adapters: :class:`QtScreenCapture` for Qt apps (X11 + Wayland
    fallback).  Used by the screencast pipeline to feed live desktop
    pixels into the device.

    Returns a :class:`RawFrame` with RGB24 pixel data sized exactly to
    the requested rectangle — callers handle scale/fit/encode.
    """

    @abstractmethod
    def grab_region(self, x: int, y: int, width: int, height: int) -> RawFrame:
        """Capture *width* × *height* pixels starting at (*x*, *y*).

        Raise :class:`OSError` (or subclass) on capture failure — the
        caller decides whether to retry, stop the screencast, or surface
        the error to the user.
        """
        ...


# =========================================================================
# HttpFetcher — minimal HTTP GET, abstracted so tests can intercept
# =========================================================================


class HttpFetcher(ABC):
    """Tiny port for "fetch bytes from URL" used by cloud-theme adapters.

    Separate from full ``requests``/``httpx`` use because next/'s needs
    are minimal — GET a small/medium body with a timeout, multi-server
    fallback handled by the caller.  Tests inject a fake that returns
    canned bytes; production uses ``UrllibHttpFetcher``.
    """

    @abstractmethod
    def fetch(self, url: str, timeout_s: float = 30.0) -> bytes:
        """Fetch a URL's body.  Raise on non-200 status or transport error."""
        ...


# =========================================================================
# AutostartManager — OS-specific boot-time launch configuration
# =========================================================================


class PackageManager(ABC):
    """What the OS's package manager can tell us about a missing tool.

    Exists because a static table of package names rots, and the rot ships.
    Four commands the app printed were verified broken on 2026-08-21 -- one
    named a package deleted from FreeBSD as vulnerable, one named a package
    that installs a differently-named binary, two named packages that do not
    exist.  Every one had been in a release.

    Two questions, deliberately separate because they cost differently:

    ``owns`` reads the LOCAL installed-package database -- ``rpm -q
    --whatprovides`` is 23 ms -- and never touches the network.  It answers
    "you already have this", which is the difference between useful advice and
    telling someone to install what is sitting on their disk.

    ``provides`` asks what WOULD supply a file, which needs repository
    metadata.  Implementations must use their manager's cache-only mode
    (``dnf -C``, ``--no-refresh``): the doctor runs on a broken machine, often
    offline, and must never trigger a refresh under the user.

    **None means "cannot determine", never "absent".**  That distinction
    decides whether a user is told to install something, and conflating the
    two is the defect this port exists to remove.  A manager that cannot
    answer says so, and the caller falls back to the static hint.
    """

    @abstractmethod
    def owns(self, path: str) -> str | None:
        """Installed package owning *path*, or None if none does / unknown."""

    @abstractmethod
    def provides(self, path: str) -> str | None:
        """Package that would supply *path*, from cache only.

        None when the manager cannot say — no cache, no such tool, a timeout.
        Never a guess.
        """

    @abstractmethod
    def installed(self, package: str) -> bool:
        """Whether *package* is installed, by NAME.

        Distinct from :meth:`owns`, which asks by file path.  Some advice
        depends on a package being present rather than on a binary: EPEL is
        the case that forced this — on RHEL/Rocky/Alma the packages our hints
        name are EPEL-only, so the correct command depends on whether
        ``epel-release`` is already there.  Local query, never the network.
        """

    @abstractmethod
    def install_argv(self, package: str) -> tuple[str, ...]:
        """Argv that installs *package*.  Empty when this OS has no manager."""


class AutostartManager(ABC):
    @abstractmethod
    def is_enabled(self) -> bool: ...

    @abstractmethod
    def enable(self) -> None: ...

    @abstractmethod
    def disable(self) -> None: ...

    @abstractmethod
    def refresh(self) -> None: ...


# =========================================================================
# HotplugMonitor — OS-specific add/remove + sleep/wake listener
# =========================================================================


class HotplugMonitor(ABC):
    """Background listener that pushes hardware events onto the EventBus.

    Implementations spawn one daemon thread that translates OS-native
    udev / IOKit / WM_DEVICECHANGE notifications into
    :class:`DeviceAttached` / :class:`DeviceDetached` (for registry-known
    vid:pid combos) and, where the OS exposes it,
    :class:`SystemSuspending` / :class:`SystemResumed`.

    UIs / Commands never call into the monitor directly — they subscribe
    to the EventBus.
    """

    @abstractmethod
    def start(self, bus: EventBus) -> None:
        """Begin listening.  Idempotent — calling twice is a no-op."""

    @abstractmethod
    def stop(self) -> None:
        """Stop listening + clean up the listener thread."""

    @property
    @abstractmethod
    def is_running(self) -> bool: ...


# =========================================================================
# Platform — OS root, one instance per app
# =========================================================================


class Platform(ABC):
    """OS abstraction.  DI'd into App at startup.

    Responsibilities:
        - Enumerate attached devices (scan_devices).
        - Open USB handles (open_usb).
        - Expose sensors, paths, autostart.
        - Run OS-specific setup (udev, WinUSB guide, etc.).
    """

    # ── Transport factory — ONE method, wire-agnostic ─────────────────
    @abstractmethod
    def open_transport(self, wire: Wire, vid: int, pid: int,
                       serial: str | None = None) -> Transport:
        """Return an unopened transport for *wire*.

        **The port must not name a wire.**  It used to: separate
        ``open_bulk`` / ``open_scsi`` abstract methods meant every OS
        implemented a wire-named method, and both callers branched on
        ``wire is Wire.SCSI`` to choose between them — so a seventh wire
        needing a new kernel interface cost a new abstract method here plus
        an implementation in all four OS adapters.  Wire × OS, the one place
        in this design where two axes multiplied.

        Which kernel interface a wire needs is a per-``(OS, wire)`` fact, and
        it belongs in a *table inside the OS adapter* — exactly how
        ``adapters/system/_udev.py`` already keys subsystem names by ``Wire``.
        ``BaseOS`` provides that table plus the shared bulk path; an OS only
        supplies the bodies that genuinely differ.

        Bulk (libusb) serves HID / BULK / LY / LED identically on
        every OS.  SCSI is the one wire needing a native path per OS:
            Linux   → SG_IO ioctl on /dev/sgN
            Windows → DeviceIoControl on the raw volume
            macOS   → USB BOT (no SG equivalent)
            BSD     → USB BOT
        """

    @abstractmethod
    def scan_devices(self) -> list[DeviceInfo]:
        """Enumerate currently-attached supported devices."""

    # ── Filesystem ────────────────────────────────────────────────────
    @abstractmethod
    def paths(self) -> Paths: ...

    # ── Sensors ───────────────────────────────────────────────────────
    @abstractmethod
    def sensors(self) -> SensorEnumerator: ...

    # ── Autostart ─────────────────────────────────────────────────────
    @abstractmethod
    def autostart(self) -> AutostartManager: ...

    # ── Screen capture ────────────────────────────────────────────────
    @abstractmethod
    def screen_capture(self) -> ScreenCapture:
        """Grab a desktop rectangle — the source the screencast feed reads.

        Declared on the port because WHICH tool captures a screen is an
        OS fact: ``grim`` on wlroots, ``scrot`` on X11, and neither exists
        on Windows or macOS, where Qt's native grab is the whole answer.
        ``BaseOS`` supplies the Qt-backed adapter, which already degrades
        through those tools, so an OS only overrides this when it has
        something better.

        Here rather than injected like ``Renderer`` because a Command needs
        to reach it: the screencast driver runs core-side, and
        ``app.platform`` is what a Command has.
        """

    # ── Package manager (diagnostics; read-only) ──────────────────────
    @abstractmethod
    def packages(self) -> PackageManager:
        """This OS's package-manager query surface.

        Declared here rather than only on ``BaseOS`` for the reason every
        other member is: the port asks, so a new OS is told the question
        exists.  ``BaseOS`` answers it with "cannot be asked", which is the
        truthful default and keeps callers on the static install hint.
        """

    # ── Hotplug ───────────────────────────────────────────────────────
    @abstractmethod
    def hotplug(self) -> HotplugMonitor:
        """Return the OS hotplug listener.

        Caller manages lifecycle — typically the daemon starts it once
        on boot and stops it on shutdown.  Sub-Platforms that can't
        observe USB hotplug yield a no-op monitor.
        """

    # ── One-time setup (udev rules / WinUSB guide / etc.) ─────────────
    @abstractmethod
    def setup(self, interactive: bool = True) -> int:
        """Run OS-specific setup.  Returns a shell-style exit code."""

    @abstractmethod
    def check_permissions(self) -> list[str]:
        """Return a list of user-facing permission warnings, empty if OK."""

    # ── OS identity (for UIs, diagnostics, install hints) ─────────────
    @abstractmethod
    def distro_name(self) -> str: ...

    @abstractmethod
    def install_method(self) -> str:
        """How this app was installed: pip, rpm, deb, pacman, app-bundle..."""

    # ── Every remaining question, asked of every OS ───────────────────
    #
    # These carried concrete bodies here until 2026-08-21, which read as
    # generosity and worked as a trap: an OS that never implemented one still
    # returned a plausible value, so "this OS cannot tell" and "nobody wrote it
    # yet" produced identical output.  ``disk_info`` / ``memory_info`` were
    # advertised on the generated port page as free inheritance — and
    # inheriting them means reporting no disks and no memory.
    #
    # MEASURED: deleting ``LinuxOS.memory_info`` left the whole suite green
    # (3840 passed) and the GUI's DRAM panel silently blank, because the port
    # answered ``[]`` on its behalf.  All four OSes already implement all four
    # of the no-default group, which is the proof they were never optional.
    #
    # So the port asks; it does not answer.  Where a real shared default
    # exists it lives on ``BaseOS`` (``adapters/system/_base.py``), one MRO
    # step below, so a new OS still inherits it — but a new OS that skips one
    # of these fails at instantiation with a ``TypeError`` naming it, which is
    # the to-do list this contract exists to hand over.  There is no C# oracle
    # for the OS layer (TRCC has one OS and never abstracted it) and no VM for
    # macOS/BSD, so the interface is the entire substitute.

    @abstractmethod
    def usb_power_state(self, vid: int, pid: int) -> UsbPowerState | None:
        """The device's USB runtime-power state, or ``None`` if unknowable.

        Read-only.  TRCC never sets power policy — that is the udev rules'
        job (``adapters/system/_udev.py``); this only reports what the kernel
        currently thinks, so a failed handshake can be told apart from a
        SUSPENDED panel (#150).

        An OS with no such notion returns ``None`` — but it says so itself,
        because "this OS does not expose it" is an answer and silence is not.
        """

    # ── Per-OS diagnostic hints (DI'd into the doctor / health checks) ──
    #
    # Shared consumers (``adapters/diagnostics/health.py``) reach these through
    # the injected Platform — they NEVER hardcode a distro command, so the
    # advice is correct on Windows / macOS / BSD, not just the Linux dev box.

    @abstractmethod
    def package_manager(self) -> str:
        """The system package manager, or "" when this OS has none of ours."""

    @abstractmethod
    def upgrade_command(self) -> tuple[str, ...]:
        """Argv that upgrades trcc on this OS, or empty when there is none."""

    @classmethod
    def resolve(cls) -> type[Platform]:
        """The concrete class for this host — usually ``cls`` itself.

        ``sys.platform`` names most OSes precisely enough, but says only
        "linux" for every distro, so an OS whose variants differ overrides this
        to pick one.  One seam, so the factory never grows an ``if`` per OS.

        The one concrete member of this port: returning ``cls`` is a genuine
        shared default, and making six classes write ``return cls`` would be
        boilerplate holding no decision.
        """
        log.debug("%s.resolve: no refinement needed", cls.__name__)
        return cls

    @abstractmethod
    def software_install_hint(self, tool: str) -> str:
        """OS-correct one-line hint for installing a missing CLI tool.

        ``tool`` is a logical name — ``"ffmpeg"``, ``"7z"``, ``"python"``,
        ``"pynvml"``.  Linux → package manager, Windows → winget, macOS →
        brew, BSD → pkg / pkg_add.
        """

    @abstractmethod
    def no_devices_hint(self) -> str:
        """OS-correct guidance shown when no device is detected.

        Linux → udev rules, Windows → WinUSB driver, macOS → replug/Privacy.
        Lands in ``trcc report``, so a generic sentence naming no command is
        a round-trip we cannot afford.
        """

    @abstractmethod
    def permission_denied_hint(self) -> str:
        """OS-correct guidance for an ``EACCES`` USB error.

        Surfaces inline in the recovery tracker's WARNING log so users see the
        actionable next step (Linux → udev rules, Windows → WinUSB, macOS →
        sudo/Privacy).
        """

    # ── GUI / hardware-probe convenience ──────────────────────────────

    @abstractmethod
    def minimize_on_close(self) -> bool:
        """True if the GUI should minimize-to-tray on close instead of hiding.

        Windows expects minimize; Linux/macOS/BSD hide-to-tray.
        """

    @abstractmethod
    def configure_stdout(self) -> None:
        """Adjust the interpreter's stdout/stderr at startup if the OS
        needs it (Windows ↔ cp1252 console).

        Called from every UI entry point BEFORE ``configure_logging`` so the
        StreamHandler attaches to an already-UTF-8-safe stream.  Consoles that
        already speak UTF-8 need nothing.
        """

    @abstractmethod
    def worker_thread_context(self) -> AbstractContextManager[None]:
        """Per-thread OS setup a background worker needs before OS API calls.

        Any non-main thread that touches OS APIs wraps its body in this::

            with platform.worker_thread_context():
                <loop>

        Windows opens a COM apartment (``CoInitialize``) so WMI sensor reads
        work off the main thread; an OS needing nothing returns a null context.
        """

    @abstractmethod
    def memory_info(self) -> list[dict[str, str]]:
        """Return DRAM slot descriptors for LC1-style memory displays.

        Each dict carries keys like ``size`` / ``type`` / ``speed`` /
        ``manufacturer`` / ``tcas`` / ``trcd`` / … as discovered.  An OS with
        no probe returns an empty list — deliberately, in its own body, so the
        caller's ``NC`` means "measured nothing" and not "never asked".
        """

    @abstractmethod
    def disk_info(self) -> list[dict[str, str]]:
        """Return attached-disk descriptors for LF11-style disk displays.

        Each dict carries ``name`` / ``model`` / ``size`` / ``type`` /
        optional ``health``.  Same rule as :meth:`memory_info`: an OS with no
        probe returns an empty list from its own body.
        """


# =========================================================================
# Callable type aliases (infrastructure DI)
# =========================================================================

DetectDevicesFn = Callable[[], list["DeviceInfo"]]


# =========================================================================
# Send scheduling — policy/execution split for the per-device send worker
# =========================================================================
#
# A device's USB wire has exactly one owner: a ``SendTask`` per device that
# serializes every write.  The *policy* (what to write, when to keepalive)
# lives in the task; the *execution* (thread / pool / manual tick) lives in a
# ``SendScheduler``.  Injecting the scheduler keeps the task pure of threading
# and lets tests drive it deterministically (``SyncSendScheduler``) with no
# sleeps.  See ``doc/SEND_FOUNDATION.md``.


class SendTask(ABC):
    """One unit of work a :class:`SendScheduler` drives on its own cadence.

    The scheduler loops ``wait(delay) → run_once(now)`` forever; producers
    (any thread) wake the task via the concrete object's own ``submit``.  A
    single scheduler thread per task makes the task the *sole* caller of the
    device write — serialization by construction, no wire lock needed.
    """

    @property
    @abstractmethod
    def key(self) -> str:
        """Stable identifier (the device key) — used by the scheduler registry."""

    @abstractmethod
    def wait(self, timeout: float) -> None:
        """Block until woken by a producer or *timeout* seconds elapse.

        Thread-efficiency only — a correct scheduler may also just call
        :meth:`run_once` on a fixed cadence and skip this.
        """

    @abstractmethod
    def wake(self) -> None:
        """Interrupt a pending :meth:`wait` — used by the scheduler at teardown
        so a long-idle task leaves ``wait`` promptly instead of blocking the
        join."""

    @abstractmethod
    def run_once(self, now: float) -> float:
        """Perform any pending + keepalive work for *now* (monotonic seconds).

        Returns the maximum seconds to wait before the next ``run_once``.
        """


class SendScheduler(ABC):
    """Drives :class:`SendTask` instances.  One impl per execution model.

    Concrete: ``ThreadSendScheduler`` (a daemon thread per task) for
    production, ``SyncSendScheduler`` (manual ``tick``) for deterministic
    tests.  Injected at the composition root so the task never names a thread.
    """

    @abstractmethod
    def add(self, task: SendTask) -> None:
        """Start driving *task*."""

    @abstractmethod
    def remove(self, key: str) -> None:
        """Stop driving the task with *key* and release its resources."""

    @abstractmethod
    def shutdown(self) -> None:
        """Stop driving every task (app teardown)."""


class DataInstallRunner(ABC):
    """Runs per-resolution data installs OFF the caller's thread.

    ``ensure_all`` fetches six archives (~30 MB for a non-square panel)
    straight from GitHub.  Calling it inline from ``ConnectDevice`` put that
    on the GUI's startup path: the splash blocks until the last byte lands,
    so a slow link delayed the main window by minutes and an unreachable one
    by far longer.  The install is best-effort -- an empty theme grid is a
    degraded app, a window that never opens is a broken one.  (#275)

    Concrete: ``ThreadDataInstallRunner`` (a daemon worker) for production,
    ``SyncDataInstallRunner`` (installs inline) for deterministic tests.
    Injected at the composition root so no Command names a thread.
    """

    @abstractmethod
    def submit(self, resolution: tuple[int, int],
               variant: str = "", mask_variant: str = "") -> None:
        """Queue *resolution* for install.  Returns immediately.

        *variant* / *mask_variant* are the per-SKU artwork suffixes from
        ``core.protocol.artwork_variant`` / ``mask_variant``; both default to
        the unsuffixed libraries.

        Idempotent per REQUEST -- resolution and suffixes together, not
        resolution alone.  The discover -> connect sequence sees the same panel
        twice and downloads once, while two coolers that share a panel but want
        different libraries each get theirs.
        """

    @abstractmethod
    def shutdown(self) -> None:
        """Stop the worker and drop anything still queued (app teardown)."""
