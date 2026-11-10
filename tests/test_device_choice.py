"""DeviceChoice -- which device a graphical window shows, for every skin.

The gui's rules (the blueprint, his call 2026-11-10: "the only thing on a gui
should be the skin"), written once.  Plain pytest: there is no toolkit here.
"""
from __future__ import annotations

from trcc.ui.presentation.device_choice import DeviceChoice

A, B, LED = "0402:3922", "87ad:70db", "0416:8001"
BOTH = {A: "lcd", B: "lcd"}


def _choice(last_used: str = "") -> tuple[DeviceChoice, list[str]]:
    remembered: list[str] = []
    return DeviceChoice(last_used, remembered.append), remembered


def test_it_opens_on_the_last_used_device() -> None:
    choice, remembered = _choice(last_used=B)
    assert choice.opened(BOTH) == B
    assert remembered == []                         # already the last used


def test_without_one_it_opens_on_the_first_lcd_and_remembers_it() -> None:
    choice, remembered = _choice(last_used="9999:0001")       # not attached
    assert choice.opened({LED: "led", A: "lcd", B: "lcd"}) == A
    assert remembered == [A]


def test_an_led_controller_is_the_default_only_when_it_is_all_there_is() -> None:
    choice, _ = _choice()
    assert choice.opened({LED: "led"}) == LED


def test_nothing_attached_is_nothing_on_screen() -> None:
    choice, remembered = _choice(last_used=A)
    assert choice.opened({}) == "" and remembered == []


def test_another_device_arriving_moves_nothing() -> None:
    choice, _ = _choice()
    choice.opened({A: "lcd"})
    assert choice.connected(B, BOTH) is None
    assert choice.shown == A


def test_an_empty_window_takes_the_device_that_arrives() -> None:
    choice, remembered = _choice()
    choice.opened({})
    assert choice.connected(A, {A: "lcd"}) == A and remembered == [A]


def test_losing_the_device_on_screen_shows_the_next_and_brings_it_back() -> None:
    """A blink (a VM, a hub reset) must not leave the user on another panel."""
    choice, remembered = _choice(last_used=B)
    choice.opened(BOTH)
    assert choice.disconnected(B, {A: "lcd"}) == A
    assert choice.connected(B, BOTH) == B
    assert remembered == [A, B]                    # every device shown


def test_a_pick_meanwhile_keeps_the_screen() -> None:
    choice, _ = _choice(last_used=B)
    choice.opened(BOTH)
    choice.disconnected(B, {A: "lcd"})
    assert choice.picked(A) == A
    assert choice.connected(B, BOTH) is None and choice.shown == A


def test_losing_a_device_not_on_screen_moves_nothing() -> None:
    choice, _ = _choice()
    choice.opened(BOTH)
    assert choice.disconnected(B, {A: "lcd"}) is None and choice.shown == A


def test_losing_the_last_device_leaves_nothing_on_screen() -> None:
    choice, _ = _choice()
    choice.opened({A: "lcd"})
    assert choice.disconnected(A, {}) == ""
