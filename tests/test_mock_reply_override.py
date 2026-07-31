"""Mock handshake-reply override — the lever the dev console drives.

`set_active_reply(vid, pid, pm, sub, fbl)` pins the exact handshake reply a
vid:pid returns on the next `open_*()`, so the dev console can morph one device
through every variant that shares a vid:pid (and the app re-presents it).
"""
from __future__ import annotations

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


def test_set_active_reply_switches_handshake_bytes(tmp_path) -> None:
    plat = MockPlatform([{"vid": "87ad", "pid": "70db", "pm": 0, "name": "x"}],
                        tmp_path)

    plat.set_active_reply(_VID, _PID, pm=10, sub=0, fbl=58)
    first = plat.open_transport(Wire.BULK, _VID, _PID).read_script[0]

    plat.set_active_reply(_VID, _PID, pm=20, sub=1, fbl=58)
    second = plat.open_transport(Wire.BULK, _VID, _PID).read_script[0]

    assert first and second
    assert first != second          # different reply → different handshake bytes


def test_override_is_used_verbatim_not_truthy_gated(tmp_path) -> None:
    # The spec-default path SKIPS pm/sub when falsy (``if spec.pm``); an explicit
    # injected reply must be honoured exactly, including zero bytes.
    plat = MockPlatform(
        [{"vid": "87ad", "pid": "70db", "pm": 99, "sub": 5, "name": "x"}],
        tmp_path)

    spec_default = plat.open_transport(Wire.BULK, _VID, _PID).read_script[0]   # uses spec pm=99/sub=5

    plat.set_active_reply(_VID, _PID, pm=0, sub=0, fbl=58)
    forced_zero = plat.open_transport(Wire.BULK, _VID, _PID).read_script[0]    # exact pm=0/sub=0

    assert forced_zero != spec_default        # override won, verbatim

    # And it's deterministic for the same injected reply.
    again = plat.open_transport(Wire.BULK, _VID, _PID).read_script[0]
    assert again == forced_zero


def test_clearing_back_to_default(tmp_path) -> None:
    # No override set → falls back to the spec/geometry path (unchanged behaviour).
    plat = MockPlatform([{"vid": "87ad", "pid": "70db", "pm": 7, "name": "x"}],
                        tmp_path)
    a = plat.open_transport(Wire.BULK, _VID, _PID).read_script[0]
    b = plat.open_transport(Wire.BULK, _VID, _PID).read_script[0]
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
    app = App(MockPlatform([{"vid": "87ad", "pid": "70db"}], tmp_path),
              send_scheduler=SyncSendScheduler())
    try:
        app.platform.set_active_reply(_VID, _PID, pm=pm_a, sub=sub_a,
                                      fbl=pm_to_fbl(pm_a, sub_a))
        assert app.dispatch(ConnectDevice(key=key)).ok
        res_a = app.devices[key].profile.resolution

        # Inject the other variant's reply + reconnect (what the console does).
        app.platform.set_active_reply(_VID, _PID, pm=pm_b, sub=sub_b,
                                      fbl=pm_to_fbl(pm_b, sub_b))
        assert app.dispatch(ConnectDevice(key=key)).ok
        res_b = app.devices[key].profile.resolution

        assert res_a != res_b      # same vid:pid, different presentation
    finally:
        app.close()
