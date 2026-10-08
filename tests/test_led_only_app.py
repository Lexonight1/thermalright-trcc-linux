"""#299: an App that finds only LED coolers loads no Qt.

An LED cooler shows a segment display: the App sends every UI a list of
colours (``FrameSent.display_colors``), never a picture, so the Qt renderer
was ~30 MB held for nothing (measured on the mock: 58 MB without, 90 with).

It is decided at build, on the main thread, from the startup scan -- a cooler
sits inside the case, so the scan sees them all.  Built lazily instead, Qt
would start on whichever worker drew the first frame, and Qt started off the
main thread crashes the process at exit (measured: a core dump).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from trcc._boot import _build_local_app
from trcc.app import App
from trcc.core.commands import ConnectDevice
from trcc.core.events import ErrorOccurred, FrameSent, SensorsUpdated

from .mock_platform import MockPlatform

ROOT = Path(__file__).resolve().parents[1]
LED = {"vid": "0416", "pid": "8001", "pm": 1}
LCD = {"vid": "0402", "pid": "3922"}

_PROBE = """
import json, os, sys, tempfile
from pathlib import Path
sys.path.insert(0, ".")
from tests.mock_platform import MockPlatform
from trcc._boot import _build_local_app
app = _build_local_app(platform=MockPlatform(json.loads(os.environ["FLEET"]),
                                             Path(tempfile.mkdtemp())))
print(json.dumps([app.has_renderer, "PySide6.QtGui" in sys.modules]))
"""


def _probe(fleet: list[dict]) -> list[bool]:
    """Build the App in a FRESH process -- this one has Qt loaded already."""
    # The parent's whole import path: the suite points HOME at a temp dir,
    # which hides the user site-packages a child would otherwise find.
    path = os.pathsep.join([str(ROOT / "src"), *sys.path])
    env = {**os.environ, "PYTHONPATH": path, "TRCC_DAEMON": "0",
           "FLEET": json.dumps(fleet)}
    out = subprocess.run([sys.executable, "-c", _PROBE], cwd=ROOT, env=env,
                         capture_output=True, text=True, timeout=120,
                         check=False)
    assert out.returncode == 0, out.stderr[-2000:]
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_led_coolers_only_never_import_qt() -> None:
    assert _probe([LED]) == [False, False]


def test_any_lcd_or_an_empty_scan_builds_the_renderer_as_before(
        tmp_path: Path) -> None:
    for fleet in ([LED, LCD], [LCD], []):
        app = _build_local_app(platform=MockPlatform(fleet, tmp_path))
        assert app.has_renderer, fleet


def test_an_led_only_app_still_updates_its_segment_display(
        tmp_path: Path, monkeypatch) -> None:
    """The render observer's no-renderer guard sat above the LED branch.

    A STATIC LED display -- the observer leaves an animating one to the
    animation loop, which owns its cadence."""
    app = App(MockPlatform([LED], tmp_path))
    assert not app.has_renderer
    assert app.dispatch(ConnectDevice(key="0416:8001")).ok
    monkeypatch.setattr(app.led_animation_loop, "animating_keys", list)
    sent: list = []
    app.events.subscribe(FrameSent, sent.append)
    app.events.publish(SensorsUpdated(readings={"cpu:temp": 55.0}))
    assert [e.key for e in sent] == ["0416:8001"]
    assert sent[0].display_colors


def test_a_late_lcd_on_an_led_only_app_says_to_restart(tmp_path: Path) -> None:
    app = App(MockPlatform([LED, LCD], tmp_path))
    errors: list = []
    app.events.subscribe(ErrorOccurred, errors.append)
    assert app.dispatch(ConnectDevice(key="0402:3922")).ok
    assert [e.kind for e in errors] == ["render"]
    assert "Restart TRCC" in errors[0].message
