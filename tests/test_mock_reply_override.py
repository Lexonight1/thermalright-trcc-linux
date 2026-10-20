"""Mock handshake-reply override — the lever the dev console drives.

`set_active_reply(vid, pid, pm, sub, fbl)` pins the exact handshake reply a
vid:pid returns on the next `open_*()`, so the dev console can morph one device
through every variant that shares a vid:pid (and the app re-presents it).
"""
from __future__ import annotations

from tests.conftest import FakeBulkTransport
from tests.mock_platform import MockPlatform
from trcc.adapters.infra.send_scheduler import SyncSendScheduler
from trcc.app import App
from trcc.core.commands import ConnectDevice
from trcc.core.models import Wire
from trcc.core.protocol import get_profile, pm_to_fbl
from trcc.core.variants import _VARIANT_REGISTRY

# A bulk device whose reply encodes PM + sub_byte (so switching them changes the
# reply bytes) — 76 catalog variants share this single vid:pid in real life.
_VID, _PID = 0x87AD, 0x70DB


def _first_reply(plat: MockPlatform) -> bytes:
    """The scripted handshake reply a fresh bulk open of 87ad:70db returns."""
    transport = plat.open_transport(Wire.BULK, _VID, _PID)
    assert isinstance(transport, FakeBulkTransport)
    return transport.read_script[0]


def _resolution(app: App, key: str) -> tuple[int, int]:
    """The connected device's resolved canvas."""
    profile = app.devices[key].profile
    assert profile is not None
    return profile.resolution


def test_set_active_reply_switches_handshake_bytes(tmp_path) -> None:
    plat = MockPlatform([{"vid": "87ad", "pid": "70db", "pm": 0, "name": "x"}],
                        tmp_path)

    plat.set_active_reply(_VID, _PID, pm=10, sub=0, fbl=58)
    first = _first_reply(plat)

    plat.set_active_reply(_VID, _PID, pm=20, sub=1, fbl=58)
    second = _first_reply(plat)

    assert first and second
    assert first != second          # different reply → different handshake bytes


def test_override_is_used_verbatim_not_truthy_gated(tmp_path) -> None:
    # The spec-default path SKIPS pm/sub when falsy (``if spec.pm``); an explicit
    # injected reply must be honoured exactly, including zero bytes.
    plat = MockPlatform(
        [{"vid": "87ad", "pid": "70db", "pm": 99, "sub": 5, "name": "x"}],
        tmp_path)

    spec_default = _first_reply(plat)   # uses spec pm=99/sub=5

    plat.set_active_reply(_VID, _PID, pm=0, sub=0, fbl=58)
    forced_zero = _first_reply(plat)    # exact pm=0/sub=0

    assert forced_zero != spec_default        # override won, verbatim

    # And it's deterministic for the same injected reply.
    again = _first_reply(plat)
    assert again == forced_zero


def test_clearing_back_to_default(tmp_path) -> None:
    # No override set → falls back to the spec/geometry path (unchanged behaviour).
    plat = MockPlatform([{"vid": "87ad", "pid": "70db", "pm": 7, "name": "x"}],
                        tmp_path)
    a = _first_reply(plat)
    b = _first_reply(plat)
    assert a == b and a            # stable default reply, no override in play


def _two_variants_with_different_resolution() -> tuple[
        tuple[int, int], tuple[int, int]]:
    """Two (pm, sub) of 87ad:70db that the app resolves to different canvases."""
    by_res: dict[tuple[int, int], tuple[int, int]] = {}
    for pm, subs in _VARIANT_REGISTRY[(_VID, _PID)].items():
        for sub in subs:
            rsub = sub if sub is not None else 0
            res = get_profile(pm_to_fbl(pm, rsub), pm).resolution
            by_res.setdefault(res, (pm, rsub))
    pairs = list(by_res.values())
    return pairs[0], pairs[1]


def test_inject_reply_reresolves_profile_through_real_connect(tmp_path) -> None:
    """End-to-end: injecting a different reply makes the REAL app re-handshake
    and resolve a different profile — the whole point of the dev console."""
    (pm_a, sub_a), (pm_b, sub_b) = _two_variants_with_different_resolution()
    key = "87ad:70db"
    plat = MockPlatform([{"vid": "87ad", "pid": "70db"}], tmp_path)
    app = App(plat, send_scheduler=SyncSendScheduler())
    try:
        plat.set_active_reply(_VID, _PID, pm=pm_a, sub=sub_a,
                                      fbl=pm_to_fbl(pm_a, sub_a))
        assert app.dispatch(ConnectDevice(key=key)).ok
        res_a = _resolution(app, key)

        # Inject the other variant's reply + reconnect (what the console does).
        plat.set_active_reply(_VID, _PID, pm=pm_b, sub=sub_b,
                                      fbl=pm_to_fbl(pm_b, sub_b))
        assert app.dispatch(ConnectDevice(key=key)).ok
        res_b = _resolution(app, key)

        assert res_a != res_b      # same vid:pid, different presentation
    finally:
        app.close()


# ── Verbatim reply — a reporter's actual bytes, our model NOT consulted ──────
#
# Everything above varies a VALUE we then pack ourselves, and the default
# geometry is brute-forced through the app's own ``pm_to_fbl`` / ``get_profile``
# until it reproduces the registry's declared resolution.  That is faithful for
# "show me every SKU we believe in" and structurally unable to reproduce "this
# cooler does not behave the way we believe" — the shape of every reporter bug.
# ``spec.reply`` is the one path that skips our packing entirely.


def test_spec_reply_is_returned_untouched(tmp_path) -> None:
    """The bytes on disk are the bytes on the wire — no re-packing."""
    raw = bytes(range(64))
    plat = MockPlatform(
        [{"vid": "87ad", "pid": "70db", "name": "captured",
          "reply": raw.hex()}], tmp_path)

    assert _first_reply(plat) == raw


def test_spec_reply_can_contradict_the_registry(tmp_path) -> None:
    """The whole point: a reply our model would never have produced.

    The registry says this vid:pid is 480x480 (FBL 72).  Feed it a reply whose
    PM says 320x320 and the device must resolve **320x320** — the bytes win.
    If this ever asserts the registry's answer instead, the mock has gone back
    to confirming itself and can no longer reproduce a reporter's device.
    """
    from tests.mock_platform import bulk_handshake_reply

    contradicting_pm = 32                      # RGB565 320x320, not 480x480
    plat = MockPlatform(
        [{"vid": "87ad", "pid": "70db", "name": "contradicts",
          "reply": bulk_handshake_reply(contradicting_pm).hex()}], tmp_path)
    app = App(plat, send_scheduler=SyncSendScheduler())
    try:
        assert app.dispatch(ConnectDevice(key=f"{_VID:04x}:{_PID:04x}")).ok
        registry_says = get_profile(pm_to_fbl(72), 72).resolution
        device_says = _resolution(app, f"{_VID:04x}:{_PID:04x}")
        assert registry_says == (480, 480)
        assert device_says == (320, 320), (
            "the verbatim reply was ignored — the mock is confirming our own "
            f"registry ({registry_says}) instead of parsing the bytes given")
    finally:
        app.close()


def test_malformed_reply_hex_falls_back_instead_of_exploding(tmp_path) -> None:
    """One typo in one fixture row must not take the whole fleet down."""
    plat = MockPlatform(
        [{"vid": "87ad", "pid": "70db", "name": "typo", "reply": "not-hex!"}],
        tmp_path)

    assert _first_reply(plat)


def test_live_override_beats_a_spec_reply(tmp_path) -> None:
    """A variant click is the user acting now; the fixture is only a default."""
    raw = bytes(range(64))
    plat = MockPlatform(
        [{"vid": "87ad", "pid": "70db", "name": "captured",
          "reply": raw.hex()}], tmp_path)

    plat.set_active_reply(_VID, _PID, pm=20, sub=1, fbl=58)
    assert _first_reply(plat) != raw


def test_traced_bytes_round_trip_into_a_spec_reply(tmp_path, caplog) -> None:
    """The reporter loop, end to end — the reason both halves exist.

    ``BaseDevice._trace_reply`` puts the raw reply in ``trcc report`` at
    ``-vvv``; ``DeviceSpec.reply`` takes it back.  If the hex a reporter sends
    does not reproduce their device here, the loop is decorative — so assert
    the actual bytes the trace emitted, parsed back, land on the same geometry.
    """

    from trcc.core.logs import TRACE

    key = f"{_VID:04x}:{_PID:04x}"
    specs = [{"vid": "87ad", "pid": "70db", "pm": 72, "name": "origin"}]

    with caplog.at_level(TRACE, logger="trcc.adapters.device.bulk_lcd"):
        app = App(MockPlatform(specs, tmp_path / "a"),
                  send_scheduler=SyncSendScheduler())
        try:
            assert app.dispatch(ConnectDevice(key=key)).ok
            origin = _resolution(app, key)
        finally:
            app.close()

    traced = [r.getMessage() for r in caplog.records
              if r.levelno == TRACE and "raw handshake reply" in r.getMessage()]
    assert traced, "no raw reply was traced — a reporter would have nothing to send"
    hex_from_log = traced[0].rsplit(": ", 1)[1]

    replayed = App(
        MockPlatform([{"vid": "87ad", "pid": "70db", "name": "replay",
                       "reply": hex_from_log}], tmp_path / "b"),
        send_scheduler=SyncSendScheduler())
    try:
        assert replayed.dispatch(ConnectDevice(key=key)).ok
        assert _resolution(replayed, key) == origin
    finally:
        replayed.close()
