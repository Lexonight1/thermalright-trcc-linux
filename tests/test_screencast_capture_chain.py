"""The desktop-capture fallback chain — ONE chain, and every rung reachable.

Until 2026-09-14 this chain existed **three times**: here in the adapter, in
``ui/gui/screen_capture.py`` (the gui's per-tick grab) and in
``ui/screen_overlay.py`` (the region picker's frozen backdrop).  They had
diverged on the rung that matters — only the UI copies knew about
``gnome-screenshot``, which is the ONLY one of the three tools that works on
GNOME and KDE Wayland (``grim`` is wlroots-only, ``scrot`` is X11).

The user-visible result: on GNOME Wayland a user could freeze the screen in the
region picker, start a screencast, and get a black panel from the CLI, the API
or qtgui — while the gui beside them worked.  Same desktop, same Command,
different answer per face.

These tests pin the rungs by **forcing each one to be the only survivor**, so a
rung that stops being reachable fails here rather than on someone's desktop.
Each also checks the CROP, because a full-screen rung that returns the whole
screen instead of the asked-for region is the failure mode a "did it return
something" assertion cannot see.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from trcc.adapters.screencast import build_screen_capture
from trcc.adapters.screencast.qt import (
    WAYLAND_TOOLS,
    X11_TOOLS,
    QtNativeCapture,
    ToolCapture,
)
from trcc.core.models import DisplayServer, DisplaySession, RawFrame
from trcc.core.ports import ScreenCapture

pytest.importorskip("PySide6")

#: A region inset from the origin, so a missing crop shows up as wrong pixels
#: rather than wrong size alone.
REGION = (40, 30, 64, 48)
FULL = (320, 240)
INK = (0, 200, 40)
#: Everything OUTSIDE the region — a crop must not return any of it.
BACKDROP = (200, 0, 0)


@pytest.fixture
def cap() -> ToolCapture:
    """Every tool in one chain, so each rung can be forced to be the only
    survivor.  Which tools a real session gets is the composer's decision,
    tested separately below."""
    return ToolCapture(X11_TOOLS + WAYLAND_TOOLS)


def _png(path: Path, w: int, h: int, rgb: tuple[int, int, int],
         marker: tuple[int, int, int, int] | None = None) -> None:
    """A PNG filled with *rgb*, optionally with INK painted at *marker*.

    The marker is what makes a crop assertion meaningful.  Asserting only the
    returned SIZE cannot see a missing crop: ``_pixmap_to_raw_frame`` resizes
    whatever it is given to the requested dimensions, so an uncropped
    full-screen grab comes back at exactly the right size holding the whole
    desktop squashed into it.  Measured — removing the ``.copy(QRect(...))``
    left all eight tests green until this marker was added.
    """
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QColor, QImage, QPainter
    img = QImage(w, h, QImage.Format.Format_RGB888)
    img.fill(QColor(*rgb))
    if marker is not None:
        painter = QPainter(img)
        painter.fillRect(QRect(*marker), QColor(*INK))
        painter.end()
    img.save(str(path), "PNG")


def _uniform(frame: Any, rgb: tuple[int, int, int]) -> bool:
    """True when every pixel of *frame* is *rgb* (RGB24, no row padding)."""
    px = frame.data
    return all(tuple(px[i:i + 3]) == rgb for i in range(0, len(px), 3))


def _only(tool: str, monkeypatch: pytest.MonkeyPatch,
          size: tuple[int, int]) -> list[list[str]]:
    """Make *tool* the only binary on PATH and have it write a *size* PNG."""
    calls: list[list[str]] = []

    def fake_which(name: str) -> str | None:
        return f"/usr/bin/{name}" if name == tool else None

    def fake_run(cmd: list[str], **kw: Any) -> Any:
        calls.append(cmd)
        if size == FULL:
            # A whole-desktop grab: BACKDROP everywhere, INK only inside the
            # requested region, so a correct crop returns pure INK and a
            # missing crop returns a mix.
            _png(Path(cmd[-1]), *size, BACKDROP, marker=REGION)
        else:
            _png(Path(cmd[-1]), *size, INK)
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr("trcc.adapters.screencast.qt.shutil.which", fake_which)
    monkeypatch.setattr("trcc.adapters.screencast.qt.subprocess.run", fake_run)
    return calls


# ── the region rungs ───────────────────────────────────────────────────────

@pytest.mark.parametrize("tool", ["grim", "scrot"])
def test_region_tools_are_asked_for_the_region(
    cap: ToolCapture, monkeypatch: pytest.MonkeyPatch, tool: str,
) -> None:
    """``grim -g`` / ``scrot -a`` take a geometry, so no crop is needed."""
    calls = _only(tool, monkeypatch, (REGION[2], REGION[3]))
    frame = cap.grab_region(*REGION)

    assert len(calls) == 1 and calls[0][0] == tool
    assert " ".join(calls[0]).count(str(REGION[2])) >= 1, (
        f"{tool} was not given the region geometry: {calls[0]}")
    assert (frame.width, frame.height) == (REGION[2], REGION[3])


# ── the rung that was missing ──────────────────────────────────────────────

def test_import_is_told_to_hold_its_bell(
    cap: ToolCapture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ImageMagick's ``import`` rings the X bell on every capture unless told
    ``-silent``.  Plasma plays that bell through KWin, so this rung made an
    "error noise" two to three times a second for as long as the fallback
    ran.  Measured on Plasma 6 with the audio server watching: one playback
    per call without the flag, none with it.
    """
    calls = _only("import", monkeypatch, (REGION[2], REGION[3]))
    cap.grab_region(*REGION)

    assert len(calls) == 1 and calls[0][0] == "import"
    assert "-silent" in calls[0], f"import will ring the bell: {calls[0]}"


def test_gnome_screenshot_grabs_full_and_is_cropped(
    cap: ToolCapture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The GNOME / KDE Wayland rung — full grab, then crop.

    ``gnome-screenshot`` has no scriptable region flag (``-a`` is an
    interactive picker), so it MUST be cropped afterwards.  Returning the
    whole screen would look like a working capture and put the wrong picture
    on the panel.
    """
    calls = _only("gnome-screenshot", monkeypatch, FULL)
    frame = cap.grab_region(*REGION)

    assert len(calls) == 1 and calls[0][0] == "gnome-screenshot"
    assert "-f" in calls[0], "gnome-screenshot needs -f <file>"
    assert (frame.width, frame.height) == (REGION[2], REGION[3])
    assert len(frame.data) == REGION[2] * REGION[3] * 3
    # CONTENT, not size — the assertion that can actually see a missing crop.
    assert _uniform(frame, INK), (
        "full-screen grab was not cropped to the requested region — the "
        "frame carries pixels from outside it")


def test_spectacle_grabs_full_and_is_cropped(
    cap: ToolCapture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """KDE Plasma Wayland's only rung: ``spectacle -b -n -o <file>`` grabs
    the whole screen, then the port crops it (#271)."""
    calls = _only("spectacle", monkeypatch, FULL)
    frame = cap.grab_region(*REGION)

    assert calls == [["spectacle", "-b", "-n", "-o", calls[0][-1]]]
    assert (frame.width, frame.height) == (REGION[2], REGION[3])
    assert len(frame.data) == REGION[2] * REGION[3] * 3
    assert _uniform(frame, INK)


def test_a_tool_that_exits_before_its_file_lands_is_waited_for(
    cap: ToolCapture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``spectacle`` returns 0 with the file still empty; the chain must
    wait for bytes instead of reading an empty PNG and moving on (#271)."""
    pending: list[Path] = []

    def fake_which(name: str) -> str | None:
        return f"/usr/bin/{name}" if name == "spectacle" else None

    def fake_run(cmd: list[str], **kw: Any) -> Any:
        pending.append(Path(cmd[-1]))            # file lands later
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    def late_write(seconds: float) -> None:      # the first wait poll
        _png(pending.pop(), *FULL, BACKDROP, marker=REGION)

    monkeypatch.setattr("trcc.adapters.screencast.qt.shutil.which", fake_which)
    monkeypatch.setattr("trcc.adapters.screencast.qt.subprocess.run", fake_run)
    monkeypatch.setattr("trcc.adapters.screencast.qt.time.sleep", late_write)

    frame = cap.grab_region(*REGION)

    assert _uniform(frame, INK)


def test_region_tools_are_preferred_over_the_full_grab(
    cap: ToolCapture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Order matters: cropping a whole screen is the expensive last resort."""
    seen: list[str] = []

    def fake_which(name: str) -> str | None:
        return f"/usr/bin/{name}"          # everything is installed

    def fake_run(cmd: list[str], **kw: Any) -> Any:
        seen.append(cmd[0])
        _png(Path(cmd[-1]), REGION[2], REGION[3], INK)
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr("trcc.adapters.screencast.qt.shutil.which", fake_which)
    monkeypatch.setattr("trcc.adapters.screencast.qt.subprocess.run", fake_run)

    cap.grab_region(*REGION)

    region_tools = {tool.name for tool in cap.tools if tool.region}
    assert len(seen) == 1 and seen[0] in region_tools, (
        f"expected one region tool first, got {seen}")


# ── nothing available ──────────────────────────────────────────────────────

def test_every_backend_failing_raises_rather_than_returning_black(
    cap: ToolCapture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A black frame is indistinguishable from a legitimately dark desktop.

    The port's contract says raise, so the caller can say WHY it stopped
    instead of silently pushing black to the panel.
    """
    monkeypatch.setattr("trcc.adapters.screencast.qt.shutil.which",
                        lambda name: None)
    monkeypatch.setattr(
        "trcc.adapters.screencast.qt.QApplication.primaryScreen",
        staticmethod(lambda: None))

    with pytest.raises(OSError, match="Screen capture failed"):
        cap.grab_region(*REGION)


def test_invalid_region_is_refused(cap: ToolCapture) -> None:
    with pytest.raises(OSError, match="Invalid region size"):
        cap.grab_region(0, 0, 0, 100)


# ── the single chooser ─────────────────────────────────────────────────────

def test_one_place_chooses_the_backend() -> None:
    """``build_screen_capture`` composes the chain from the display session.

    One decision for every face -- the CLI, the REST route, qtgui and the gui
    window all reach it through ``Platform.screen_capture()`` -- so a backend
    lands for all of them at once.  On Wayland that is the portal with the
    desktop's own tool behind it; Qt's grab is never composed there, because
    the compositor hands a client only its own surfaces.
    """
    from trcc.adapters.screencast.pipewire import PipeWireScreenCapture

    made = build_screen_capture(
        DisplaySession(DisplayServer.WAYLAND, ("kde",)))

    assert isinstance(made, ScreenCapture)
    assert isinstance(made, PipeWireScreenCapture)
    assert isinstance(made._fallback, ToolCapture)


def test_the_os_delegates_to_that_chooser(monkeypatch: pytest.MonkeyPatch) -> None:
    """And hands it its OWN session and config dir.

    The session is what the chooser composes from; the config dir is where
    the portal token is kept.  Without the latter the backend has nowhere to
    store what the portal returns, so the consent dialog reappears on every
    launch -- a silent downgrade, since capture still works.
    """
    from trcc.adapters.system.linux import LinuxOS

    sentinel = ToolCapture(())
    seen: list[object] = []

    def chooser(session, config_dir=None):
        seen.append((session.server, config_dir))
        return sentinel

    monkeypatch.setattr("trcc.adapters.screencast.build_screen_capture",
                        chooser)
    os_ = LinuxOS()

    assert os_._build_screen_capture() is sentinel
    assert seen == [(os_.display_session().server,
                     os_.paths().config_dir())], (
        f"the OS did not forward its session and config dir: {seen}"
    )


# ── the composer: which links, per session ────────────────────────────────

def _tools_of(chain: ScreenCapture) -> list[str]:
    """The tool names a composed chain can spawn, wherever they sit."""
    from trcc.adapters.screencast.pipewire import PipeWireScreenCapture
    match chain:
        case PipeWireScreenCapture():
            return _tools_of(chain._fallback)
        case QtNativeCapture():
            return [] if chain._then is None else _tools_of(chain._then)
        case ToolCapture():
            return [tool.name for tool in chain.tools]
    return []


#: The tools that ONLY ever work on X11.  ``spectacle`` and
#: ``gnome-screenshot`` are in both chains on purpose -- they capture X11
#: perfectly well, and on Wayland each works on the compositor that trusts it.
_X11_ONLY = {"scrot", "maim", "import"}


@pytest.mark.parametrize(("desktops", "expected"), [
    (("kde",), ["spectacle"]),
    (("ubuntu", "gnome"), ["gnome-screenshot"]),
    (("sway",), ["grim"]),
    (("hyprland",), ["grim"]),
    (("cosmic",), ["grim", "gnome-screenshot", "spectacle"]),
    ((), ["grim", "gnome-screenshot", "spectacle"]),
], ids=["plasma", "ubuntu-gnome", "sway", "hyprland", "unknown", "unnamed"])
def test_a_wayland_session_gets_its_own_desktops_tool_and_no_x11_grabber(
    desktops: tuple[str, ...], expected: list[str],
) -> None:
    """On Wayland each desktop lends ONE tool, and the X11 grabbers see only
    Xwayland -- one of them ringing the X bell on every call, which Plasma
    played as an error noise two to three times a second.  An unknown desktop
    gets every Wayland tool, because we cannot know which it trusts.
    """
    from trcc.adapters.screencast.pipewire import PipeWireScreenCapture

    made = build_screen_capture(DisplaySession(DisplayServer.WAYLAND, desktops))

    assert isinstance(made, PipeWireScreenCapture), "Wayland captures through the portal"
    assert isinstance(made._fallback, ToolCapture), "Qt's grab is blank on Wayland and is not composed"
    assert _tools_of(made) == expected
    assert not _X11_ONLY.intersection(_tools_of(made)), (
        "an X11-only grabber was composed for a Wayland session; it sees "
        "Xwayland alone, and one of them rings the X bell every call")


@pytest.mark.parametrize("desktop", ["xfce", "gnome", "kde"])
def test_an_x11_session_gets_qt_first_then_every_tool_that_works_on_x11(
    desktop: str,
) -> None:
    """X11 is NOT narrowed by desktop, and that is the point.

    ``spectacle`` and ``gnome-screenshot`` capture an X11 screen whatever
    desktop is running, and the chain before this one reached them from
    every desktop.  A first draft of the composer gave X11 the three region
    grabbers alone -- which takes capture away from a GNOME-on-X11 or
    Plasma-on-X11 box that has its desktop's tool and none of the three.
    Only WAYLAND narrows, because there a tool works solely on the
    compositor that trusts it.
    """
    from trcc.adapters.screencast.pipewire import PipeWireScreenCapture

    made = build_screen_capture(DisplaySession(DisplayServer.X11, (desktop,)))
    tools = _tools_of(made)

    assert isinstance(made, QtNativeCapture)
    assert not isinstance(made, PipeWireScreenCapture)
    assert {"spectacle", "gnome-screenshot"} <= set(tools), tools
    assert set(tools) >= _X11_ONLY, tools
    assert "grim" not in tools, "grim is wlroots-only and cannot capture X11"


def test_stop_travels_down_the_chain() -> None:
    """``stop`` is on the port so the gui can call it on whatever it was
    handed; the native link holds nothing itself and passes it on to the
    link that might."""
    stopped: list[str] = []

    class _Held(ScreenCapture):
        def grab_region(self, x, y, width, height):
            raise OSError("not asked here")

        def stop(self) -> None:
            stopped.append("held")

    QtNativeCapture(then=_Held()).stop()
    QtNativeCapture().stop()                  # nothing to forward, no error

    assert stopped == ["held"]


def test_a_stateless_source_inherits_stop_from_the_port() -> None:
    """``stop`` is concrete on the port, so a source that holds nothing --
    every fake in this suite, Qt's grab, the tools -- need not write one and
    the gui can call it on whatever it was handed.  Measured 2026-09-18:
    with the port's ``stop`` removed, no test in the tree noticed.
    """
    class _Bare(ScreenCapture):
        def grab_region(self, x, y, width, height):
            raise OSError("never asked")

    _Bare().stop()                            # inherited, and a no-op


def test_native_windowing_gets_qt_alone() -> None:
    """Windows and macOS: no portal, no tools, Qt's grab is the whole answer."""
    made = build_screen_capture(DisplaySession(DisplayServer.NATIVE))

    assert isinstance(made, QtNativeCapture)
    assert made._then is None


def test_a_headless_process_is_composed_like_x11() -> None:
    """No session variables at all: the X11 grabbers may still find a display
    of their own, and Qt's guard declines on its own when it cannot."""
    made = build_screen_capture(DisplaySession(DisplayServer.HEADLESS))

    assert isinstance(made, QtNativeCapture)
    assert _tools_of(made) == [tool.name for tool in X11_TOOLS]


@pytest.mark.parametrize(("desktops", "name"), [
    (("kde",), "portal-restore-token.kde"),
    (("ubuntu", "gnome"), "portal-restore-token.ubuntu-gnome"),
    ((), "portal-restore-token.unknown"),
], ids=["plasma", "ubuntu-gnome", "unnamed"])
def test_the_portal_token_is_kept_per_desktop(
    tmp_path: Path, desktops: tuple[str, ...], name: str,
) -> None:
    """A restore token is meaningful only to the portal backend that issued
    it, and the backend is chosen per desktop.  One file for all of them
    meant switching between GNOME and KDE asked again every time, each grant
    overwriting the other's -- measured on 2026-09-18: the GNOME token
    replayed to KDE and refused, then KDE's overwriting it.
    """
    made = build_screen_capture(
        DisplaySession(DisplayServer.WAYLAND, desktops), config_dir=tmp_path)

    assert made._token_path == tmp_path / name


# ── the blank-grab trap ───────────────────────────────────────────────


def test_an_offscreen_qt_never_supplies_a_capture(monkeypatch) -> None:
    """An offscreen Qt cannot see a screen, so it must not be asked.

    ``grabWindow`` does NOT fail on the offscreen platform — it returns a
    correctly-sized, non-null, essentially black pixmap.  Every blank test in
    this adapter is a SIZE test (``isNull`` / ``width() <= 1``), so that black
    rectangle passed as a capture and the external tools were never tried.
    MEASURED against ImageMagick ground truth on the same rectangle: offscreen
    scored a mean absolute error of 68.9, native xcb scored 0.0.

    Reachable from every non-GUI face — ``_ensure_qt_app`` forces
    ``QT_QPA_PLATFORM=offscreen`` for headless rendering without asking
    whether a display exists.
    """
    cap = QtNativeCapture()

    assert cap._qt_can_grab() is False, (
        "the suite runs offscreen, so Qt must decline — if this passes, Qt is "
        "about to hand the wire a black frame"
    )
    assert cap._qt_grab(0, 0, 64, 64) is None


def test_the_native_link_hands_over_when_qt_is_blank() -> None:
    """When Qt cannot see a screen the next link is asked, not a blank sent.

    The old single class reached Qt by TWO routes -- a region grab and a
    full-screen grab cropped after the tools -- and one guard missed the
    second, so it went on serving the same blank pixmap by a different path.
    The native link now has ONE route and one guard, and hands over.
    """
    seen: list[tuple[int, int, int, int]] = []

    class _Next(ScreenCapture):
        def grab_region(self, x, y, width, height):
            seen.append((x, y, width, height))
            return RawFrame(data=bytes(width * height * 3), width=width,
                            height=height)

    frame = QtNativeCapture(then=_Next()).grab_region(*REGION)

    assert seen == [REGION], "the next link was not asked for the region"
    assert (frame.width, frame.height) == (REGION[2], REGION[3])


def test_a_blank_source_raises_instead_of_sending_black() -> None:
    """With nothing able to capture, the answer is an error, not a picture.

    Returning black is worse than failing: the caller sends it to the panel
    and the user sees a dead screencast with no message anywhere.  Qt alone
    is the whole chain on Windows and macOS, so a blank there is the end.
    """
    with pytest.raises(OSError, match="Screen capture failed"):
        QtNativeCapture().grab_region(0, 0, 64, 64)


def test_the_region_tools_cover_plain_x11() -> None:
    """``grim`` is wlroots and ``scrot`` is not everywhere.

    A plain X11 desktop with neither — this dev box — had NO region tool at
    all, so capture failed outright once the blank Qt grab stopped being
    accepted.  ``maim`` and ImageMagick's ``import`` are the common X11
    answers; with ``import`` the headless chain measured a mean absolute
    error of 0.00 against ground truth.  And ``grim`` is NOT an X11 tool.
    """
    names = [tool.name for tool in X11_TOOLS]

    assert {"maim", "import"} <= set(names), names
    assert "grim" not in names, "grim is wlroots-only and does not belong here"
    assert all(tool.region for tool in X11_TOOLS if tool.name in _X11_ONLY), (
        "every X11 region grabber takes a geometry")
    region_first = [t.region for t in X11_TOOLS]
    assert region_first == sorted(region_first, reverse=True), (
        "the region grabbers must come before the whole-screen tools: a "
        f"full grab plus a crop is the expensive last resort — {names}")


# ── one stride loop, three producers ──────────────────────────────────

#: A width whose packed row is NOT a multiple of 4, so Qt must pad it.
#: ``85 * 3 == 255`` → stride 256, one junk byte per row.  At a width where
#: the padding happens to be zero (1920, say) this whole family of bugs is
#: invisible, which is how three copies of the loop survived.
PADDED_W, PADDED_H = 85, 6


def _row_ramp(w: int, h: int) -> Any:
    """A QImage whose row *r* is filled with the solid grey value *r*.

    Row identity is what makes shear visible: if padding is read as pixels,
    every row starts a byte further left than the one above and the first
    pixel of row *r* stops being *r*.  Asserting only the byte COUNT cannot
    see that — the count is right either way once the rows are rebuilt.
    """
    from PySide6.QtGui import QColor, QImage

    img = QImage(w, h, QImage.Format.Format_RGB888)
    for r in range(h):
        for x in range(w):
            img.setPixelColor(x, r, QColor(r, r, r))
    return img


def _assert_tightly_packed(data: bytes, w: int, h: int, who: str) -> None:
    assert len(data) == w * h * 3, f"{who}: {len(data)} bytes, want {w * h * 3}"
    assert [data[r * w * 3] for r in range(h)] == list(range(h)), (
        f"{who}: rows are misaligned — this is the diagonal shear"
    )


def test_qimage_producer_strips_row_padding() -> None:
    """``qimage_to_raw_rgb24`` — the gui screencast tick and the Renderer."""
    from trcc.adapters.render.qt import qimage_to_raw_rgb24

    img = _row_ramp(PADDED_W, PADDED_H)
    assert img.bytesPerLine() != PADDED_W * 3, (
        "pick a width Qt actually pads, or this test proves nothing"
    )

    frame = qimage_to_raw_rgb24(img)

    _assert_tightly_packed(frame.data, PADDED_W, PADDED_H, "qimage_to_raw_rgb24")


def test_pixmap_producer_strips_row_padding() -> None:
    """``_pixmap_to_raw_frame`` — every external region-capture rung."""
    from PySide6.QtGui import QPixmap

    from trcc.adapters.screencast.qt import _pixmap_to_raw_frame

    pix = QPixmap.fromImage(_row_ramp(PADDED_W, PADDED_H))

    frame = _pixmap_to_raw_frame(pix, PADDED_W, PADDED_H)

    _assert_tightly_packed(frame.data, PADDED_W, PADDED_H,
                           "_pixmap_to_raw_frame")


def test_both_qt_producers_agree_with_the_shared_primitive() -> None:
    """The three copies were verified identical before being collapsed.

    This is the guard that keeps them that way: both Qt producers must agree
    byte-for-byte with ``core._frames.unpad_rows`` applied by hand to the same
    buffer.  A future "optimisation" inside either one fails here.
    """
    from PySide6.QtGui import QImage, QPixmap

    from trcc.adapters.render.qt import qimage_to_raw_rgb24
    from trcc.adapters.screencast.qt import _pixmap_to_raw_frame
    from trcc.core._frames import unpad_rows

    img = _row_ramp(PADDED_W, PADDED_H).convertToFormat(
        QImage.Format.Format_RGB888)
    by_hand = unpad_rows(bytes(img.constBits()), PADDED_W, PADDED_H,
                         img.bytesPerLine())

    assert qimage_to_raw_rgb24(img).data == by_hand
    assert _pixmap_to_raw_frame(
        QPixmap.fromImage(img), PADDED_W, PADDED_H).data == by_hand


# ── nothing left behind (class A audit, 2026-09-26) ────────────────────────

def _scrot_like(monkeypatch: pytest.MonkeyPatch, module: str) -> None:
    """Only ``scrot`` on PATH, behaving as scrot does: it will not overwrite an
    existing file and writes ``<name>_000.png`` beside it instead (confirmed
    against the installed scrot)."""
    def fake_which(name: str) -> str | None:
        return "/usr/bin/scrot" if name == "scrot" else None

    def fake_run(cmd: list[str], **kw: Any) -> Any:
        out = Path(cmd[-1])
        if out.exists():
            out = out.with_name(f"{out.stem}_000{out.suffix}")
        _png(out, REGION[2], REGION[3], INK)
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(f"{module}.shutil.which", fake_which)
    monkeypatch.setattr(f"{module}.subprocess.run", fake_run)


@pytest.fixture
def private_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    import tempfile
    private = tmp_path / "systmp"
    private.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(private))
    return private


def test_a_screencast_grab_leaves_nothing_in_tmp(
    cap: ToolCapture, monkeypatch: pytest.MonkeyPatch, private_tmp: Path,
) -> None:
    """A pre-created mkstemp file made scrot write ``_000.png`` beside it: one
    PNG left in /tmp — tmpfs, RAM — on every screencast tick."""
    _scrot_like(monkeypatch, "trcc.adapters.screencast.qt")

    frames = [cap.grab_region(*REGION) for _ in range(3)]

    assert all(_uniform(f, INK) for f in frames)
    assert list(private_tmp.iterdir()) == []


def test_the_picker_grab_leaves_nothing_in_tmp(
    monkeypatch: pytest.MonkeyPatch, private_tmp: Path,
) -> None:
    """The region picker's own full-screen fallback had the same mkstemp."""
    from trcc.ui import screen_overlay

    _scrot_like(monkeypatch, "trcc.ui.screen_overlay")
    monkeypatch.setattr(screen_overlay.QApplication, "primaryScreen",
                        staticmethod(lambda: None))

    pix = screen_overlay.grab_full_screen()

    assert not pix.isNull()
    assert list(private_tmp.iterdir()) == []


# ── a cast writes nothing to the log per frame ─────────────────────────────
#
# A grab runs ~16 times a second.  Every line it wrote on the plain logger
# reached the file each frame -- up to six per frame on the tool rung,
# INFOs among them -- the noise that rotates away the lines a report is
# read for.  Per-frame detail goes to the ``trcc.frame`` family; a change of
# state (handing over, a new crop) is said once.


def _file_records(caplog: pytest.LogCaptureFixture) -> list[str]:
    """What would reach the log file: everything but the per-frame family."""
    return [f"{r.levelname} {r.getMessage()}" for r in caplog.records
            if not r.name.startswith("trcc.frame")]


@pytest.mark.parametrize("tool, size", [
    ("grim", (REGION[2], REGION[3])),       # a region tool
    ("gnome-screenshot", FULL),             # a full grab, cropped
])
def test_a_cast_through_a_tool_writes_nothing_per_frame(
    cap: ToolCapture, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture, tool: str, size: tuple[int, int],
) -> None:
    """Qt cannot grab offscreen, so every frame is handed to the tool rung.

    MUTATION CHECK: log the handover, the crop or the capture on the plain
    logger again and the 20 frames below write 20+ records.
    """
    import logging

    _only(tool, monkeypatch, size)
    chain = QtNativeCapture(then=cap)
    with caplog.at_level(logging.DEBUG):
        chain.grab_region(*REGION)          # the first frame may announce
        caplog.clear()
        for _ in range(20):
            chain.grab_region(*REGION)

    assert _file_records(caplog) == []


def test_a_cast_through_qt_writes_nothing_per_frame(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    """Qt's own grab works (an X11 session): three lines a frame, until now."""
    import logging

    from PySide6.QtGui import QColor, QPixmap

    pix = QPixmap(REGION[2], REGION[3])
    pix.fill(QColor(*INK))
    monkeypatch.setattr(QtNativeCapture, "_qt_grab",
                        lambda self, *region: pix)
    chain = QtNativeCapture()
    with caplog.at_level(logging.DEBUG):
        chain.grab_region(*REGION)
        caplog.clear()
        for _ in range(20):
            chain.grab_region(*REGION)

    assert _file_records(caplog) == []
