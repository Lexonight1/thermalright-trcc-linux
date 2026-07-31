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

import logging
import time
from abc import abstractmethod
from collections.abc import Callable
from functools import partial
from typing import Any, ClassVar

from ...core.errors import DeviceNotFoundError, HandshakeError, TransportError
from ...core.factory import Registry, Reject
from ...core.models import HandshakeResult, ProductInfo, Wire
from ...core.ports import BulkTransport, Device, T
from ...core.protocol import DeviceProfile

log = logging.getLogger(__name__)

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
        if wire is not None:
            DEVICES.register(wire)(cls)

    def __init__(self, info: ProductInfo, transport: T) -> None:
        super().__init__(info, transport)
        # Handshake-derived geometry + encoding flags.  Every LCD wire fills
        # this in ``_do_handshake``; LED leaves it None (no canvas), which is
        # exactly what the ``Device.profile`` port contract says.
        self._profile: DeviceProfile | None = None

    @property
    def profile(self) -> DeviceProfile | None:
        """Handshake-derived profile; None pre-handshake (and for LED)."""
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
        constant (AliLcd) or that caches something else (Led) overrides.
        """
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
