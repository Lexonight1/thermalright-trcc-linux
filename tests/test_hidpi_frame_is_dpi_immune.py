"""The wire frame does not change when the desktop is scaled (#220).

``configure_qt_environment`` set ``QT_ENABLE_HIGHDPI_SCALING=0`` from v6.1.7
(2026-02-26) until this file existed, and it never did anything: that is a
**Qt5** variable, this tree has only ever depended on PySide6, and Qt6 makes
high-DPI scaling mandatory -- ``AA_DisableHighDpiScaling`` still exists but has
no effect, and the env var is documented as testing-only and ignored outright
on Wayland and macOS.  Two CHANGELOG entries credit it with fixing HiDPI layout
corruption for reporters, and #220 was investigated for 71 days on the
hypothesis that devicePixelRatio was leaking into the composed wire surface
*despite* it.

Measured, it is not leaking, and the env var was never why.  ``QtRenderer``
composes on ``QImage(w, h)`` with explicit pixel dimensions, whose
``logicalDpiX`` is 96 whatever the screen reports; ``QPixmap`` -- the
devicePixelRatio-aware type -- is used only by ``to_pixmap`` for the GUI
preview and never reaches the wire.  So the frame is DPI-immune BY
CONSTRUCTION, which is a much better guarantee than an env var, but nothing
proved it.  This does.

**Why subprocesses.** Qt resolves scaling once, when the QApplication is
constructed, and the suite shares one.  A test that claims to compare two
device pixel ratios has to pay for two processes, or it is comparing one value
with itself -- which is exactly why ``test_the_scale_factor_actually_differs``
exists: without it, a Qt version that stopped honouring ``QT_SCALE_FACTOR``
would turn this file into two identical runs that agree trivially and pass
forever.

MUTATION CHECK -- make the canvas's DPI follow the screen::

    surface.setDotsPerMeterX(int(3780 * screen.devicePixelRatio()))
    surface.setDotsPerMeterY(int(3780 * screen.devicePixelRatio()))

and the metrics DOUBLE at dpr 2 -- ``[22, 54] -> [43, 108]``,
``[87, 216] -> [175, 432]`` -- which is #220's reported symptom exactly, a
theme drawn twice too big.  Both
``test_text_geometry_is_identical_across_scale_factors`` and
``test_the_canvas_dpi_is_pinned_whatever_the_screen_says`` fail on it.

``setDevicePixelRatio`` is **not** the mutation to reach for, and that is worth
recording: it was tried first and the whole file stayed green, because point
sizes resolve against the paint device's logical DPI and devicePixelRatio does
not move it.  A mutation that does not land tells you nothing -- it nearly
certified this gate on the strength of a check that could not fail.
"""
from __future__ import annotations

import json
import os
import site
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]

#: Run inside a fresh interpreter so Qt resolves scaling from OUR environment.
#: Reports what the renderer would put on the wire, plus the dpr it saw, so the
#: comparison can prove the inputs really differed.
_HARNESS = r"""
import json, sys
sys.path.insert(0, %(src)r)
from PySide6.QtGui import QGuiApplication, QImage
app = QGuiApplication(sys.argv)
screen = app.primaryScreen()

from trcc.adapters.render.qt import QtRenderer

renderer = QtRenderer()
surface = QImage(320, 240, QImage.Format.Format_ARGB32)
surface.fill(0)

# The one DPI-sensitive construct in the whole render path: point sizes are
# resolved against the paint device's logical DPI.
from PySide6.QtGui import QFontMetrics
metrics = {}
for size in (12, 24, 48):
    font = renderer._get_font(size, False, False, "Sans Serif")
    fm = QFontMetrics(font, surface)
    metrics[size] = [fm.height(), fm.horizontalAdvance("CPU 52")]

renderer.draw_text(surface, 10, 60, "CPU 52", size=24, color="#ffffff")
wire = renderer.encode_rgb565(surface)

print(json.dumps({
    "dpr": screen.devicePixelRatio(),
    "screen_logical_dpi": screen.logicalDotsPerInch(),
    "surface_logical_dpi": surface.logicalDpiX(),
    "surface": [surface.width(), surface.height()],
    "wire_len": len(wire),
    "metrics": metrics,
}))
"""


def _render_at(scale_factor: str) -> dict:
    # ``site.USER_SITE`` is resolved at interpreter startup, so it still holds
    # the developer's REAL ``~/.local/...`` even though conftest redirects HOME
    # per test.  Without passing it the child cannot import PySide6 at all: it
    # would look for user site-packages under the tmp HOME and find nothing.
    paths = [str(_REPO / "src")]
    if site.USER_SITE:
        paths.append(site.USER_SITE)
    env = {
        **os.environ,
        "QT_QPA_PLATFORM": "offscreen",
        "QT_SCALE_FACTOR": scale_factor,
        "PYTHONPATH": os.pathsep.join(paths),
    }
    proc = subprocess.run(
        [sys.executable, "-c", _HARNESS % {"src": str(_REPO / "src")}],
        capture_output=True, text=True, env=env, timeout=120, check=False,
    )
    assert proc.returncode == 0, (
        f"harness failed at QT_SCALE_FACTOR={scale_factor}:\n{proc.stderr[-2000:]}"
    )
    return json.loads(proc.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module")
def rendered() -> tuple[dict, dict]:
    return _render_at("1"), _render_at("2")


def test_the_scale_factor_actually_differs(rendered: tuple[dict, dict]) -> None:
    """Prove the two runs really are two scalings before comparing them.

    Everything below asserts that something did NOT change.  Without this, a
    Qt that ignored ``QT_SCALE_FACTOR`` would make them pass by comparing one
    value with itself -- the shape of a green test that proves nothing.
    """
    one, two = rendered
    assert one["dpr"] == 1.0, one
    assert two["dpr"] == 2.0, (
        f"QT_SCALE_FACTOR=2 gave devicePixelRatio {two['dpr']} — this Qt does "
        f"not honour it, so every comparison in this file is vacuous"
    )


def test_text_geometry_is_identical_across_scale_factors(
    rendered: tuple[dict, dict],
) -> None:
    """The symptom #220 reports is a theme drawn 2x too big."""
    one, two = rendered
    assert one["metrics"] == two["metrics"], (
        f"overlay text resolves differently at devicePixelRatio 2 — this is "
        f"the #220 blow-up reaching the wire.\n  dpr 1: {one['metrics']}\n"
        f"  dpr 2: {two['metrics']}"
    )


def test_the_canvas_and_the_wire_do_not_move(rendered: tuple[dict, dict]) -> None:
    one, two = rendered
    assert one["surface"] == two["surface"] == [320, 240]
    assert one["wire_len"] == two["wire_len"] == 320 * 240 * 2


def test_the_canvas_dpi_is_pinned_whatever_the_screen_says(
    rendered: tuple[dict, dict],
) -> None:
    """WHY the frame is immune, not just that it is.

    A ``QImage`` carries its own logical DPI and does not inherit the screen's.
    That is the actual guarantee; if a future change composed onto a QPixmap or
    a widget-derived surface instead, this is the assertion that would break
    first, and it names the reason rather than the symptom.
    """
    for run in rendered:
        assert run["surface_logical_dpi"] == 96, run
