"""What both windows say about RAM lighting -- no Qt."""
from __future__ import annotations

import pytest

from trcc.core.models import RamAccessState
from trcc.core.results import RamLightingResult
from trcc.ui.presentation.ram_access import WAITING, RamAccessView, ram_access_view


def _result(state: RamAccessState, command: str = "") -> RamLightingResult:
    return RamLightingResult(ok=True, state=state, message=f"msg {state.value}",
                             command=command)


@pytest.mark.parametrize("state, view", [
    (RamAccessState.UNSUPPORTED, RamAccessView("msg unsupported")),
    (RamAccessState.NO_BUS, RamAccessView("msg no-bus")),
    (RamAccessState.ON, RamAccessView("msg on", "Turn off RAM lighting", False)),
    (RamAccessState.NOT_APPLIED,
     RamAccessView("msg not-applied", "Turn off RAM lighting", False)),
    (RamAccessState.OFF, RamAccessView("msg off", "Enable RAM lighting...", True)),
    # Another program's grant: TRCC's own still lets it survive that one going.
    (RamAccessState.ELSEWHERE,
     RamAccessView("msg elsewhere", "Enable RAM lighting...", True)),
], ids=lambda v: getattr(v, "value", ""))
def test_each_state_is_one_line_and_at_most_one_button(state, view) -> None:  # type: ignore[no-untyped-def]
    assert ram_access_view(_result(state)) == view


def test_with_no_prompt_here_the_row_names_the_command() -> None:
    view = ram_access_view(_result(RamAccessState.OFF,
                                   "sudo trcc system ram-lighting enable"))
    assert view == RamAccessView(
        "msg off -- to enable, run: sudo trcc system ram-lighting enable")


def test_while_waiting_on_the_password_there_is_no_button() -> None:
    assert ram_access_view(_result(RamAccessState.ON), busy=True) == RamAccessView(WAITING)
