"""Game mode's hysteresis, against the C#'s once-a-second check.

2.1.8 ``FormCZTV.GetSystemInfo`` (FormCZTV.cs:2853-2913).  Pure: no device,
no clock, no Qt.
"""
from __future__ import annotations

from trcc.services.game_mode import HOLD_COUNT, GameModeGate, GameVerdict

V = GameVerdict
_THR = 75


def _feed(gate: GameModeGate, values: list[int], enabled: bool = True,
          ) -> list[GameVerdict]:
    return [gate.step(enabled, v, _THR) for v in values]


def _engaged() -> GameModeGate:
    gate = GameModeGate()
    assert _feed(gate, [90] * (HOLD_COUNT + 1))[-1] is V.ENGAGE
    return gate


def test_the_eleventh_reading_above_engages_and_draws_nothing() -> None:
    """Ten readings arm the counter; the eleventh engages.  The C# returns
    from that check before drawing, so the game frame starts one later."""
    gate = GameModeGate()

    verdicts = _feed(gate, [90] * 12)

    assert verdicts == [V.IDLE] * 10 + [V.ENGAGE, V.HOLD]


def test_a_reading_at_the_threshold_is_not_above_it() -> None:
    """The C# compares with ``>``: 75 at a threshold of 75 never arms."""
    gate = GameModeGate()

    assert set(_feed(gate, [_THR] * 30)) == {V.IDLE}
    assert gate.count == 0


def test_one_low_reading_while_arming_starts_the_count_again() -> None:
    gate = GameModeGate()

    verdicts = _feed(gate, [90] * 10 + [50] + [90] * 11)

    assert V.ENGAGE not in verdicts[:21]
    assert verdicts[-1] is V.ENGAGE


def test_the_eleventh_reading_at_or_below_releases() -> None:
    """Engaged, the counter falls from 10; the low reading that finds it at 0
    releases, and like engaging it draws nothing."""
    gate = _engaged()

    verdicts = _feed(gate, [_THR] * 12)

    assert verdicts == [V.HOLD] * 10 + [V.RELEASE, V.IDLE]
    assert not gate.engaged


def test_one_high_reading_while_engaged_restarts_the_release_count() -> None:
    gate = _engaged()

    verdicts = _feed(gate, [50] * 10 + [90] + [50] * 11)

    assert V.RELEASE not in verdicts[:21]
    assert verdicts[-1] is V.RELEASE


def test_switching_off_while_engaged_releases_once() -> None:
    """The C# clears both fields when ``isGame`` goes off; the panel's own
    sources resume, so the task needs to hear it was a release."""
    gate = _engaged()

    assert _feed(gate, [90, 90], enabled=False) == [V.RELEASE, V.IDLE]
    assert (gate.engaged, gate.count) == (False, 0)


def test_switched_off_nothing_arms() -> None:
    gate = GameModeGate()

    assert set(_feed(gate, [99] * 30, enabled=False)) == {V.IDLE}
    assert _feed(gate, [90] * (HOLD_COUNT + 1))[-1] is V.ENGAGE
