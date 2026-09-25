"""Ports — ABCs that adapters implement.

Pure contract definitions.  Adapter implementations live in
`trcc.adapters.*`.  Services and App depend on these ABCs, never on
concrete implementations.
"""
from __future__ import annotations

import builtins
import logging
from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from pathlib import Path
from typing import TYPE_CHECKING, Any, Generic, TypeVar

from ._safe import is_under
from .errors import DeviceDisconnectedError, UnsupportedOperationError
from .logs import per_frame
from .models import (
    DEFAULT_REFRESH_INTERVAL_S,
    MIN_REFRESH_INTERVAL_S,
    VideoExportRequest,
    format_device_key,
)

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
        DisplaySession,
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
    """Abstract USB bulk/interrupt transport.  One per open device handle.

    **Deliberately not a context manager**, though ``open``/``close`` looks like
    one.  The handle's life is owned by the DEVICE lifecycle: ``_open_transport``
    runs on connect and ``disconnect`` closes it, with the handshake, hundreds of
    frames and the keepalive loop in between — different methods, different user
    actions, minutes or hours apart.  That does not fit inside a ``with`` block,
    and a scoped protocol here invites ``with transport:`` inside ``send()``,
    which closes the wire mid-stream.

    Both concrete transports carried ``__enter__``/``__exit__`` with ZERO callers
    anywhere in ``src``, ``tests`` or ``dev`` (removed 2026-09-15).  They were
    also unreachable through this port, so a caller holding the abstraction could
    never have used them.
    """

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
        log.debug("__init__: info=%s transport=%s", info, transport)
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
        # Where this device may persist its own state, injected by the
        # composition root via ``set_state_dir``.  ``None`` means nobody told
        # us, and a device that has not been told MUST NOT GUESS — see there.
        self._state_dir: Path | None = None

    def set_state_dir(self, path: Path) -> None:
        """Inject the directory this device may persist its own state in.

        A resolved ``Path``, never the ``Paths`` port — the same rule
        :meth:`set_permission_hint` states: the composition root resolves, the
        device receives a value and still knows nothing about the OS.

        Exists because the alternative is what the LED adapter did for as long
        as it existed: ``Path.home() / ".trcc" / ...`` as a module constant.
        That is only the config dir on Linux and BSD (Windows uses
        ``%APPDATA%``, macOS ``Application Support``), so on half our platforms
        the file landed where nothing else lives and no report collects it —
        and because the constant needed no injection, **the test suite wrote
        into the real user's home**.  MEASURED: five LED test files put a fake
        ``pm=208 MAGIC_QUBE`` entry in ``~/.trcc/led_probe_cache.json``, and
        that cache is what a second launch trusts INSTEAD of a handshake, so a
        test run could redefine a real device's identity.

        Left ``None`` a device persists nothing and says so.  That is the point:
        the un-injected case is now silent on disk by construction rather than
        by every test remembering to monkeypatch a module constant.
        """
        self._state_dir = path
        log.debug("Device %s: state_dir=%s", self.info.key, path)

    def set_permission_hint(self, hint: str) -> None:
        """Inject the OS-specific EACCES remediation hint (a pre-resolved
        string from ``Platform.permission_denied_hint``).

        The device receives a string, never a ``Platform`` — it still knows
        nothing about the OS; the composition root resolves the hint and hands
        it down for the recovery tracker's permission-denied warning.
        """
        log.debug("set_permission_hint: hint=%s", hint)
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
        log.debug("is_led")
        return False

    #: Which PHYSICAL unit this is, when more than one of the model is
    #: plugged in.  Empty is the normal case — see :meth:`set_unit`.
    _unit: str = ""

    def set_unit(self, unit: str) -> None:
        """Name WHICH of several identical units this object drives (#287).

        Two of the same cooler share a VID/PID and ship no serial, so the
        catalog's ``ProductInfo.key`` cannot tell them apart and both devices
        landed in ``App.devices`` under one key — the second overwrote the
        first.  ``Platform.scan_devices`` resolves the USB port and
        ``disambiguate`` hands it out; the composition root passes it here,
        the same way it passes quirks and the state dir.

        Empty for every single-device user, which keeps :attr:`key` at the
        plain ``vid:pid`` their config and commands already use.
        """
        self._unit = unit
        log.info("Device %s: unit=%r", self.info.key, unit)

    @property
    def key(self) -> str:
        """This DEVICE's identity — ``vid:pid``, or ``vid:pid@port`` (#287).

        Mirrors :attr:`DeviceInfo.key` exactly, because the two are compared:
        the scan produces the ``DeviceInfo`` and the composition root builds
        the ``Device`` from it, and a user's settings are looked up under
        whichever string reaches them.
        """
        key = format_device_key(self.info.vid, self.info.pid, self._unit)
        frame_log.debug("Device.key: %s", key)
        return key

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
        log.debug("needs_keepalive")
        return (self.info.volatile_frames or self._quirks.keepalive_stream)

    @property
    def profile(self) -> DeviceProfile | None:
        """Handshake-derived geometry + encoding profile.

        Set by LCD subclasses when ``connect()`` parses the PM/FBL bytes.
        LED devices and pre-handshake state both return None — callers
        that build frames must fall back to ``info.native_resolution``.
        """
        log.debug("profile")
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
        log.debug("handshake")
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
        log.debug("led_handshake")
        return None

    @property
    def can_boot_animate(self) -> bool:
        """True if this device accepts a flash boot animation (SCSI only).

        Lets a Command gate on capability *before* the connection check
        (boot anim is SCSI-only regardless of connection state), instead
        of ``isinstance(device, ScsiLcd)``.  SCSI LCDs override to True.
        """
        log.debug("can_boot_animate")
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
        log.debug("send_boot_animation: %d frame(s) delays_ds=%s",
                  len(frames), delays_ds)
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


# A quantity method below that carries a BODY instead of ``@abstractmethod`` is
# optional: its default answers ``None``, and a backend that cannot read it
# simply does not override it.  Each one is optional because a real backend
# demonstrated it cannot answer — ``WmiVideoControllerGpu`` reads 1 of GpuSource's
# 8, ``SmcCpu`` 1 of CpuSource's 4 — and before this the contract demanded all of
# them, so 43 method bodies across 28 backends existed only to write
# ``return None`` and say "I cannot".  That is not an implementation; it is the
# contract being wrong, and it cost more than noise: a backend could satisfy the
# ABC by stubbing a sensor it had simply never wired up, and nothing could tell
# that apart from hardware that genuinely lacks it.
#
# The defaults are docstring-only on purpose.  They return ``None`` implicitly,
# which is the whole behaviour, and nothing happens in them to log.
#
# ``key`` / ``name`` / ``is_discrete`` / ``rpm`` / ``DiskSource.temp`` /
# ``DramSource.temp`` and every ``MemorySource`` reading stay ABSTRACT: no
# backend has ever stubbed one, so there is no evidence they are optional, and
# a source that cannot say what it IS should not be constructible.

class QuantitySource:
    """A sensor source whose individual quantity readings are OPTIONAL.

    **Deliberately NOT an ``ABC``** — it is shared behaviour, not a contract.
    It declares nothing a subclass must write, so ``ABC`` would buy none of the
    three things ABCs are here for: it would not enforce anything, it would
    make the class instantiable-looking while claiming otherwise, and
    ``doc/REFERENCE_PORTS.md`` skips it either way (the generator selects on
    ``inspect.isabstract``, which is false without abstract members).  Ruff's
    B024 says the same thing, and it is right; the answer is to be honest about
    what this is, not to silence it.  The ports that mix it in keep their own
    ``ABC``.

    The three ports below declare quantities a backend may simply not have —
    :class:`CpuSource` 4, :class:`GpuSource` 7, :class:`FanSource` 1 — and
    answer ``None`` by default so a backend that cannot read one does not have
    to say so in code.  That default is what makes this ABC possible: "the
    subclass never overrode it" IS "this backend has no such sensor", and
    :meth:`provides` reads it back.

    **It spans THREE ports, not five, and that is measured.**
    ``MemorySource``, ``DiskSource`` and ``DramSource`` declare every reading
    ``@abstractmethod`` — they have no optional quantity at all — so they are
    not here and would gain nothing by it.

    **This is NOT the forbidden ``SensorSource``.**
    ``feedback_one_shared_sensorsource_abc`` prohibits a supertype over all
    FIVE role ports because their intersection is EMPTY.  That reasoning is
    untouched and still stands.  This is a different, non-empty intersection —
    *"has optional quantity readings"* — carrying a real method with a real
    body, so it is not a marker interface.  Like :class:`IdentifiedSource`, it
    is deliberately named for what it contracts rather than for the family.

    **Why it exists at all**: the ``None`` a consumer sees has three causes and
    they need different answers — this backend has no such sensor (``-1``), the
    read found nothing this tick (``0``), or the read raised.  Only the first is
    static, and only the first is safe to act on: the other two change between
    ticks.  Everything that omits, unbinds or explains a missing sensor keys off
    :meth:`provides` for exactly that reason.
    """

    def provides(self, quantity: str) -> bool:
        """True when this source actually reads *quantity*.

        Derived, never listed: the port declares the default, a backend that
        can read the quantity overrides it, so the answer is simply whether the
        class that DEFINES *quantity* is the one that DECLARED it.  There is no
        table to drift out of date, and an abstract quantity answers ``True``
        because it could not have been left unimplemented.

        **A delegating source MUST override this.**  The default asks about the
        class in hand, and a chain overrides every quantity in order to forward
        it — so an un-overridden chain of backends that read nothing would
        answer "reads everything".  See ``CpuSourceChain.provides``.

        One-shot by contract, so it logs on the ordinary logger: the whole
        point is that a reporter's file keeps the line saying which quantities
        this host can never read.  Callers that need it per tick cache it —
        ``SensorEnumerator.unsupported()`` does.
        """
        owners = [k for k in type(self).__mro__ if quantity in vars(k)]
        if not owners:
            log.warning(
                "provides: %s declares no quantity %r — a typo here reads as "
                "'unsupported' and would silently drop a real sensor",
                type(self).__name__, quantity,
            )
            return False
        answer = owners[0] is not owners[-1]
        log.debug("provides: %s.%s declared by %s -> %s",
                  type(self).__name__, quantity, owners[0].__name__, answer)
        return answer


class CpuSource(QuantitySource, ABC):
    """Primary CPU.  usage/freq nearly always present; temp/power may be None."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    def temp(self) -> float | None:
        """CPU package temperature in °C.

        ``None`` is the default and means this backend has no such
        sensor.  A backend that can read it overrides this.
        """

    def usage(self) -> float | None:
        """CPU utilization 0-100.

        ``None`` is the default and means this backend has no such
        sensor.  A backend that can read it overrides this.
        """

    def freq(self) -> float | None:
        """Current CPU frequency in MHz.

        ``None`` is the default and means this backend has no such
        sensor.  A backend that can read it overrides this.
        """

    def power(self) -> float | None:
        """Package power draw in W.

        ``None`` is the default and means this backend has no such
        sensor.  A backend that can read it overrides this.
        """


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


class IdentifiedSource(ABC):
    """A sensor the OS can enumerate SEVERAL of, each one identifiable.

    ``key`` + ``name`` were declared identically on :class:`GpuSource`,
    :class:`FanSource`, :class:`DiskSource` and :class:`DramSource` — one
    contract written four times.  They are also not an arbitrary pair: they mark
    exactly the sources that come in PLURALS.  :class:`CpuSource` and
    :class:`MemorySource` are singular and declare neither.

    That line matters because plural is precisely what a user can CHOOSE
    between.  So this ABC is the contract "an enumerable, identifiable sensor",
    which is the same set a preference can be pinned to — and it is what gives
    :func:`_resolve_preferred` a type bound instead of a per-family copy.

    Each OS fills it the way it already fills the role ports; no adapter
    changes, because every implementation already provides both members.

    **This overrides secondary finding #1 of
    ``feedback_one_shared_sensorsource_abc`` — NOT its prohibition.**  That memo
    forbids a ``SensorSource`` ABC over all FIVE role ports, because their
    intersection is EMPTY: :class:`MemorySource` declares neither member, so the
    supertype would be a marker interface.  That reasoning is untouched and
    still stands.  This ABC spans the FOUR ports whose intersection is
    ``{key, name}`` — which is the memo's own ``IdentifiedSource`` proposal, and
    it carries the memo's own name so the two can never be mistaken for each
    other.

    Finding #1 weighed it as "saves ~8 trivial properties, adds a layer" and
    judged it not worth it.  Two things it did not weigh: CLAUDE.md's own DRY
    threshold — *"3+ duplicates = centralize"* — which is met at FOUR; and
    :func:`_resolve_preferred`, which did not exist then, so nothing needed a
    type bound.  Without a base the alternative is
    ``TypeVar("_P", GpuSource, DiskSource)``, which must be EDITED to admit each
    new family where a bound need not be.
    """

    @property
    @abstractmethod
    def key(self) -> str:
        """Stable, UNIQUE ID for this source.

        Stable matters as much as unique: a persisted user choice is looked up
        by this string on the next boot.  ``HwmonDisk`` learned that twice over
        — it collided across two NVMe drives until 2026-08-31, and the obvious
        fix (the hwmon directory name) is unique but renumbers between boots.
        """

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable label."""


class GpuSource(IdentifiedSource, QuantitySource):
    """One GPU — NVIDIA/AMD/Intel/Apple, discrete or integrated."""

    @property
    @abstractmethod
    def is_discrete(self) -> bool:
        """True for dedicated cards, False for iGPUs sharing CPU memory."""

    def temp(self) -> float | None:
        """Core temperature in °C.

        ``None`` is the default and means this backend has no such
        sensor.  A backend that can read it overrides this.
        """

    def usage(self) -> float | None:
        """Utilization 0-100.

        ``None`` is the default and means this backend has no such
        sensor.  A backend that can read it overrides this.
        """

    def clock(self) -> float | None:
        """Core clock in MHz.

        ``None`` is the default and means this backend has no such
        sensor.  A backend that can read it overrides this.
        """

    def power(self) -> float | None:
        """Board power draw in W.

        ``None`` is the default and means this backend has no such
        sensor.  A backend that can read it overrides this.
        """

    def fan(self) -> float | None:
        """Fan speed 0-100.

        ``None`` is the default and means this backend has no such
        sensor.  A backend that can read it overrides this.
        """

    def vram_used(self) -> float | None:
        """VRAM used in MB.

        ``None`` is the default and means this backend has no such
        sensor.  A backend that can read it overrides this.
        """

    def vram_total(self) -> float | None:
        """VRAM total in MB.

        ``None`` is the default and means this backend has no such
        sensor.  A backend that can read it overrides this.
        """


class FanSource(IdentifiedSource, QuantitySource):
    """One fan — may be role-mapped (cpu/gpu/sys1) or anonymous."""

    @abstractmethod
    def rpm(self) -> int | None:
        """Current RPM, or None."""

    def percent(self) -> float | None:
        """Duty cycle 0-100.

        ``None`` is the default and means this backend has no such
        sensor.  A backend that can read it overrides this.
        """


class DiskSource(IdentifiedSource):
    """One storage device's thermal sensor (NVMe / SATA SSD / HDD).

    Every OS reads drive temperature differently — Linux from hwmon
    ``nvme`` / ``drivetemp`` nodes, Windows from LibreHardwareMonitor /
    HWiNFO or SMART attribute 0xC2, macOS from SMC / ``smartctl``, BSD
    from ``sysctl dev.nvme.*.temp`` — so each ``Platform`` discovers its
    own ``DiskSource`` list and the OS-neutral aggregator just folds the
    hottest into ``disk:temp``.  Mirrors :class:`FanSource`.
    """

    @abstractmethod
    def temp(self) -> float | None:
        """Current temperature in °C, or None."""


class BoardTempSource(IdentifiedSource):
    """One motherboard / super-I/O temperature input.

    The sensors every other port deliberately does NOT claim.  CPU, GPU, disk
    and DRAM temperatures are ROLE-typed -- we know what they mean -- so each
    has its own port and its own discovery.  A board sensor has no role: it is
    whatever the builder wired to that header, which is why the user has to be
    the one who picks it.

    That gap was invisible because the chip was already half-read.  A Nuvoton
    ``nct6xxx`` is enumerated for its FANS (``hwmon.py`` says so in its own
    module docstring) while its dozen ``tempN_input`` channels were passed
    over -- so on a typical desktop we walked past ``SYSTIN``, ``CPUTIN`` and
    five ``AUXTIN`` inputs on a chip we already had open.  ``T_SENSOR1``, the
    external probe header ASUS boards expose, is one of those AUXTINs (#259),
    and a Fujitsu ``sch5636`` went entirely unseen (#282).

    Plural and identifiable, hence :class:`IdentifiedSource`: this is exactly
    the set a preference can be pinned to, which is the whole reason a user
    asks for it.
    """

    @abstractmethod
    def temp(self) -> float | None:
        """Current temperature in Celsius, or ``None``."""


class DramSource(IdentifiedSource):
    """One memory module's SPD-hub thermal sensor.

    DDR5 DIMMs carry an integrated SPD-hub temperature sensor (Linux
    ``spd5118`` hwmon); DDR4 modules expose an optional JEDEC JC-42.4
    thermal sensor (``jc42``).  Each ``Platform`` discovers its own
    ``DramSource`` list and the OS-neutral aggregator folds the hottest
    into ``memory:temp``.  Mirrors :class:`DiskSource`.
    """

    @abstractmethod
    def temp(self) -> float | None:
        """Current temperature in °C, or None."""


# =========================================================================
# SensorEnumerator — the aggregate: composes one CPU + one memory + N GPUs + N fans
# =========================================================================


def _or_zero(value: float | None) -> float:
    """None-coalesce a possibly-absent sensor reading to 0.0."""
    log.debug("_or_zero: value=%s", value)
    return 0.0 if value is None else float(value)


_Preferred = TypeVar("_Preferred", bound=IdentifiedSource)


def _resolve_preferred(
    sources: Sequence[_Preferred],
    preferred_key: str | None,
    warned_key: str | None,
    kind: str,
) -> tuple[_Preferred | None, str | None]:
    """Resolve a pinned choice among interchangeable sensors.  PURE.

    Returns ``(match, new_warn_state)``.  ``match is None`` means "no pin, or
    the pinned source is gone — use the family default", which the CALLER
    supplies, because the default genuinely differs: a GPU auto-picks
    discrete-first (a property of the sources), a disk falls back to the hottest
    (a property of the readings).

    Pure on purpose.  Returning the new warn-state instead of mutating a named
    attribute keeps the state where it belongs — on the instance, assigned at
    the call site — and avoids passing an attribute NAME as a string, which is
    the design this project rejects.

    The warn-once dedupe is the part actually worth sharing: ``primary_gpu`` is
    called every tick, so a stale preference would otherwise log an identical
    line per poll.  Warn once per DISTINCT missing key; a key that comes back
    re-arms the warning.
    """
    if preferred_key is None:
        return None, warned_key
    for source in sources:
        if source.key == preferred_key:
            return source, None          # present again — re-arm the warning
    if warned_key != preferred_key:
        log.warning("preferred %s %s not among %s — using the default",
                    kind, preferred_key, [s.key for s in sources])
        return None, preferred_key
    return None, warned_key


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

    # The user's disk choice — same contract as the GPU pair above, seeded at
    # boot by the composition root from ``settings.active_disk``.
    _preferred_disk_key: str | None = None
    _warned_missing_disk_key: str | None = None

    # Poll cadence, in seconds.  Lives here rather than on the concrete
    # enumerator for the same reason the preference keys do: it is pure state
    # with no OS-specific variation, so every implementation would write the
    # identical setter.  ``AppSettings.refresh_interval_s`` is the source of
    # truth; ``MetricsLoop`` pushes it down each iteration.
    _interval_s: float = DEFAULT_REFRESH_INTERVAL_S

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

    @abstractmethod
    def disks(self) -> list[DiskSource]:
        """All detected drive thermal sensors.  Empty if none.

        Added 2026-08-31, and its absence was the whole reason a user's disk
        choice could never be honoured: disks existed only as a private field
        on the concrete aggregator, so no Query could enumerate them and the
        picker had to be sourced from a DIFFERENT list (psutil partitions, then
        physical drives) than the one the metric comes from.
        """

    def set_preferred_gpu(self, gpu_key: str | None) -> None:
        """Pin which GPU ``primary_gpu()`` returns (``''``/``None`` = auto)."""
        normalized = gpu_key or None
        log.info("set_preferred_gpu: %s -> %s",
                 self._preferred_gpu_key, normalized)
        self._preferred_gpu_key = normalized
        # A fresh choice re-arms the missing-GPU warning.
        self._warned_missing_gpu_key = None

    def set_interval(self, seconds: float) -> None:
        """Change the poll cadence, in flight.

        The ONE writer of :attr:`_interval_s` — ``start_polling`` delegates
        here so a second path cannot skip the floor clamp.

        Waking the sleeper is the load-bearing half: a poll loop that is
        mid-``wait`` on the OLD interval would otherwise honour the new one
        only after sleeping out the old, so lowering 100 s to 1 s would leave
        the sweep 100 s behind the broadcast it feeds.
        """
        clamped = max(MIN_REFRESH_INTERVAL_S, seconds)
        if clamped == self._interval_s:
            log.debug("set_interval: already %.2fs — no change", clamped)
            return
        log.info("set_interval: %.2fs -> %.2fs", self._interval_s, clamped)
        self._interval_s = clamped
        self._interval_changed()

    def _interval_changed(self) -> None:
        """Hook: cut a running poll loop's sleep short.  No-op by default.

        An enumerator that does not poll on a thread has nothing to wake, so
        the base does nothing and only a polling implementation overrides.
        """
        log.debug("_interval_changed: nothing to wake")

    def primary_gpu(self) -> GpuSource | None:
        """The user-preferred GPU if set and still present, else the first
        discrete GPU, else first integrated, else None.

        The preference half is shared with :meth:`preferred_disk`; the DEFAULT
        stays here because it is a property of the sources (discrete-first) and
        needs no readings.
        """
        frame_log.debug("primary_gpu")
        gpus = self.gpus()
        match, self._warned_missing_gpu_key = _resolve_preferred(
            gpus, self._preferred_gpu_key, self._warned_missing_gpu_key, "gpu",
        )
        if match is not None:
            return match
        for gpu in gpus:
            if gpu.is_discrete:
                return gpu
        return gpus[0] if gpus else None

    def set_preferred_disk(self, disk_key: str | None) -> None:
        """Pin which drive supplies ``disk_temp`` (``''``/``None`` = hottest)."""
        normalized = disk_key or None
        log.info("set_preferred_disk: %s -> %s",
                 self._preferred_disk_key, normalized)
        self._preferred_disk_key = normalized
        self._warned_missing_disk_key = None

    def preferred_disk(self) -> DiskSource | None:
        """The pinned drive if still present, else ``None``.

        **No family default here, unlike :meth:`primary_gpu` — and the asymmetry
        is the data, not an oversight.**  A disk's default is "the hottest",
        which is a property of the READINGS; the aggregator has just taken them
        (and must keep taking all of them, because ``_read`` does per-source
        failure bookkeeping every tick).  Answering "hottest" here would mean
        reading every drive a second time.  So this answers only "did the user
        pin one, and is it still here", and the aggregator applies its own
        default.
        """
        match, self._warned_missing_disk_key = _resolve_preferred(
            self.disks(), self._preferred_disk_key,
            self._warned_missing_disk_key, "disk",
        )
        # Per-TICK: the aggregator calls this on every snapshot, so it belongs
        # to the frame family (INFO by default, DEBUG under -vvv) rather than
        # the ordinary logger — an ordinary .debug() here would write a record
        # per frame, the defect the frame gate exists to catch.
        frame_log.debug("preferred_disk: %s -> %s",
                        self._preferred_disk_key or "(hottest)",
                        match.key if match else None)
        return match

    def snapshot(self) -> HardwareMetrics:
        """Typed metrics snapshot — one fresh object per tick, raw °C.

        Concrete template method: a TYPED view of the one sample
        ``read_all()`` returns, so the DTO a cooler displays and the flat
        readings an overlay renders can never disagree — they are the same
        numbers.  Every OS inherits this unchanged.

        Returns RAW canonical units (°C); callers apply user prefs via
        :func:`trcc.services.metrics_personalize.personalize_metrics`.
        ``cpus``/``gpus`` carry every detected unit (single-element on
        consumer hardware); the scalar fields collapse them — ``cpu_temp``
        is the hottest socket, ``cpu_percent`` the average — so the one
        number a cooler shows stays correct when sources widen to plural.
        """
        from .models import CpuMetrics, GpuMetrics, HardwareMetrics

        # ONE sample, two views.  Every scalar below comes from ``readings``,
        # which ``read_all`` has just made current -- this method used to
        # re-read each source from hardware instead, so a metrics tick read the
        # sensors TWICE.  Measured on a 320x320 SCSI panel at a 2 s interval:
        # **25.01 ms per tick, 43% of the whole tick** (the sweep is 47 ms; the
        # bus fan-out, for scale, is 0.87 ms), and 15 redundant reads -- the GPU
        # alone was read nine times, once for the plural list and again for the
        # primary.
        #
        # The second read was also the WRONG one, and it is the one the GUI
        # showed.  ``cpu:usage`` and ``cpu:power`` are DELTAS -- a percentage
        # since the previous call, and RAPL energy over elapsed time -- so
        # re-reading microseconds after the poll measures a window of nothing.
        # Over 8 steady ticks the LCD overlay (fed from these readings) showed
        # 11.7% +/- 0.53 while the panel (fed from this DTO) showed
        # 12.5% +/- 3.34, and CPU power spiked to 27.8 W against a true 13.0 W.
        # ``RenderLed`` fixed this one layer up -- its docstring records the
        # same "resampled instantaneous readings ... flicker" -- and reads
        # ``app.last_raw_snapshot`` now.  The resampling simply moved in here.
        #
        # ``.get(key, 0.0)`` preserves the old degrade-to-zero contract:
        # ``_store`` omits a source that returned None, exactly where the
        # removed ``_safe`` returned 0.0.
        readings = self.read_all()
        value = readings.get          # bound lookup, not a new frame per field
        cpu = self.cpu()
        cpus = [CpuMetrics(
            name=cpu.name,
            temp=value("cpu:temp", 0.0), usage=value("cpu:usage", 0.0),
            freq=value("cpu:freq", 0.0), power=value("cpu:power", 0.0),
        )]
        gpus = [GpuMetrics(
            name=g.name,
            temp=value(f"gpu:{i}:temp", 0.0), usage=value(f"gpu:{i}:usage", 0.0),
            clock=value(f"gpu:{i}:clock", 0.0), power=value(f"gpu:{i}:power", 0.0),
        ) for i, g in enumerate(self.gpus())]
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
        fan_gpu = value("gpu:primary:fan", 0.0)
        # Same pool, same order, same "an empty header is 0 RPM" skip -- but
        # read from the sample.  ``_store`` keeps a 0.0 (it omits only None),
        # so the truthiness test still has to be applied here or a stopped
        # header would take a fan slot.
        pool = iter(
            rpm for f in self.fans()
            if "gpu" not in f.key.lower()
            and (rpm := readings.get(f"fan:{f.key}:rpm"))
        )
        fan_cpu = next(pool, 0)
        fan_ssd = next(pool, 0)
        fan_sys2 = next(pool, 0)
        metrics = HardwareMetrics(
            cpu_temp=max((c.temp for c in cpus), default=0.0),
            cpu_percent=(sum(c.usage for c in cpus) / len(cpus)) if cpus else 0.0,
            cpu_freq=max((c.freq for c in cpus), default=0.0),
            cpu_power=sum(c.power for c in cpus),
            gpu_temp=value("gpu:primary:temp", 0.0),
            gpu_usage=value("gpu:primary:usage", 0.0),
            gpu_clock=value("gpu:primary:clock", 0.0),
            gpu_power=value("gpu:primary:power", 0.0),
            mem_percent=value("memory:percent", 0.0),
            mem_available=value("memory:available", 0.0),
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
    def unsupported(self) -> frozenset[str]:
        """Normalized keys NO backend on this host can read.  STATIC.

        The third state, made answerable: a key here is absent from
        :meth:`read_all` because the backend has no such sensor
        (:meth:`QuantitySource.provides` is ``False``), not because this
        tick's read came back empty.

        **Static is the whole contract, and the reason this is a separate
        method rather than a diff against ``read_all()``.**  That diff is
        derived from ONE tick and is transient — rate-derived keys
        (``disk:read``, ``net:up``, ``cpu:power`` where power comes from an
        energy counter) need two samples and are legitimately missing from the
        first poll.  Anything that OMITS, UNBINDS or explains away a sensor
        must key off this set instead, or a cold start would persist a
        decision made before the sensor had a chance to report.

        Abstract rather than derived here on purpose: the normalized key
        vocabulary is spelled by the enumerator that builds it, and core
        cannot import an adapter to reach it.
        """

    @abstractmethod
    def start_polling(
        self, interval_s: float = DEFAULT_REFRESH_INTERVAL_S,
        on_sweep: Callable[[], None] | None = None,
    ) -> None:
        """Begin refreshing the cache in the background every *interval_s*.

        ``on_sweep`` is called once after each completed sweep, ON THE POLL
        THREAD, so it must be trivial and must not raise — the only intended
        argument is an ``Event.set``.  It exists so a consumer can publish
        when the data actually changed instead of on a clock of its own:
        two independent timers on one period drift against each other, and
        measured on 2026-09-19 that cost ``MetricsLoop`` a full interval of
        staleness on the fakes and a mean of half an interval on real
        hardware, plus an intermittent first broadcast of an UNSWEPT cache.
        """

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

    def is_user_content(self, path: Path) -> bool:
        """True when *path* is an asset the USER authored, not a shipped one.

        **The one place this question is answered.**  It decides how a
        background is fitted: user content arrives at its NATIVE resolution
        and honours ``DeviceSettings.fit_mode``; program/cloud content is
        pre-authored at the device canvas and takes the C# native-or-black
        width test (``Renderer.bg_fit``).  ``PlayVideo`` asks the same
        question to pick a decode size, and the two answers have to agree or
        a natively-decoded upload meets the canvas-sized rule.

        They did not agree.  ``DisplayService`` used to ask it of the active
        THEME's directory, which says nothing about where the background
        came from -- a ``background_path`` override and a reference theme's
        asset both resolve outside the theme dir -- so the same file rendered
        or went black depending on which theme happened to be selected.

        Rooted at :meth:`user_content_dir`, not :meth:`user_data_dir`, so
        staged one-off content (``single-image/``, ``uploads/``) counts too.
        Concrete on the ABC for the same reason :meth:`user_data_dir` is:
        every OS roots user content the same way, only the root differs.
        """
        under = is_under(path, self.user_content_dir())
        frame_log.debug("is_user_content: %s → %s", path, under)
        return under

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

    @abstractmethod
    def tile_path(self, theme_dir: Path) -> Path | None:
        """Best tile image for a theme DIRECTORY, or None.

        ``Theme.png`` → ``00.png`` → any ``*.png``.  Keyed on a directory
        rather than a :class:`Theme` because listings ask it of paths they have
        not loaded, and it falls back where :meth:`preview_path` does not — the
        store owns which files can stand in for a tile, exactly as it owns which
        files mark a theme dir.

        NOTE the overlap with :meth:`preview_path`, which answers the same
        question more narrowly and is NOT implemented in terms of this.  They
        genuinely disagree for a theme with no ``Theme.png``: one returns None,
        the other falls back.  Unifying them is a behaviour change to the GUI
        grid, not a refactor, so it is recorded here rather than done quietly.
        """

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
    def write_manifest(self, theme_dir: Path, manifest: dict) -> Path:
        """Persist *manifest* as *theme_dir*'s reference manifest; return its path.

        The caller owns the manifest as DOMAIN data — which background, which
        mask, which elements.  How that becomes bytes on disk (the filename,
        JSON, the encoding, the trailing newline) is the adapter's business and
        the reason this takes a dict rather than a string.
        """

    @abstractmethod
    def write_preview(self, theme_dir: Path, png: bytes) -> Path:
        """Give *theme_dir* the grid tile the chooser shows; return its path.

        ``Theme.png`` is the chooser's tile and is NEVER rendered to a device,
        so a full composite is safe here in a way it would not be for a frame.
        """

    @abstractmethod
    def copy_preview(self, src_theme_dir: Path, dst_theme_dir: Path) -> bool:
        """Copy *src*'s grid tile to *dst*; False when *src* has none.

        A pair rather than read-then-write because "does the source have a tile"
        is the store's question, and answering it in the caller means a probe in
        the caller.  Returning a bool rather than raising keeps it usable as the
        best-effort fallback ``SaveTheme`` needs — a missing tile must never
        fail a save.
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

    def fill_rect(self, surface: Any, x: int, y: int, width: int, height: int,
                  color: tuple[int, int, int, int]) -> Any:
        """Draw an RGBA rectangle onto *surface*; returns the result.

        **Concrete, and built only from primitives every Renderer already
        implements** — ``create_surface`` makes the patch, ``composite`` lays
        it over with alpha.  Adding this as an ABSTRACT method instead broke
        every implementation at once: nine test doubles subclass this port and
        none could be instantiated, 553 tests failed, and the cost of a new
        primitive landed on files that have nothing to do with drawing.

        It returns the surface because the default cannot mutate in place —
        ``composite`` yields a new one.  A backend with a real painter should
        OVERRIDE this, mutate, and return the same object: the default copies
        the whole frame per call, which is fine for a one-shot and wrong for a
        16-bar meter at 7 fps.  ``QtRenderer`` does exactly that.
        """
        frame_log.debug("fill_rect: (%d,%d) %dx%d rgba=%s (default: compose)",
                        x, y, width, height, color)
        patch = self.create_surface(width, height, color=color)
        return self.composite(surface, patch, position=(x, y))

    def draw_spectrum(self, surface: Any,
                      levels: Sequence[float]) -> Any:
        """Paint an audio spectrum analyser across the bottom of *surface*.

        Concrete on the port, deliberately: the layout and the colour ramp
        are the SAME picture on every backend, so only the rectangle fill is
        abstract.  A new Renderer inherits the visualiser by implementing
        :meth:`fill_rect` and nothing else.

        It lived in ``ui/gui``'s screencast tick as a ``QPainter`` block, so
        the bars existed for exactly one of the four faces — the CLI, the API
        and qtgui asked for ``audio=True``, the flag was persisted in
        ``screencast_region``, and nothing ever drew them.  Moving it here is
        what makes the flag mean the same thing everywhere.

        *levels* are per-band magnitudes in [0, 1] from
        ``AudioCapture.get_spectrum()``; an empty sequence draws nothing,
        which is what "no ``sounddevice`` installed" looks like.

        Geometry is the ported original: bars fill the bottom quarter,
        centred, 2 px apart, green through yellow to red with the level, at
        alpha 200 so the picture underneath still reads.
        """
        if not len(levels):
            frame_log.debug("draw_spectrum: no bands — nothing to draw")
            return surface
        width, height = self.surface_size(surface)
        bar_area = int(height * 0.25)
        gap = 2
        count = len(levels)
        bar_w = max(1, (width - gap * (count + 1)) // count)
        x0 = (width - (bar_w + gap) * count) // 2
        frame_log.debug("draw_spectrum: %d bands, bar=%dpx, area=%dpx",
                        count, bar_w, bar_area)
        for i, level in enumerate(levels):
            clamped = 0.0 if level < 0.0 else 1.0 if level > 1.0 else float(level)
            bar_h = max(1, int(clamped * bar_area))
            # Green (quiet) -> yellow (half) -> red (loud).
            # Alpha 200 (78%) so the picture underneath still reads through.
            rgba: tuple[int, int, int, int] = (
                (int(clamped * 2 * 255), 255, 0, 200) if clamped < 0.5
                else (255, int((1 - clamped) * 2 * 255), 0, 200)
            )
            surface = self.fill_rect(
                surface, x0 + i * (bar_w + gap), height - bar_h,
                bar_w, bar_h, rgba)
        return surface

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
        log.debug("encode_png: surface=%s", surface)
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
        log.debug("get_pixels_rgb: surface=%s cols=%s", surface, cols)
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
        frame_log.debug("build_frame: profile=%s content=%s", profile, content)
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
        log.debug("list_fonts")
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


class CaptureNotReady(OSError):
    """The capture source is starting up and will answer later.

    Raised by a session-backed source -- the xdg-portal stream -- while the
    user is being asked for consent, or before its first frame lands.  Not a
    failure: every caller drops the frame and asks again next tick, and the
    capture Command reports it at DEBUG where a real failure is a WARNING.
    """


class ScreenCapture(ABC):
    """Port for "grab a rectangle off the desktop right now".

    Adapters: ``QtNativeCapture`` (Qt's own grab), ``ToolCapture``
    (external screenshot programs) and ``PipeWireScreenCapture`` (the
    xdg-portal stream), composed per display session by
    ``build_screen_capture``.  Used by the screencast pipeline to feed live
    desktop pixels into the device.

    Returns a :class:`RawFrame` with RGB24 pixel data sized exactly to
    the requested rectangle — callers handle scale/fit/encode.
    """

    @abstractmethod
    def grab_region(self, x: int, y: int, width: int, height: int) -> RawFrame:
        """Capture *width* × *height* pixels starting at (*x*, *y*).

        Raise :class:`OSError` (or subclass) on capture failure — the
        caller decides whether to retry, stop the screencast, or surface
        the error to the user.  :class:`CaptureNotReady` is the one subclass
        that means "not yet" rather than "no": drop the frame, ask again.
        """
        ...

    def stop(self) -> None:
        """Release a held session, if this source holds one.

        Concrete and a no-op: Qt's grab and the external tools hold nothing
        between calls.  The portal backend overrides it to close its consented
        stream when the screencast stops, so nothing keeps streaming a screen
        nobody is showing.
        """
        log.debug("%s.stop: nothing held", type(self).__name__)


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
    """Start-with-the-computer, per OS.

    ``target`` names WHICH ui starts — see ``core.models.AUTOSTART_TARGETS``.
    All four ship, so pinning autostart to one of them would make the others
    unreachable at login.
    """

    @abstractmethod
    def is_enabled(self) -> bool: ...

    @abstractmethod
    def entry_location(self) -> str:
        """Where this OS records the entry — a path, a registry value, a label.

        For diagnostics: it is what a reporter checks and what ``trcc report``
        carries.  Declared rather than duck-typed — ``_autostart_path`` reached
        for a ``.path`` attribute only ONE implementation had and silently
        returned "" for the other two, which are exactly the platforms we
        cannot reproduce on and therefore depend on the reporter for.
        """

    @abstractmethod
    def installed_target(self) -> str | None:
        """The target the installed entry launches, or None when absent.

        The entry is the record — there is no second copy to drift from it.
        ``refresh`` needs it (it re-renders by re-enabling, and would otherwise
        reset the user's choice) and so does a status report.
        """

    @abstractmethod
    def enable(self, target: str | None = None) -> None:
        """Install the entry.  ``None`` keeps whatever this manager defaults to."""

    @abstractmethod
    def disable(self) -> None: ...

    @abstractmethod
    def refresh(self) -> None:
        """Re-render an EXISTING entry; never install one.

        The repair for a moved install (#201): an entry keeps whatever launch
        command it was written with forever, so a change to that command — a
        relocated install, a new flag like ``--resume``, a different target —
        reaches new installs and never reaches existing ones.

        Two branches, and every implementation has both:

          1. no entry installed → do nothing.  A refresh that could enable
             would silently opt the user into autostart on every launch, which
             is the whole line between this and :meth:`enable`.
          2. otherwise → re-render with the CURRENT command and the
             **installed** target, so a repair never changes which ui the user
             chose.

        Written here because it was the one method on this port with no
        contract at all, and a test double duly implemented it as ``pass`` —
        against which ``RefreshAutostart`` could stop calling ``refresh()``
        entirely with the whole suite still green.
        """


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
                       serial: str | None = None,
                       unit: str = "") -> Transport:
        """Return an unopened transport for *wire*.

        *unit* names WHICH physical device to open when several of the same
        model are plugged in — the USB port, as ``Platform.scan_devices``
        resolved it (#287).  Empty means "the only one of this model", which
        is what every caller meant before the keyword existed.

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
    def display_session(self) -> DisplaySession:
        """Which display server draws the desktop this PROCESS can see, and
        which desktop family runs it.

        An OS fact, so it is asked here and answered by the OS adapter: on
        Linux and the BSDs from the session variables, on Windows and macOS
        always native.  The screen belongs to the session of the process
        asking, which is why this is a method on the Platform an object
        holds and not a value on the bus: under ``TRCC_DAEMON=1`` the
        window's Platform answers for the window's session and the daemon's
        for the daemon's, and those can differ.

        This is the input :func:`~trcc.adapters.screencast.build_screen_capture`
        chooses its links from.  Until 2026-09-18 nothing in the tree could
        answer it -- one env probe sat in a UI module and nothing read it --
        so the capture chain ran every desktop's tool on every desktop:
        X11 grabbers under Wayland, which see only Xwayland and one of which
        rings the X bell on each call; a wlroots tool under KWin, which
        fails every tick; a Plasma tool under GNOME.
        """

    @abstractmethod
    def screen_capture(self) -> ScreenCapture:
        """Grab a desktop rectangle — the source the screencast feed reads.

        Declared on the port because WHICH path captures a screen is an
        OS fact: the portal stream and one desktop's own tool on Wayland,
        Qt's grab then the X11 grabbers on X11, Qt's grab alone on Windows
        and macOS.  ``BaseOS`` composes that chain from
        :meth:`display_session`, so an OS only overrides this when it has
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
    def setup(self, dry_run: bool = False) -> int:
        """Run OS-specific setup.  Returns a shell-style exit code.

        ``dry_run=True`` prints what would be done and changes nothing.

        The parameter was called ``interactive`` (inverted) until 2026-09-10,
        and the name was the bug: ``interactive=False`` reads as "don't ask
        me" and meant "don't act".  So ``trcc system setup --yes`` mapped a
        confirmation flag onto an apply flag and previewed instead of writing
        the udev rules (#285), and the API's setup route passed
        ``interactive=False`` and could never apply anything at all.  Two
        axes -- apply-or-preview, and confirm-or-not -- collapsed into one
        boolean.  Only the first belongs to a Platform; confirming is the
        UI's business.
        """

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

    @abstractmethod
    def disk_partitions(self) -> list[tuple[str, str]]:
        """Mounted partitions as ``(device, mountpoint)`` pairs.

        A DIFFERENT question from :meth:`disk_info`, which reports PHYSICAL
        drives: one drive supplies many partitions, so the two lists have
        different lengths and no shared key.  ``ListDisks`` answers "what is
        mounted where"; ``disk_info`` answers "what drives are attached, with
        model and health".  Neither is the THERMAL list ``disk_temp`` comes
        from — that is ``SensorEnumerator.disks()``, and confusing the three
        is what kept a disk picker from ever working.

        Cross-platform via psutil, so :class:`BaseOS` carries the shared body
        and no OS overrides it today — but it is declared here, with no body,
        because a Query must reach the filesystem through this port rather
        than importing a probe into ``core``.
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


class VideoExportRunner(ABC):
    """Encodes ``Theme.zt`` clips OFF the caller's thread.

    An export is ffmpeg over every frame of a clip up to five minutes
    long, and the exporter allows it 600 s.  The IPC dispatch timeout is
    **30 s**, so a Command that encoded inline could not survive daemon
    mode at all -- the socket would give up twenty times before ffmpeg
    did.  Submitting instead lets ``ExportVideoClip`` return a token
    immediately, and the work reports itself on the EventBus as
    ``VideoExportProgress`` / ``VideoExportFinished``.

    That is also what makes video export a capability of the app rather
    than of a window: both Qt skins used to own a private QThread each --
    two copies of the same encode, invisible to the CLI and the API and
    to any second client of one daemon.

    Serialized on purpose.  Two concurrent ffmpeg runs over the same
    machine finish no sooner together than in turn and make the UI's
    progress meaningless, so a second submission queues.

    Concrete: ``ThreadVideoExportRunner`` (a daemon worker) for
    production, ``SyncVideoExportRunner`` (encodes inline) for
    deterministic tests.  Injected at the composition root so no Command
    names a thread.  Mirrors :class:`DataInstallRunner`.
    """

    @abstractmethod
    def submit(self, token: str, request: VideoExportRequest) -> None:
        """Queue *request* for encoding under *token*.  Returns immediately.

        *token* identifies this one export for the lifetime of its
        events; the caller mints it and matches on it, because several
        clients of one daemon see every event and only the initiator
        should act.

        NOT deduplicated: exporting the same clip twice is a thing a user
        may legitimately ask for, unlike re-downloading an archive that
        is already on disk.
        """

    @abstractmethod
    def shutdown(self) -> None:
        """Stop the worker and drop anything still queued (app teardown)."""
