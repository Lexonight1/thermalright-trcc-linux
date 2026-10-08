"""Qt-backed :class:`ScreenCapture` adapters: Qt's own grab, and external tools.

Two links, composed per display session by :func:`build_screen_capture`
(``adapters/screencast/__init__.py``), which is the ONE place a chain is
chosen:

* :class:`QtNativeCapture` -- ``QScreen.grabWindow`` on the primary screen.
  The whole answer on Windows and macOS and the fast path on X11.  Never
  composed for a Wayland session: there the compositor hands a client only
  its own surfaces, so the grab is blank, and through Xwayland it sees X
  clients alone.  Hands over to the next link when it cannot see a screen.
* :class:`ToolCapture` -- a list of :class:`ToolSpec`, region tools first
  (``grim``, ``scrot``, ``maim``, ``import``), then whole-screen tools
  cropped (``spectacle``, ``gnome-screenshot``).  WHICH tools is the
  composer's decision from the session; HOW each is driven is here.

Until 2026-09-18 this was one class with one fixed list tried on every
desktop: X11 grabbers under Wayland, one of which (``import``) rang the X
bell on every call -- an "error noise" Plasma played two to three times a
second; the wlroots tool under KWin, failing every tick; the Plasma tool
under GNOME.  Measured on Plasma 6, with the audio server watching.

Every successful path returns a :class:`RawFrame` with packed RGB24 bytes,
ready for :meth:`Renderer.from_raw_rgb24`; every failure raises
:class:`OSError` naming what was tried, never a blank frame.

**The region picker's frozen backdrop is still a separate chain.**
``ui/screen_overlay.py::grab_full_screen`` grabs the FULL screen for both
pickers and the eyedropper with its own tool list, and returns a null
QPixmap where this port raises.  Not consolidated here.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QRect
from PySide6.QtGui import QGuiApplication, QImage, QPixmap
from PySide6.QtWidgets import QApplication

from ...core._frames import unpad_rows
from ...core.logs import per_frame, recurring_warning
from ...core.models import RawFrame
from ...core.ports import ScreenCapture

log = logging.getLogger(__name__)
# Everything a grab says on EVERY frame (~16/s while casting).  On the plain
# logger it reached the file per frame -- the noise that rotates away the
# one-shot lines a report is read for.
frame_log = per_frame(__name__)


_EXTERNAL_TIMEOUT_S = 2
# How long to wait for an external tool's output file to fill after it exits.
_OUTPUT_WAIT_POLLS = 20
_OUTPUT_WAIT_INTERVAL_S = 0.025


# ── the tools ──────────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class ToolSpec:
    """One external screenshot program and how to ask it for a picture.

    ``argv`` is formatted with ``x``, ``y``, ``w``, ``h`` and ``out``.  A
    ``region`` tool writes exactly the rectangle; a whole-screen tool is
    cropped afterwards, because it has no scriptable region flag.
    """
    name: str
    argv: tuple[str, ...]
    region: bool

    def command(self, x: int, y: int, w: int, h: int, out: str) -> list[str]:
        frame_log.debug("ToolSpec.command: %s (%d,%d) %dx%d",
                        self.name, x, y, w, h)
        return [part.format(x=x, y=y, w=w, h=h, out=out) for part in self.argv]


#: wlroots' own protocol -- sway, Hyprland, river, wayfire, labwc.
GRIM = ToolSpec("grim", ("grim", "-g", "{x},{y} {w}x{h}", "{out}"), region=True)
SCROT = ToolSpec("scrot", ("scrot", "-a", "{x},{y},{w},{h}", "{out}"), region=True)
MAIM = ToolSpec("maim", ("maim", "-g", "{w}x{h}+{x}+{y}", "{out}"), region=True)
#: ImageMagick.  Least specialised of the X11 grabbers, and the one a plain
#: X11 desktop most often has -- this dev box had no grim, scrot or maim.
#: ``-silent``: without it ``import`` rings the X bell on every call, and
#: Plasma plays that bell through KWin.  Measured on Plasma 6 with the audio
#: server watching: one playback per call without the flag, none with it.
IMPORT = ToolSpec(
    "import",
    ("import", "-silent", "-window", "root", "-crop", "{w}x{h}+{x}+{y}",
     "+repage", "{out}"),
    region=True,
)
#: Plasma's tool, trusted by KWin through its desktop file; a third-party
#: process asking KWin the same thing is refused.  Flags verified on Plasma
#: (PR #271 and this box): background, no notification, output file.
SPECTACLE = ToolSpec(
    "spectacle", ("spectacle", "-b", "-n", "-o", "{out}"), region=False)
#: GNOME's tool; ``-a`` is an interactive picker, so whole screen and crop.
GNOME_SCREENSHOT = ToolSpec(
    "gnome-screenshot", ("gnome-screenshot", "-f", "{out}"), region=False)

#: What an X11 desktop has when Qt's own grab is blocked.  The region
#: grabbers first, then the two desktop tools cropped -- ``spectacle`` and
#: ``gnome-screenshot`` capture X11 perfectly well, and the chain that
#: preceded this one reached them on EVERY desktop.  Narrowing X11 to the
#: three region grabbers took capture away from a GNOME-on-X11 or
#: Plasma-on-X11 box that has its own desktop tool and none of the three.
#: The Wayland narrowing is different and stays: there a tool works only on
#: the compositor that trusts it.
X11_TOOLS: tuple[ToolSpec, ...] = (SCROT, MAIM, IMPORT, SPECTACLE,
                                   GNOME_SCREENSHOT)
#: Every tool that can capture a Wayland desktop, each borrowing its own
#: compositor's trust.  The composer narrows this to the one the session's
#: desktop actually has.
WAYLAND_TOOLS: tuple[ToolSpec, ...] = (GRIM, GNOME_SCREENSHOT, SPECTACLE)


def _check_region(width: int, height: int) -> None:
    if width <= 0 or height <= 0:
        log.error("screen capture: invalid region size %dx%d", width, height)
        raise OSError(
            f"Invalid region size {width}x{height} — both must be > 0")


# ── link 1: Qt's own grab ──────────────────────────────────────────────────

class QtNativeCapture(ScreenCapture):
    """``QScreen.grabWindow`` on the primary screen, then hand over.

    ``then`` is the next link when Qt cannot see a screen.  ``None`` means
    this link is the whole chain -- Windows and macOS, where the OS's own
    windowing is all there is -- and a blank grab is the error.
    """

    def __init__(self, then: ScreenCapture | None = None) -> None:
        log.info("QtNativeCapture: then=%s",
                 type(then).__name__ if then is not None else None)
        self._then = then
        # Whether the last grab went to ``then`` -- so the handover is said
        # once when it starts and once when Qt works again, not per frame.
        self._handed_over = False

    def grab_region(
        self, x: int, y: int, width: int, height: int,
    ) -> RawFrame:
        _check_region(width, height)
        frame_log.debug("QtNativeCapture: grab region (%d,%d) %dx%d",
                        x, y, width, height)
        pix = self._qt_grab(x, y, width, height)
        if pix is not None and not pix.isNull() and pix.width() > 1:
            if self._handed_over:
                log.info("QtNativeCapture: Qt native grab works again")
                self._handed_over = False
            return _pixmap_to_raw_frame(pix, width, height)
        if self._then is None:
            log.error("QtNativeCapture: Qt returned a blank pixmap for "
                      "(%d,%d) %dx%d and this chain has no next link",
                      x, y, width, height)
            raise OSError(
                "Screen capture failed — Qt returned a blank pixmap and "
                "this platform has no other capture path")
        # Once per change, not per frame: when Qt cannot grab (offscreen,
        # Wayland) it cannot on any frame, and this INFO came every tick.
        sink = frame_log if self._handed_over else log
        sink.info("QtNativeCapture: Qt native grab unusable (blank) — "
                  "handing over to %s", type(self._then).__name__)
        self._handed_over = True
        return self._then.grab_region(x, y, width, height)

    def stop(self) -> None:
        """Qt holds nothing; the next link may."""
        log.debug("QtNativeCapture.stop: then=%s",
                  type(self._then).__name__ if self._then else None)
        if self._then is not None:
            self._then.stop()

    @staticmethod
    def _qt_can_grab() -> bool:
        """Whether Qt can see a real screen right now.

        An OFFSCREEN Qt has no window system to read, and ``grabWindow``
        does not fail on it -- it returns a correctly-sized, non-null,
        essentially black pixmap.  Every "is this blank?" test in this file
        is a SIZE test (``isNull``, ``width() <= 1``), and that sails
        straight through them, so the black frame was accepted as a
        capture.  MEASURED against ImageMagick ground truth on the same
        rectangle: offscreen scores a mean absolute error of 68.9, native
        xcb scores 0.0 -- pixel-identical.

        Reachable from every non-GUI face: ``_ensure_qt_app`` builds the
        headless app on the offscreen platform (``-platform offscreen``)
        without asking whether a display exists.
        """
        app = QGuiApplication.instance()
        if not isinstance(app, QGuiApplication):
            frame_log.debug("_qt_can_grab: no QGuiApplication (got %r)", type(app))
            return False
        if app.platformName() == "offscreen":
            frame_log.debug("_qt_can_grab: platform is offscreen — Qt cannot "
                            "see a screen, leaving it to the next link")
            return False
        return True

    def _qt_grab(
        self, x: int, y: int, w: int, h: int,
    ) -> QPixmap | None:
        frame_log.debug("_qt_grab: x=%s y=%s", x, y)
        if not self._qt_can_grab():
            return None
        screen = QApplication.primaryScreen()
        if screen is None:
            return None
        return screen.grabWindow(0, x, y, w, h)  # type: ignore[arg-type]


# ── link 2: external tools ─────────────────────────────────────────────────

class ToolCapture(ScreenCapture):
    """External screenshot programs: region tools first, then whole-screen
    tools cropped to the region.

    Two stages because the tools split that way, not because the code wants
    to: ``grim`` and ``scrot`` take a geometry, while ``spectacle`` and
    ``gnome-screenshot`` have no scriptable region flag and can only be
    cropped after the fact.
    """

    def __init__(self, tools: tuple[ToolSpec, ...]) -> None:
        log.info("ToolCapture: %s", [tool.name for tool in tools])
        self._tools = tools
        # The last full-grab crop announced: said again only when it changes.
        self._last_crop: tuple[int, ...] | None = None

    @property
    def tools(self) -> tuple[ToolSpec, ...]:
        log.debug("ToolCapture.tools")
        return self._tools

    def grab_region(
        self, x: int, y: int, width: int, height: int,
    ) -> RawFrame:
        _check_region(width, height)
        frame_log.debug("ToolCapture: grab region (%d,%d) %dx%d",
                        x, y, width, height)
        pix: QPixmap | None = None
        # A directory, and a name inside it that does not exist yet: scrot
        # refuses to overwrite, so a pre-created mkstemp file made it write
        # ``<name>_000.png`` beside it instead — one PNG left in /tmp (tmpfs,
        # RAM) per screencast tick.  The directory takes whatever a tool writes.
        with tempfile.TemporaryDirectory(prefix="trcc-grab-") as scratch:
            tmp_path = str(Path(scratch) / "grab.png")
            pix = self._run(tuple(t for t in self._tools if t.region),
                            x, y, width, height, tmp_path)
            if pix is None:
                shot = self._run(tuple(t for t in self._tools if not t.region),
                                 x, y, width, height, tmp_path)
                if shot is not None:
                    crop = (shot.width(), shot.height(), x, y, width, height)
                    sink = frame_log if crop == self._last_crop else log
                    self._last_crop = crop
                    sink.info("ToolCapture: cropping %dx%d full grab to "
                              "(%d,%d) %dx%d", *crop)
                    pix = shot.copy(QRect(x, y, width, height))
        if pix is None or pix.isNull():
            names = ", ".join(tool.name for tool in self._tools) or "no tool"
            log.error("ToolCapture: all capture paths failed for (%d,%d) "
                      "%dx%d — tried %s", x, y, width, height, names)
            raise OSError(
                f"Screen capture failed — none of {names} produced output; "
                "install one of them, or check that this session lets it "
                "capture")
        return _pixmap_to_raw_frame(pix, width, height)

    def _run(
        self, tools: tuple[ToolSpec, ...],
        x: int, y: int, w: int, h: int, tmp_path: str,
    ) -> QPixmap | None:
        """First tool whose binary exists and exits 0 with usable output wins.

        One loop for both stages -- a second copy is how the region chain
        and the full chain drift apart.
        """
        for tool in tools:
            if shutil.which(tool.name) is None:
                frame_log.debug("ToolCapture: %s not on PATH; skipping",
                                tool.name)
                continue
            cmd = tool.command(x, y, w, h, tmp_path)
            frame_log.debug("ToolCapture: trying %s", " ".join(cmd))
            try:
                result = subprocess.run(
                    cmd, capture_output=True,
                    timeout=_EXTERNAL_TIMEOUT_S, check=False,
                )
            except subprocess.TimeoutExpired:
                recurring_warning(log, "ToolCapture: %s timed out", tool.name)
                continue
            if result.returncode != 0:
                recurring_warning(log, "ToolCapture: %s exited %d (stderr=%r)",
                                  tool.name, result.returncode,
                                  result.stderr[:200].decode("utf-8", "replace"))
                continue
            # ``spectacle`` returns before its file is flushed (measured on
            # Plasma, PR #271) -- wait for bytes.  The file is not pre-created
            # (scrot would refuse it), so "not there yet" is part of waiting.
            # A tool that never writes falls through to the null-pixmap
            # warning below.
            out = Path(tmp_path)
            for _ in range(_OUTPUT_WAIT_POLLS):
                if out.is_file() and out.stat().st_size > 0:
                    break
                time.sleep(_OUTPUT_WAIT_INTERVAL_S)
            pix = QPixmap(tmp_path)
            if not pix.isNull():
                frame_log.debug("ToolCapture: %s captured %dx%d",
                                tool.name, pix.width(), pix.height())
                return pix
            recurring_warning(log, "ToolCapture: %s output was null QPixmap",
                              tool.name)
        return None


def _pixmap_to_raw_frame(
    pix: QPixmap, target_w: int, target_h: int,
) -> RawFrame:
    """Convert a :class:`QPixmap` to RGB24-packed :class:`RawFrame`.

    Resize to exactly ``target_w × target_h`` so callers can rely on
    the dimensions — external tools sometimes round geometry to even
    pixels.
    """
    frame_log.debug("_pixmap_to_raw_frame: pix=%s target_w=%s", pix, target_w)
    image = pix.toImage().convertToFormat(QImage.Format.Format_RGB888)
    if image.width() != target_w or image.height() != target_h:
        image = image.scaled(target_w, target_h)
    # ``constBits()`` is memoryview-like and row-padded to a multiple of 4;
    # copy to immutable bytes for the hand-off across thread/UI boundaries,
    # then strip the pad so consumers see exactly width*height*3.
    data = unpad_rows(bytes(image.constBits()), target_w, target_h,
                      image.bytesPerLine())
    return RawFrame(data=data, width=target_w, height=target_h)
