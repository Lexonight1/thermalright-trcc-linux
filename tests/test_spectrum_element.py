"""The ``spectrum`` overlay element: its model, its simulated source, the
playback watch that wakes it, and how the display paints it."""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, cast

import numpy as np

from trcc.core.models import OverlayElement
from trcc.services import playback_watch
from trcc.services.display import DisplayService
from trcc.services.spectrum_feed import SimulatedSpectrum


def _element(**over) -> dict:
    return {"id": "el_s", "type": "spectrum", "x": 10, "y": 20, "width": 100,
            "height": 60, "bars": 4, "cell": 6, "gap": 2, "color": "#2fa83a",
            **over}


class _Rects:
    """A renderer that only records the rectangles it is asked to fill."""

    def __init__(self) -> None:
        self.rects: list[tuple[int, int, int, int, tuple]] = []

    def fill_rect(self, surface, x, y, w, h, color):
        self.rects.append((x, y, w, h, color))
        return surface


def _display(heard) -> tuple[DisplayService, _Rects]:
    renderer = _Rects()
    display = object.__new__(DisplayService)
    display._r = cast(Any, renderer)
    display._spectrum = lambda: heard
    return display, renderer


def test_element_round_trips_its_fields() -> None:
    element = OverlayElement.from_dict(_element(mode="simulated",
                                                lms_url="http://h:9000",
                                                lms_player="aa:bb"))
    out = element.to_dict()
    assert (out["width"], out["height"], out["bars"]) == (100, 60, 4)
    assert (out["mode"], out["lms_url"], out["lms_player"]) == (
        "simulated", "http://h:9000", "aa:bb")
    assert json.loads(json.dumps(out)) == out


def test_other_elements_carry_no_spectrum_fields() -> None:
    assert "bars" not in OverlayElement(id="t", type="text").to_dict()


def test_lit_cells_follow_the_band_heights() -> None:
    heights = np.array([0.0, 0.5, 1.0, 0.0])
    display, renderer = _display((heights, heights))
    display._draw_spectrum_elements(object(), [_element()])
    rows = 60 // 6
    per_bar: dict[int, int] = {}
    for x, *_ in renderer.rects:
        per_bar[x] = per_bar.get(x, 0) + 1
    assert sorted(per_bar.values()) == [rows // 2, rows]
    assert all(x >= 10 and x + w <= 110 for x, _, w, _, _ in renderer.rects)


def test_a_peak_cap_floats_above_a_lower_bar() -> None:
    display, renderer = _display((np.array([0.2] * 4), np.array([0.8] * 4)))
    display._draw_spectrum_elements(object(), [_element()])
    dim = [r for r in renderer.rects if r[4][3] < 255]
    assert len(dim) == 4


def test_nothing_is_drawn_when_nothing_is_heard() -> None:
    display, renderer = _display(None)
    display._draw_spectrum_elements(object(), [_element()])
    assert renderer.rects == []


def test_a_band_count_mismatch_draws_nothing() -> None:
    display, renderer = _display((np.ones(3), np.ones(3)))
    display._draw_spectrum_elements(object(), [_element()])
    assert renderer.rects == []


def test_simulated_bands_move_while_playing_and_rest_after() -> None:
    dance = SimulatedSpectrum(18)
    seen = []
    for _ in range(90):
        heights, _ = dance.step(1 / 30, True)
        seen.append(heights.copy())
    assert max(h.max() for h in seen) > 0.4
    assert np.ptp(np.array(seen), axis=0).min() > 0.0      # every band moves
    for _ in range(120):
        heights, peaks = dance.step(1 / 30, False)
    assert heights.max() == 0.0 and peaks.max() < 0.01


def test_simulated_bands_stay_within_range() -> None:
    dance = SimulatedSpectrum(12)
    for _ in range(300):
        heights, peaks = dance.step(1 / 30, True)
        assert heights.min() >= 0.0 and peaks.max() <= 1.0


def test_lms_playing_reads_the_players_mode(monkeypatch) -> None:
    def reply(mode):
        body = json.dumps({"result": {"mode": mode}}).encode()
        return lambda *_a, **_k: SimpleNamespace(
            __enter__=lambda s: s, __exit__=lambda *a: None,
            read=lambda: body)

    import io
    for mode, expected in (("play", True), ("pause", False), ("stop", False)):
        monkeypatch.setattr(
            playback_watch.urllib.request, "urlopen",
            lambda *_a, _m=mode, **_k: io.BytesIO(
                json.dumps({"result": {"mode": _m}}).encode()))
        assert playback_watch.lms_playing("http://h:9000", "aa:bb") is expected


def test_lms_playing_is_false_when_the_server_is_away(monkeypatch) -> None:
    def refuse(*_a, **_k):
        raise OSError("down")
    monkeypatch.setattr(playback_watch.urllib.request, "urlopen", refuse)
    assert playback_watch.lms_playing("http://h:9000", "aa:bb") is False


def test_mpris_playing_matches_by_name(monkeypatch) -> None:
    listing = ("org.mpris.MediaPlayer2.strawberry 1 x\n"
               "org.mpris.MediaPlayer2.vlc 2 y\n")

    def run(cmd, **_k):
        if "list" in cmd:
            return SimpleNamespace(stdout=listing)
        playing = "strawberry" in cmd[3]
        return SimpleNamespace(stdout='s "Playing"' if playing else 's "Paused"')
    monkeypatch.setattr(playback_watch.subprocess, "run", run)
    assert playback_watch.mpris_playing(("strawberry",)) is True
    assert playback_watch.mpris_playing(("vlc",)) is False
    assert playback_watch.mpris_playing(()) is True
