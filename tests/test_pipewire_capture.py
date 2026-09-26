"""The Wayland backend, behind the stateless port.

The portal handshake itself cannot be tested here — it needs a compositor that
provides ``org.freedesktop.portal.ScreenCast`` and a human to approve the
consent dialog.  What CAN be tested is everything wrapped around it, which is
where a break would come from: the portal internals moved out of ``ui/gui``
byte-for-byte, the wrapper is new.

So the session is injected.  Every test below drives a fake one and asserts
the POLICY: serve the fallback until the portal is up, switch when it is, and
never block a caller on a consent dialog.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from contextlib import suppress
from pathlib import Path
from typing import Any

import pytest

from trcc.adapters.screencast import pipewire as pw
from trcc.adapters.screencast.pipewire import (
    PipeWireScreenCapture,
    crop_rgb24,
    unpad_rows,
)
from trcc.core.models import RawFrame
from trcc.core.ports import CaptureNotReady, ScreenCapture


class _Fallback(ScreenCapture):
    """Stands in for the Qt chain; says who answered."""

    def __init__(self) -> None:
        self.calls = 0

    def grab_region(self, x: int, y: int, width: int, height: int) -> RawFrame:
        self.calls += 1
        return RawFrame(data=b"\x01\x02\x03" * (width * height),
                        width=width, height=height)


class _Session:
    """A portal session that is exactly as ready as the test says."""

    def __init__(self, running: bool = False, frame: Any = None,
                 start_result: bool = True) -> None:
        self.is_running = running
        self._frame = frame
        self._start_result = start_result
        self.started = threading.Event()
        self.stopped = False

    def start(self, timeout: float = 30.0) -> bool:
        self.started.set()
        self.is_running = self._start_result
        return self._start_result

    def grab_frame(self) -> Any:
        return self._frame

    def stop(self) -> None:
        self.stopped = True
        self.is_running = False


class _Pending(_Session):
    """A session whose start genuinely WAITS, as a consent dialog does.

    ``_Session.start`` flips ``is_running`` the moment the background thread
    runs, so a test built on it never meets the "still starting" branch --
    measured: with the old fallback-while-pending policy put back, the test
    that used it still passed.  This one stays pending until ``stop``.
    """

    def __init__(self) -> None:
        super().__init__(running=False)
        self.release = threading.Event()

    def start(self, timeout: float = 30.0) -> bool:
        self.started.set()
        self.release.wait(timeout)
        return False

    def stop(self) -> None:
        self.release.set()
        super().stop()


def _capture(session: _Session, fallback: _Fallback) -> PipeWireScreenCapture:
    """The factory takes the stored token and a sink for a fresh one."""
    def factory(restore_token=None, on_restore_token=None) -> _Session:
        return session

    return PipeWireScreenCapture(fallback, session_factory=factory)


# ── the stride bug ────────────────────────────────────────────────────


def test_padded_rows_are_unpadded() -> None:
    """GStreamer pads each row; a consumer assuming ``width * 3`` shears.

    MEASURED with ``GstVideo.VideoInfo`` on a real GStreamer 1.28: a
    1366-wide RGB frame has a stride of 4100 against ``width * 3 == 4098``.
    Two bytes a row, 768 rows — every row starts further left than the one
    above it and the picture leans.
    """
    w, h, stride = 1366, 4, 4100
    padded = b"".join(bytes([r]) * (w * 3) + b"\xEE\xEE" for r in range(h))

    tight = unpad_rows(padded, w, h, stride)

    assert len(tight) == w * 3 * h
    assert b"\xEE" not in tight, "padding survived into the pixels"
    assert [tight[r * w * 3] for r in range(h)] == list(range(h)), (
        "rows are misaligned — this is the diagonal shear"
    )


def test_an_already_tight_buffer_is_returned_unchanged() -> None:
    """The common case must cost one comparison, not a rebuild."""
    w, h = 64, 4
    tight = b"\x7f" * (w * 3 * h)

    assert unpad_rows(tight, w, h, w * 3) is tight


# ── the crop ──────────────────────────────────────────────────────────


def test_a_region_is_cut_out_of_the_full_screen_frame() -> None:
    """The portal hands back a whole screen; the port asked for a rectangle."""
    w, h = 8, 4
    data = bytes([(x + y * w) % 256 for y in range(h) for x in range(w)
                  for _ in range(3)])

    f = crop_rgb24(data, w, h, 2, 1, 3, 2)

    assert (f.width, f.height) == (3, 2)
    assert f.data == bytes([10, 10, 10, 11, 11, 11, 12, 12, 12,
                            18, 18, 18, 19, 19, 19, 20, 20, 20])


def test_a_region_past_the_edge_is_clamped() -> None:
    """The user shares one monitor and picks a region against another.

    Without the clamp this indexes past the buffer and the frame is garbage
    or a crash, on data that came from outside the app.
    """
    w, h = 4, 4
    f = crop_rgb24(b"\x09" * (w * h * 3), w, h, 3, 3, 99, 99)

    assert (f.width, f.height) == (1, 1)
    assert len(f.data) == 3


def test_the_crop_never_logs_the_frame_bytes(caplog) -> None:
    """The crop line used to write the whole buffer, per frame: 968 KB at
    capture rate rolled the 10 MB log twice in two seconds and erased the
    first minute of the run it was meant to explain.  The line itself must
    stay -- it is the evidence a report needs -- with the geometry only.
    """
    w, h = 16, 8
    data = b"\xab\xcd\xef" * (w * h)
    with caplog.at_level(logging.DEBUG, logger=pw.frame_log.name):
        crop_rgb24(data, w, h, 0, 0, 4, 4)

    lines = [r.getMessage() for r in caplog.records
             if "crop_rgb24" in r.getMessage()]
    assert lines, "the crop no longer logs at all"
    assert all(f"{w}x{h}" in line for line in lines), lines
    assert not any("xab" in line or "xcd" in line for line in lines), (
        "the frame bytes are back in the log line")


# ── the policy ────────────────────────────────────────────────────────


def test_the_fallback_is_not_asked_while_the_portal_is_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A consent dialog must never make a caller wait, and must never make
    the fallback hammer the compositor either.

    This test asserted the OPPOSITE until 2026-09-18 -- "the fallback answers
    until the portal is up", the gui's old policy.  On Plasma that fallback
    is spectacle, a full compositor screenshot every 0.4 s from a client not
    processing its own events while KWin put up its dialog.  Now: no frame,
    ``CaptureNotReady``, fallback untouched.
    """
    monkeypatch.setattr(pw, "PIPEWIRE_AVAILABLE", True)
    fallback = _Fallback()
    session = _Pending()
    cap = _capture(session, fallback)
    try:
        with pytest.raises(CaptureNotReady, match="still starting"):
            cap.grab_region(0, 0, 4, 4)
        assert session.started.wait(1)
        with pytest.raises(CaptureNotReady, match="still starting"):
            cap.grab_region(0, 0, 4, 4)       # and still, once the dialog is up
    finally:
        cap.stop()

    assert fallback.calls == 0, "the fallback was asked during consent"



def test_the_portal_frame_wins_once_the_session_is_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pw, "PIPEWIRE_AVAILABLE", True)
    fallback = _Fallback()
    session = _Session(running=True,
                       frame=(8, 4, bytes([0xAB]) * (8 * 4 * 3)))

    frame = _capture(session, fallback).grab_region(1, 1, 2, 2)

    assert fallback.calls == 0, "the fallback answered while the portal was up"
    assert (frame.width, frame.height) == (2, 2)
    assert frame.data == bytes([0xAB]) * 12


def test_a_running_session_with_no_frame_yet_is_not_ready_either(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Up but not yet streaming: still "not yet", never the fallback."""
    monkeypatch.setattr(pw, "PIPEWIRE_AVAILABLE", True)
    fallback = _Fallback()

    with pytest.raises(CaptureNotReady):
        _capture(_Session(running=True, frame=None), fallback).grab_region(
            0, 0, 4, 4)

    assert fallback.calls == 0


def test_a_portal_that_refuses_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Consent denied or timed out is "no", not "not yet": the fallback
    answers from then on, and no session is retried behind the user's back.
    """
    monkeypatch.setattr(pw, "PIPEWIRE_AVAILABLE", True)
    fallback = _Fallback()
    session = _Session(running=False, start_result=False)
    cap = _capture(session, fallback)

    with suppress(CaptureNotReady):
        cap.grab_region(0, 0, 4, 4)           # starts the session, off-thread
    assert session.started.wait(2), "the session start never ran"
    deadline = time.monotonic() + 2
    while cap._session is not None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert cap._session is None, "a refused start was kept as pending"
    settled = fallback.calls                  # 0, or 1 if the thread won the first grab

    frame = cap.grab_region(0, 0, 4, 4)

    assert fallback.calls == settled + 1, "the fallback did not answer after the refusal"
    assert frame.width == 4



def test_without_the_bindings_the_portal_is_never_touched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No PyGObject → pure fallback, which is what every face does today."""
    monkeypatch.setattr(pw, "PIPEWIRE_AVAILABLE", False)
    fallback = _Fallback()
    session = _Session(running=True, frame=(8, 4, b"\x00" * 96))

    _capture(session, fallback).grab_region(0, 0, 4, 4)

    assert fallback.calls == 1
    assert not session.started.is_set(), (
        "a consent dialog was raised on a box with no portal bindings"
    )


def test_the_session_starts_once_across_many_grabs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One dialog per run, not one per frame."""
    monkeypatch.setattr(pw, "PIPEWIRE_AVAILABLE", True)
    made: list[_Session] = []

    def factory(restore_token=None, on_restore_token=None) -> _Session:
        made.append(_Session(running=False))
        return made[-1]

    cap = PipeWireScreenCapture(_Fallback(), session_factory=factory)
    for _ in range(5):
        with suppress(CaptureNotReady):
            cap.grab_region(0, 0, 4, 4)

    assert len(made) == 1, f"the portal session was built {len(made)} times"


def test_construction_alone_raises_no_dialog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``build_screen_capture`` runs for every face, screencast or not.

    Starting the session in the constructor would pop a "share your screen?"
    dialog at somebody who only ran ``trcc theme list``.
    """
    monkeypatch.setattr(pw, "PIPEWIRE_AVAILABLE", True)
    made: list[_Session] = []

    def factory(restore_token=None, on_restore_token=None) -> _Session:
        made.append(_Session())
        return made[-1]

    PipeWireScreenCapture(_Fallback(), session_factory=factory)

    assert made == [], "a portal session was created before anyone captured"


def test_stop_lets_the_next_grab_start_a_fresh_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pw, "PIPEWIRE_AVAILABLE", True)
    made: list[_Session] = []

    def factory(restore_token=None, on_restore_token=None) -> _Session:
        made.append(_Session(running=False))
        return made[-1]

    cap = PipeWireScreenCapture(_Fallback(), session_factory=factory)
    with suppress(CaptureNotReady):
        cap.grab_region(0, 0, 4, 4)
    cap.stop()
    with suppress(CaptureNotReady):
        cap.grab_region(0, 0, 4, 4)

    assert len(made) == 2
    assert made[0].stopped is True


def test_the_shared_chooser_puts_the_portal_in_front_of_qt() -> None:
    """Every face gets the same answer for the same session.

    ``build_screen_capture`` has one caller, ``BaseOS._build_screen_capture``,
    behind ``Platform.screen_capture()`` -- and "which backend" is one
    decision there, which is what makes the CLI, the REST route, qtgui and
    the gui window gain Wayland capture at once.  On Wayland the portal
    fronts the desktop's own tool; Qt's grab is not composed at all.
    """
    from trcc.adapters.screencast import build_screen_capture
    from trcc.adapters.screencast.qt import ToolCapture
    from trcc.core.models import DisplayServer, DisplaySession

    cap = build_screen_capture(DisplaySession(DisplayServer.WAYLAND, ("gnome",)))

    assert isinstance(cap, PipeWireScreenCapture)
    assert isinstance(cap._fallback, ToolCapture)


def test_a_region_outside_the_shared_area_is_reported_once(
    monkeypatch: pytest.MonkeyPatch, caplog,
) -> None:
    """A region the stream cannot cover must not be cropped in silence.

    ``crop_rgb24`` clamps, which is correct -- the alternative indexes past
    the buffer -- but it clamped silently, so the panel showed a smaller
    picture stretched to fit and nothing said why.  MEASURED 2026-09-18: a
    Plasma grant recorded a hand-drawn 1895x1008 REGION of a 1920x1080
    screen, was remembered, and replayed for six hours.

    Once per distinct (source, region): this is on the frame path, and a
    per-frame warning buries the one-shot lines a report is read for.
    """
    monkeypatch.setattr(pw, "PIPEWIRE_AVAILABLE", True)
    session = _Session(running=True, frame=(40, 20, bytes(40 * 20 * 3)))
    cap = _capture(session, _Fallback())

    with caplog.at_level(logging.WARNING,
                         logger="trcc.adapters.screencast.pipewire"):
        for _ in range(5):
            cap.grab_region(30, 10, 20, 20)     # runs off a 40x20 source

    warnings = [r.getMessage() for r in caplog.records
                if "does not fit" in r.getMessage()]
    assert len(warnings) == 1, warnings
    assert "40x20" in warnings[0], warnings[0]


def test_a_region_inside_the_shared_area_says_nothing(
    monkeypatch: pytest.MonkeyPatch, caplog,
) -> None:
    """The warning must not cry wolf on every ordinary capture."""
    monkeypatch.setattr(pw, "PIPEWIRE_AVAILABLE", True)
    session = _Session(running=True, frame=(40, 20, bytes(40 * 20 * 3)))
    cap = _capture(session, _Fallback())

    with caplog.at_level(logging.WARNING,
                         logger="trcc.adapters.screencast.pipewire"):
        cap.grab_region(0, 0, 40, 20)           # exactly the source
        cap.grab_region(8, 4, 16, 8)            # comfortably inside

    assert not [r for r in caplog.records if "does not fit" in r.getMessage()]


def test_the_granted_stream_size_is_logged(
    monkeypatch: pytest.MonkeyPatch, caplog,
) -> None:
    """What the desktop actually shared is the one fact a report needs.

    Only the PipeWire node id was logged, which says nothing about whether
    the user shared their screen or a rectangle they drew by accident.
    """
    from trcc.adapters.screencast.pipewire import PipeWireScreenCast

    session = PipeWireScreenCast()          # constructing it touches no portal
    results = {"streams": [(77, {"size": (1895, 1008), "source_type": 1})]}
    with caplog.at_level(logging.INFO,
                         logger="trcc.adapters.screencast.pipewire"):
        try:
            PipeWireScreenCast._on_start_response(session, 0, results)
        except Exception:
            pass                                # the pipeline half needs a portal

    granted = [r.getMessage() for r in caplog.records if "granted" in r.getMessage()]
    assert granted, [r.getMessage() for r in caplog.records]
    assert "1895x1008" in granted[0], granted[0]
    assert session._stream_size == (1895, 1008)


# ── the restore token ─────────────────────────────────────────────────


class _TokenSession(_Session):
    """Records the token it was handed, and can issue a new one."""

    def __init__(self, issues: str | None = None, **kw: Any) -> None:
        super().__init__(**kw)
        self.received: str | None = kw.get("restore_token")
        self._issues = issues

    def start(self, timeout: float = 30.0) -> bool:
        if self._issues is not None and self._on_token is not None:
            self._on_token(self._issues)
        return super().start(timeout)


def _token_capture(tmp_path, **session_kw):
    made: dict[str, Any] = {}

    def factory(restore_token=None, on_restore_token=None):
        s = _TokenSession(**session_kw)
        s.received = restore_token
        s._on_token = on_restore_token
        made["session"] = s
        return s

    cap = PipeWireScreenCapture(_Fallback(), config_dir=tmp_path,
                                session_factory=factory)
    return cap, made


def test_a_fresh_install_has_no_token_to_replay(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(pw, "PIPEWIRE_AVAILABLE", True)
    cap, made = _token_capture(tmp_path)

    with suppress(CaptureNotReady):
        cap.grab_region(0, 0, 4, 4)

    assert made["session"].received is None


def test_the_token_the_portal_issues_is_kept(tmp_path, monkeypatch) -> None:
    """Half the contract, and the half that was missing.

    ``persist_mode: 2`` asks the portal to remember the grant; the portal
    answers with a ``restore_token`` on Start.  We asked and then dropped the
    answer, so every launch prompted however many times the user had already
    said yes.
    """
    monkeypatch.setattr(pw, "PIPEWIRE_AVAILABLE", True)
    cap, made = _token_capture(tmp_path, issues="tok-abc")

    with suppress(CaptureNotReady):
        cap.grab_region(0, 0, 4, 4)
    made["session"].started.wait(2.0)

    stored = tmp_path / PipeWireScreenCapture.TOKEN_FILE
    assert stored.exists(), "the portal issued a token and it was dropped"
    assert stored.read_text() == "tok-abc"


def test_a_stored_token_is_replayed_on_the_next_run(
    tmp_path, monkeypatch,
) -> None:
    """The next launch must not ask again."""
    monkeypatch.setattr(pw, "PIPEWIRE_AVAILABLE", True)
    (tmp_path / PipeWireScreenCapture.TOKEN_FILE).write_text("tok-xyz")

    cap, made = _token_capture(tmp_path)
    with suppress(CaptureNotReady):
        cap.grab_region(0, 0, 4, 4)

    assert made["session"].received == "tok-xyz", (
        "the stored token was not handed to the portal — the user is asked "
        "for consent they already gave"
    )


def test_the_token_file_is_owner_only(tmp_path, monkeypatch) -> None:
    """It is a capability: whoever reads it can re-grant our screen access."""
    monkeypatch.setattr(pw, "PIPEWIRE_AVAILABLE", True)
    cap, made = _token_capture(tmp_path, issues="tok-secret")

    with suppress(CaptureNotReady):
        cap.grab_region(0, 0, 4, 4)
    made["session"].started.wait(2.0)

    mode = (tmp_path / PipeWireScreenCapture.TOKEN_FILE).stat().st_mode & 0o777
    assert mode == 0o600, f"token file is {mode:o}, not owner-only"


def test_a_torn_write_never_replaces_a_good_token(
    tmp_path, monkeypatch,
) -> None:
    """The write is atomic, so a crash mid-write cannot cost the grant.

    A restore token is SINGLE USE: replaying one makes the portal delete its
    stored grant and mint a replacement.  So a half-written file is not a
    stale token, it is NO token -- measured 2026-09-18, where a replayed
    token's entry answered ``NotFound`` afterwards.  The writer used to be a
    plain ``write_text`` straight onto the live path.
    """
    monkeypatch.setattr(pw, "PIPEWIRE_AVAILABLE", True)
    live = tmp_path / PipeWireScreenCapture.TOKEN_FILE
    live.write_text("good-token-from-last-run")

    real_replace = Path.replace

    def die_before_rename(self, target):        # the crash, mid-write
        raise OSError("disk full")

    monkeypatch.setattr(Path, "replace", die_before_rename)
    cap, made = _token_capture(tmp_path, issues="tok-new")
    with suppress(CaptureNotReady):
        cap.grab_region(0, 0, 4, 4)
    made["session"].started.wait(2.0)
    monkeypatch.setattr(Path, "replace", real_replace)

    assert live.read_text() == "good-token-from-last-run", (
        "a failed write destroyed the token that was already there")
    assert not list(tmp_path.glob("*.tmp")), (
        f"the temp file was left behind: {list(tmp_path.iterdir())}")


def test_a_token_that_does_not_read_back_is_an_error_not_a_shrug(
    tmp_path, monkeypatch, caplog,
) -> None:
    """Losing this file costs the grant, so the failure is loud.

    It was a ``log.warning`` saying "the portal will ask again next run",
    which understated it twice over: the level, and the consequence.
    """
    monkeypatch.setattr(pw, "PIPEWIRE_AVAILABLE", True)

    real_fdopen = os.fdopen
    monkeypatch.setattr(
        "os.fdopen", lambda *a, **k: _TruncatingFile(real_fdopen(*a, **k)))
    cap, made = _token_capture(tmp_path, issues="tok-new")
    with caplog.at_level(logging.DEBUG, logger="trcc.adapters.screencast.pipewire"):
        with suppress(CaptureNotReady):
            cap.grab_region(0, 0, 4, 4)
        made["session"].started.wait(2.0)

    errors = [r for r in caplog.records
              if r.levelno >= logging.ERROR and "_write_token" in r.getMessage()]
    assert errors, [r.getMessage() for r in caplog.records]
    assert "approved again" in errors[0].getMessage(), errors[0].getMessage()


class _TruncatingFile:
    """Storage that accepts a write, reports success, and keeps nothing.

    Wraps a REAL file so ``fileno`` and ``fsync`` behave -- an earlier
    version returned fd 0, which made ``fsync`` raise, so the test passed on
    that error instead of on the read-back check it exists to prove.  Caught
    by mutation: deleting the read-back left it green.
    """

    def __init__(self, real) -> None:
        self._real = real

    def write(self, data: str) -> int:
        return len(data)            # claims success, writes nothing

    def flush(self) -> None:
        self._real.flush()

    def fileno(self) -> int:
        return self._real.fileno()

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> bool:
        self._real.close()
        return False


def test_without_a_config_dir_the_token_is_not_written(
    tmp_path, monkeypatch,
) -> None:
    """No directory → the run keeps it, nothing is persisted, nothing crashes."""
    monkeypatch.setattr(pw, "PIPEWIRE_AVAILABLE", True)
    made: dict[str, Any] = {}

    def factory(restore_token=None, on_restore_token=None):
        s = _TokenSession()
        s._on_token = on_restore_token
        made["session"] = s
        if on_restore_token is not None:
            on_restore_token("tok-nowhere")
        return s

    cap = PipeWireScreenCapture(_Fallback(), config_dir=None,
                                session_factory=factory)
    with suppress(CaptureNotReady):
        cap.grab_region(0, 0, 4, 4)

    assert list(tmp_path.iterdir()) == []


def test_an_unreadable_token_costs_a_prompt_not_a_capture(
    tmp_path, monkeypatch,
) -> None:
    """A broken token file must never stop the screencast.

    The session still starts -- the portal will ask, since no token could be
    replayed -- and the first grab is "not yet", never a file error.
    """
    monkeypatch.setattr(pw, "PIPEWIRE_AVAILABLE", True)
    bad = tmp_path / PipeWireScreenCapture.TOKEN_FILE
    bad.mkdir()          # a directory where a file belongs

    cap, made = _token_capture(tmp_path)
    with pytest.raises(CaptureNotReady):
        cap.grab_region(0, 0, 4, 4)

    assert made["session"].started.wait(1), "a bad token file stopped the session"
    assert made["session"].received is None


# ── the SESSION's own token logic ─────────────────────────────────────
#
# The tests above drive a FAKE session, so they pin the adapter's file
# handling and nothing else — verified by mutation: deleting the token
# capture and disabling the replay both left them green.  These reach the
# real ``PipeWireScreenCast`` methods instead.


def test_the_session_keeps_the_token_the_portal_returns() -> None:
    """``_remember_restore_token`` is the half that was missing entirely."""
    from trcc.adapters.screencast.pipewire import PipeWireScreenCast

    seen: list[str] = []
    session = PipeWireScreenCast(on_restore_token=seen.append)

    session._remember_restore_token({"restore_token": "tok-from-portal"})

    assert session._restore_token == "tok-from-portal"
    assert seen == ["tok-from-portal"], "the token was not handed to the store"


def test_a_portal_that_issues_no_token_is_not_an_error() -> None:
    """"This time only", or a backend without persistence — just re-prompt."""
    from trcc.adapters.screencast.pipewire import PipeWireScreenCast

    seen: list[str] = []
    session = PipeWireScreenCast(on_restore_token=seen.append)

    session._remember_restore_token({"streams": [(42, {})]})

    assert session._restore_token is None
    assert seen == []


@pytest.mark.skipif(not pw.PIPEWIRE_AVAILABLE,
                    reason="needs dbus for the option types")
def test_select_options_replays_a_stored_token() -> None:
    """The request half: the token must reach ``SelectSources``.

    ``persist_mode`` alone is what we shipped, and it does nothing on its own
    — the portal remembers the grant and then cannot match it to us.
    """
    from trcc.adapters.screencast.pipewire import PipeWireScreenCast

    opts = PipeWireScreenCast(restore_token="tok-stored")._select_options("h")

    assert str(opts["restore_token"]) == "tok-stored"
    assert int(opts["persist_mode"]) == 2, "persistence was not requested"


@pytest.mark.skipif(not pw.PIPEWIRE_AVAILABLE,
                    reason="needs dbus for the option types")
def test_select_options_omits_the_token_when_there_is_none() -> None:
    """A fresh install must not send an empty token and confuse the portal."""
    from trcc.adapters.screencast.pipewire import PipeWireScreenCast

    opts = PipeWireScreenCast()._select_options("h")

    assert "restore_token" not in opts
    assert int(opts["types"]) == 1, "monitor capture was not requested"


# ── the stride CLAIM can disagree with the DATA ───────────────────────


def test_a_buffer_shorter_than_the_stride_claim_is_not_sliced_past_its_end(
    caplog,
) -> None:
    """The exact shape measured against xdg-desktop-portal-wlr.

    It hands a TIGHTLY PACKED 854x480 buffer (1_229_760 bytes) while the caps
    advertise the aligned stride 2564.  Unpadding on that claim slices at the
    wrong offsets and returns 1_228_802 bytes -- 958 short, sheared 427 px.

    The data wins over the claim, and it says so.
    """
    w, h = 854, 480
    row, claimed = w * 3, 2564
    tight = b"".join(bytes([r % 200]) * row for r in range(h))
    assert len(tight) == 1_229_760, "fixture is not the measured buffer"

    with caplog.at_level(logging.WARNING, logger="trcc.core._frames"):
        out = unpad_rows(tight, w, h, claimed)

    assert out is tight, "a tight buffer must survive a wrong stride claim"
    assert len(out) == row * h == 1_229_760
    assert "trusting the buffer" in caplog.text, (
        "a stride claim contradicted by the data must not be silent"
    )


def test_the_honest_padded_case_still_unpads() -> None:
    """GNOME's 854x480 buffer IS padded — 2564 x 480 — and must still strip.

    The guard must not 'fix' the case that was never broken.
    """
    w, h, stride = 854, 480, 2564
    row = w * 3
    # Row values stay under 200 so they can never collide with the 0xEE
    # padding sentinel -- a fixture whose data can equal its own marker
    # cannot tell the two apart, and this one could at row 238.
    padded = b"".join(bytes([r % 200]) * row + b"\xEE\xEE" for r in range(h))
    assert len(padded) == stride * h == 1_230_720

    out = unpad_rows(padded, w, h, stride)

    assert len(out) == row * h
    assert b"\xEE" not in out, "padding survived into the pixels"
    assert [out[r * row] for r in range(h)] == [r % 200 for r in range(h)]


# ── a running session that stopped delivering ─────────────────────────


def test_a_frozen_stream_is_reported_once(caplog, monkeypatch) -> None:
    """The wlroots shape: one frame arrives, then the SAME one forever.

    ``latest is None`` never fires here -- the adapter is handed a frame every
    time, so "no frame yet" cannot see this.  What is wrong is that it is the
    same frame, and the only symptom the user has is a frozen panel.
    """
    # Availability is set, not inherited: it follows whether the HOST has the
    # bindings, and on CI's clean venv (no dbus) this test read the fallback
    # path and saw no stall at all.
    monkeypatch.setattr(pw, "PIPEWIRE_AVAILABLE", True)
    frame = (4, 4, b"\x09" * 48)
    cap = _capture(_Session(running=True, frame=frame), _Fallback())

    clock = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])

    cap.grab_region(0, 0, 2, 2)                      # first sight, starts the clock
    clock[0] += PipeWireScreenCapture.STALL_AFTER + 0.1

    with caplog.at_level(logging.WARNING,
                         logger="trcc.adapters.screencast.pipewire"):
        cap.grab_region(0, 0, 2, 2)
        cap.grab_region(0, 0, 2, 2)
        cap.grab_region(0, 0, 2, 2)

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING
                and "no new frame" in r.getMessage()]
    assert len(warnings) == 1, (
        f"a frozen stream must be reported exactly ONCE, got {len(warnings)}"
    )


def test_a_stream_that_keeps_moving_is_never_called_stalled(
    caplog, monkeypatch,
) -> None:
    """A healthy 30 fps stream must not trip the watchdog.

    GNOME measured 314 buffers in 10s, so a new frame object arrives far
    inside STALL_AFTER; the clock has to restart on every one.
    """
    session = _Session(running=True, frame=(4, 4, b"\x00" * 48))
    cap = _capture(session, _Fallback())

    clock = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])

    with caplog.at_level(logging.WARNING,
                         logger="trcc.adapters.screencast.pipewire"):
        for i in range(30):
            session._frame = (4, 4, bytes([i]) * 48)   # a NEW object each tick
            clock[0] += PipeWireScreenCapture.STALL_AFTER * 0.9
            cap.grab_region(0, 0, 2, 2)

    assert not [r for r in caplog.records if r.levelno == logging.WARNING], (
        "a moving stream was reported as stalled"
    )


# ── The missing GStreamer plugin (issue #280) ────────────────────────────────


class _NoPipewireSrc:
    """A GStreamer whose plugin set is missing ``pipewiresrc``."""

    class ElementFactory:
        @staticmethod
        def find(name: str) -> Any:
            return None if name == "pipewiresrc" else object()

    @staticmethod
    def parse_launch(_desc: str) -> Any:        # pragma: no cover - must not run
        raise AssertionError(
            "the pipeline was built despite pipewiresrc being absent",
        )


def test_a_missing_pipewiresrc_names_the_package_to_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The element lives in its own package on every distro, and the
    failure it produces otherwise looks like anything but a missing plugin:
    the portal grant SUCCEEDS, the desktop shows "screen is being shared",
    then the session drops and the panel stays black (issue #280).

    ``parse_launch`` would say ``no element "pipewiresrc"`` — the element,
    never the package — so the guard has to carry the install hint itself.
    """
    monkeypatch.setattr(pw, "Gst", _NoPipewireSrc, raising=False)
    cast = pw.PipeWireScreenCast()

    with pytest.raises(RuntimeError) as excinfo:
        cast._start_gstreamer()

    message = str(excinfo.value)
    for package in ("pipewire-gstreamer",          # Fedora
                    "gstreamer1.0-pipewire",       # Debian / Ubuntu
                    "gst-plugin-pipewire"):        # Arch
        assert package in message, (
            f"the error must name {package} — a user cannot act on "
            f"'no element pipewiresrc' alone. Got: {message}"
        )
    assert "gst-inspect-1.0 pipewiresrc" in message, (
        "the error must say how to confirm the fix worked"
    )
