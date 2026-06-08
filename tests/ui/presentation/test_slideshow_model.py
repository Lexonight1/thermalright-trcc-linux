"""SlideshowModel — pure-Python tests (NO Qt, NO QApplication).

Locks the slideshow rules that used to live as raw attrs on UCThemeLocal:
the max-6 add/remove array, the min-3 interval clamp, and badge positioning.
"""
from __future__ import annotations

from trcc.ui.presentation.slideshow_model import (
    MAX_SLIDESHOW,
    MIN_INTERVAL,
    SlideshowModel,
)


def test_new_model_defaults() -> None:
    m = SlideshowModel()
    assert m.themes == []
    assert m.enabled is False
    assert m.interval == MIN_INTERVAL


def test_toggle_theme_add_then_remove() -> None:
    m = SlideshowModel()
    assert m.toggle_theme("A") is True
    assert m.themes == ["A"]
    assert m.toggle_theme("A") is False
    assert m.themes == []


def test_toggle_theme_caps_at_max_and_preserves_order() -> None:
    m = SlideshowModel()
    names = [f"t{i}" for i in range(MAX_SLIDESHOW)]
    for n in names:
        assert m.toggle_theme(n) is True
    assert m.themes == names
    # 7th is refused
    assert m.toggle_theme("overflow") is False
    assert m.themes == names


def test_badge_position_is_one_based_else_zero() -> None:
    m = SlideshowModel()
    m.toggle_theme("A")
    m.toggle_theme("B")
    assert m.badge_position("A") == 1
    assert m.badge_position("B") == 2
    assert m.badge_position("missing") == 0


def test_remove_theme() -> None:
    m = SlideshowModel()
    m.toggle_theme("A")
    m.toggle_theme("B")
    m.remove_theme("A")
    assert m.themes == ["B"]
    m.remove_theme("absent")          # no-op
    assert m.themes == ["B"]


def test_toggle_enabled_flips_and_returns_new() -> None:
    m = SlideshowModel()
    assert m.toggle_enabled() is True
    assert m.enabled is True
    assert m.toggle_enabled() is False


def test_set_interval_clamps_to_min_and_parses() -> None:
    m = SlideshowModel()
    assert m.set_interval("5") == 5
    assert m.interval == 5
    assert m.set_interval("2") == MIN_INTERVAL      # below min → clamped
    assert m.set_interval("abc") == MIN_INTERVAL    # non-numeric → min
    assert m.set_interval(10) == 10


def test_restore_caps_themes_and_sets_state_verbatim() -> None:
    m = SlideshowModel()
    many = [f"t{i}" for i in range(MAX_SLIDESHOW + 3)]
    m.restore(many, enabled=True, interval=1)        # interval verbatim (restore allows <min)
    assert m.themes == many[:MAX_SLIDESHOW]
    assert m.enabled is True
    assert m.interval == 1
