"""Shared device base — one abstract skeleton; factory children supply the wire.

There is only *the device* (the :class:`~trcc.core.ports.Device` port).  Every
wire adapter has the **same method names** (the contract); the lifecycle that is
identical across wires lives here **once**, and each factory child overrides only
the bodies whose internals genuinely differ — the handshake exchange and the
bytes it writes.

Adding a new wire is therefore one subclass that names its wire in its own
class line — ``class ScsiLcd(BaseDevice[ScsiTransport], wire=Wire.SCSI)`` — and
implements the abstract hooks, with nothing copied.  That is the future-proofing:
new panel family = new subclass, no touched callers.

The split mirrors ``adapters/system/_base.py`` on the OS edge: a concrete
Template Method on the base calls an ``@abstractmethod`` hook on the child —
the idiom already used by ``Device._send_with_recovery``.

:data:`DEVICES` lives here rather than in the package ``__init__`` so the base
class can register its own children without importing its own package (a cycle).
"""
from __future__ import annotations

import json
import logging
import time
from abc import abstractmethod
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any, ClassVar

from ...core.errors import DeviceNotFoundError, HandshakeError, TransportError
from ...core.factory import Registry, Reject
from ...core.logs import Blob, per_frame, trace
from ...core.models import HandshakeResult, ProductInfo, Wire
from ...core.ports import BulkTransport, Device, T
from ...core.protocol import DeviceProfile

log = logging.getLogger(__name__)
#: ``profile`` is read once per frame by the render path.
frame_log = per_frame(__name__)

#: How much of a handshake reply :meth:`BaseDevice._trace_reply` records.
#: 64 clears every parser's deepest offset — LY reads [36], bulk [24] and [36],
#: HID Type-2's serial is [20:36], LED [12], SCSI [0] — so a pasted capture can
#: drive any wire.  The full SCSI poll is 0xE100 bytes and is nearly all zeros.
_TRACE_REPLY_BYTES = 64

# The wire table.  A miss RAISES — unlike the OS table, an unregistered wire is
# a defect (the product registry named a wire nothing implements), not something
# to degrade through.  See ``core.factory.Reject``.
DEVICES: Registry[Wire, type[Device]] = Registry(
    "wire", on_missing=Reject(DeviceNotFoundError),
)


# Handshake pacing for the report-style wires (HID LCD + LED), from the C#:
# settle before the init write, let the firmware answer, retry a bad exchange.
HANDSHAKE_TIMEOUT_MS = 5000
_HANDSHAKE_MAX_RETRIES = 3


def read_state(path: Path) -> dict[str, Any]:
    """A device's JSON state file, or ``{}`` when absent or unreadable.

    State a device remembers across launches (the LED probe cache, the HID
    streaming-probe answer) is a hint, never an authority: a missing or
    corrupt file means "nothing remembered", not a failure.
    """
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        log.debug("read_state: %s absent", path)
        return {}
    except (OSError, ValueError) as e:
        log.debug("read_state: %s unreadable (%s) — starting fresh", path, e)
        return {}
    return state if isinstance(state, dict) else {}


def write_state(path: Path, state: dict[str, Any]) -> None:
    """Persist *state* best-effort: a failed write is logged, never raised."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
        log.debug("write_state: %s (%d entr(y/ies))", path, len(state))
    except OSError as e:
        log.warning("write_state: %s failed: %s: %s", path, type(e).__name__, e)
_HANDSHAKE_RETRY_DELAY_S = 0.5
_DELAY_PRE_INIT_S = 0.050
_DELAY_POST_INIT_S = 0.200


class BaseDevice(Device[T]):
    """Shared lifecycle for every concrete wire :class:`Device`.

    Owns the parts that are the same on every wire — opening the transport,
    storing + announcing the handshake, tearing the connection down, and the
    handshake-derived profile — and delegates the wire-specific exchange to
    :meth:`_do_handshake`.  Still abstract: ``_do_handshake`` plus the inherited
    ``send`` keep it from instantiating on its own.
    """

    def __init_subclass__(cls, wire: Wire | None = None, **kwargs: Any) -> None:
        """Register the subclass under the ``wire=`` it declares.

        A wire adapter states its own key in its class line::

            class ScsiLcd(BaseDevice[ScsiTransport], wire=Wire.SCSI): ...

        which puts the key where it belongs — in the class definition, not in a
        decorator floating above it — and makes the registration impossible to
        separate from the class it registers.

        ``wire=None`` means "intermediate base, don't register", which is what
        :class:`BaseBulkDevice` is.
        """
        super().__init_subclass__(**kwargs)
        if wire is None:
            log.debug("%s: intermediate device base, not registered", cls.__name__)
            return
        log.debug("%s: registering as wire %s", cls.__name__, wire.value)
        DEVICES.register(wire)(cls)

    def __init__(self, info: ProductInfo, transport: T) -> None:
        log.debug("__init__: info=%s transport=%s", info, transport)
        super().__init__(info, transport)
        # Handshake-derived geometry + encoding flags.  Every LCD wire fills
        # this in ``_do_handshake``; LED leaves it None (no canvas), which is
        # exactly what the ``Device.profile`` port contract says.
        self._profile: DeviceProfile | None = None

    @property
    def profile(self) -> DeviceProfile | None:
        """Handshake-derived profile; None pre-handshake (and for LED)."""
        frame_log.debug("BaseDevice.profile: %s", self._profile)
        return self._profile

    # ── Connect — Template Method ────────────────────────────────────────

    def connect(self) -> HandshakeResult:
        """Open the transport, run the wire's handshake, publish the result.

        The wire-specific part is :meth:`_do_handshake`; everything around it
        is invariant, including the announcement line — which is a **parsed
        contract**, not just a log.  ``dev/tools/diagnose.py`` recovers the
        wire from ``<Class>Lcd handshake OK`` and the fingerprint from
        ``handshake OK: PM=N SUB=M … resolution=(w, h)``, and
        ``diagnostics/debug_report.py`` scrapes the same marker out of a
        reporter's log.  Emitting it once here is what keeps every wire
        scrapable — SCSI used to log ``SCSI handshake OK: FBL=…``, which
        matched neither pattern, so SCSI reports carried no PM/SUB at all.
        """
        self._open_transport()
        result = self._do_handshake()
        self._handshake = result
        log.info("%s handshake OK: PM=%d SUB=%d resolution=%s%s",
                 type(self).__name__, result.pm_byte, result.sub_byte,
                 result.resolution, self._handshake_detail(result))
        return result

    def _trace_reply(self, resp: bytes) -> None:
        """Record the RAW handshake reply — the one thing we cannot re-derive.

        Every parsed value on the ``handshake OK`` line above is our
        *interpretation* of these bytes.  When a panel resolves to the wrong
        geometry the question is always "what did the device actually say?",
        and the answer was reachable for HID only — every other wire discarded
        it, so finding out cost another round-trip with the reporter.
        ``trcc report`` is the entire diagnosis for hardware we do not own, so
        the bytes belong in it.

        They are also the mock's input: pasted into a ``dev/devices.json``
        ``reply`` they make the real adapters parse that device locally, which
        is the difference between reproducing a reporter's panel and guessing
        at it.  Without this line there is nothing to paste.

        TRACE (``-vvv``) because the ladder puts raw payloads and wire bytes
        there, and the logger is the SUBCLASS's module so a report still says
        which wire spoke.
        """
        log.debug("_trace_reply: resp=%s", Blob(resp))
        trace(logging.getLogger(type(self).__module__),
              "%s raw handshake reply (%d bytes, first %d): %s",
              self.info.key, len(resp), min(len(resp), _TRACE_REPLY_BYTES),
              resp[:_TRACE_REPLY_BYTES].hex())

    @abstractmethod
    def _do_handshake(self) -> HandshakeResult:
        """Run this wire's handshake exchange and return its result.

        Called with the transport already open.  Implementations set any
        wire-specific cached state (``self._profile``, LED's style) and return
        the result — storing it and announcing it is the base's job.
        """

    def _handshake_detail(self, result: HandshakeResult) -> str:
        """Wire-specific suffix for the shared ``handshake OK`` line.

        Anything a wire wants on the record beyond PM/SUB/resolution — the
        encoding it picked, the PID variant, the LED style.  Returns a string
        that already carries its own leading space, or empty.
        """
        log.debug("_handshake_detail: result=%s raw=%s",
                  result, Blob(result.raw_response))
        return ""

    def _open_transport(self) -> None:
        """Open the transport or raise — identical on every wire."""
        log.info("%s %s: opening transport", type(self).__name__, self.info.key)
        if not self._transport.open():
            log.error("%s %s: transport open failed",
                      type(self).__name__, self.info.key)
            raise HandshakeError(
                f"Failed to open {self.info.wire.value} transport "
                f"for {self.info.key}"
            )

    # ── Send — Template Method ───────────────────────────────────────────

    def send(self, payload: Any) -> bool:
        """Encode *payload* for this wire and write it under the shared policy.

        Three invariant steps, one wire-specific pair of hooks:

        1. refuse a send on a closed transport (:meth:`_require_connected`);
        2. turn the payload into the exact bytes this wire puts on the bus
           (:meth:`_prepare_frame`) — done ONCE, outside the retry, so a
           reconnect never re-encodes a ~200 KB frame;
        3. write them through ``_send_with_recovery``, which owns the
           reconnect-and-retry / consecutive-failure escalation every wire
           shares (``core.ports.Device``).
        """
        frame_log.debug("send: payload=%s", Blob(payload))
        self._require_connected()
        frame = self._prepare_frame(payload)
        return self._send_with_recovery(partial(self._write_frame, frame))

    @abstractmethod
    def _prepare_frame(self, payload: Any) -> bytes:
        """Build the exact byte string this wire writes for *payload*.

        Pure and side-effect free: it is called once per send, before the
        retry policy, so it must not touch the transport.
        """

    @abstractmethod
    def _write_frame(self, frame: bytes) -> bool:
        """Put *frame* on the bus; True when the wire considers it delivered.

        Called under ``_send_with_recovery``, so it may be invoked twice for
        one payload (once after a reconnect).  Return False for a soft,
        protocol-level failure; raise for a transport error.
        """

    def _require_connected(self) -> None:
        """Refuse a send on a closed transport — identical on every wire.

        One error type for the whole family: this used to be six copies, and
        one of them (HidLcd) had drifted to raising ``HandshakeError``, so the
        same mistake surfaced as a different exception depending on which
        panel was plugged in.
        """
        if self._transport.is_open:
            return
        log.error("%s %s: send() called before connect()",
                  type(self).__name__, self.info.key)
        raise TransportError(
            f"{type(self).__name__} {self.info.key} not connected — "
            f"call connect() first"
        )

    # ── Disconnect ───────────────────────────────────────────────────────

    def disconnect(self) -> None:
        """Close the transport and drop every handshake-derived cache."""
        log.info("%s %s: disconnecting", type(self).__name__, self.info.key)
        self._transport.close()
        self._handshake = None
        self._reset_state()

    def _reset_state(self) -> None:
        """Clear the wire's cached handshake state.

        Default drops the profile, which is what every wire that *derives* its
        geometry from the handshake wants.  A wire whose profile is a fixed
        constant, or that caches something else (Led), overrides.
        """
        log.debug("_reset_state")
        self._profile = None


class BaseBulkDevice(BaseDevice[BulkTransport]):
    """Shared vocabulary for the wires that speak raw USB bulk endpoints.

    Every panel except SCSI (Bulk, LY, Ali, HID LCD, LED) talks the same two
    primitives — write a request to an OUT endpoint, read the reply from an IN
    endpoint — and differ only in *which* endpoint, *what* they send, and
    whether the firmware answers first time.  Those two shapes live here:
    :meth:`_exchange` for the wires that ask once, :meth:`_handshake_retry` for
    the report-style wires whose firmware needs a few tries.

    The endpoints are class attributes rather than module constants so the
    shared bodies can reach them; a child sets ``_EP_WRITE`` to its own OUT
    endpoint (0x01 on the GrandVision bulk wires, 0x02 on the report wires).
    """

    #: IN endpoint — 0x81 on every device we speak to.
    _EP_READ: ClassVar[int] = 0x81
    #: OUT endpoint — differs per wire, so every child states its own.
    _EP_WRITE: ClassVar[int]

    def _exchange(self, request: bytes, read_size: int,
                  timeout_ms: int) -> bytes:
        """Write *request*, read *read_size* bytes back, return the reply.

        The one-shot handshake shape (Bulk / LY / Ali).  Translates a wire
        error into :class:`HandshakeError` so the caller — and the composition
        root above it — sees one failure type for "the device didn't answer",
        whatever the transport happened to raise.
        """
        try:
            self._transport.write(self._EP_WRITE, request, timeout_ms)
            return self._transport.read(self._EP_READ, read_size, timeout_ms)
        except TransportError as e:
            log.error("%s %s: handshake I/O failed: %s",
                      type(self).__name__, self.info.key, e)
            raise HandshakeError(
                f"{type(self).__name__} handshake I/O failed: {e}"
            ) from e

    def _handshake_retry(
        self,
        init_packet: bytes,
        read_size: int,
        parse: Callable[[bytes], HandshakeResult],
    ) -> HandshakeResult:
        """Init-write → settle → read → *parse*, retried up to 3 times.

        The report-style handshake (HID LCD + LED): the firmware may still be
        booting, or answer with a report we can't accept, so a bad exchange is
        worth repeating.  *parse* raises :class:`HandshakeError` for a reply it
        rejects — that counts as a failed attempt exactly like a transport
        error, which is why both funnel through one ``except``.
        """
        last_err: Exception | None = None
        for attempt in range(1, _HANDSHAKE_MAX_RETRIES + 1):
            try:
                time.sleep(_DELAY_PRE_INIT_S)
                self._transport.write(self._EP_WRITE, init_packet,
                                      HANDSHAKE_TIMEOUT_MS)
                time.sleep(_DELAY_POST_INIT_S)
                resp = self._transport.read(self._EP_READ, read_size,
                                            HANDSHAKE_TIMEOUT_MS)
                return parse(resp)
            except Exception as e:
                last_err = e
                log.warning("%s handshake attempt %d/%d failed: %s",
                            type(self).__name__, attempt,
                            _HANDSHAKE_MAX_RETRIES, e)
                if attempt < _HANDSHAKE_MAX_RETRIES:
                    time.sleep(_HANDSHAKE_RETRY_DELAY_S)

        raise HandshakeError(
            f"{type(self).__name__} handshake failed after "
            f"{_HANDSHAKE_MAX_RETRIES} attempts"
        ) from last_err
