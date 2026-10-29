"""ui/presentation/module_catalog -- what "Get modules" shows, without Qt."""
from __future__ import annotations

from typing import Literal

import pytest

from trcc.core.models import MODULE_CAPABILITIES, AvailableModule, ModuleAsks
from trcc.ui.presentation.module_catalog import (
    MAY,
    REVIEWED,
    catalog_rows,
    consent,
    removal_question,
)

Status = Literal["available", "installed", "update", "revoked"]


def _module(status: Status = "available", *, installed_version: str = "",
            reason: str = "", capabilities: tuple[str, ...] = (),
            network_hosts: tuple[str, ...] = ()) -> AvailableModule:
    return AvailableModule(key="weather", title="Weather", summary="The forecast.",
                           version="3", status=status,
                           asks=ModuleAsks(capabilities, network_hosts),
                           installed_version=installed_version, reason=reason)


@pytest.mark.parametrize(("module", "action", "enabled", "detail"), [
    (_module("available"), "Install", True, ""),
    (_module("update", installed_version="2"), "Update", True, "installed: 2"),
    (_module("installed", installed_version="3"), "Installed", False, ""),
    (_module("revoked", reason="sends your readings away"), "Taken back", False,
     "sends your readings away"),
])
def test_each_status_has_its_button(module: AvailableModule, action: str,
                                    enabled: bool, detail: str) -> None:
    (row,) = catalog_rows([module], str)
    assert (row.action, row.enabled, row.detail) == (action, enabled, detail)


def test_the_permission_screen_says_every_capability_and_site() -> None:
    """In the words the capabilities are known by -- what the install then
    sends as accepted."""
    module = _module(capabilities=("events", "network"),
                     network_hosts=("api.open-meteo.com",))
    question, lines = consent(module, str)
    assert question == "Install Weather 3?"
    assert lines == [REVIEWED, MAY, f"  - {MODULE_CAPABILITIES['events']}",
                     f"  - {MODULE_CAPABILITIES['network']}",
                     "  - fetches from api.open-meteo.com"]


def test_a_module_asking_for_nothing_says_only_that_it_was_reviewed() -> None:
    assert consent(_module(), str)[1] == [REVIEWED]


def test_the_texts_are_translated_then_filled() -> None:
    """A translation carries the placeholders: the value goes in after."""
    words = {"Install {title} {version}?": "{title} {version} installieren?",
             "Remove {title}? Themes made with it stay.": "{title} entfernen?",
             "installed: {version}": "installiert: {version}", "Weather": "Wetter"}

    def tr(text: str) -> str:
        return words.get(text, text)

    assert consent(_module(), tr)[0] == "Wetter 3 installieren?"
    assert removal_question(tr("Weather"), tr) == "Wetter entfernen?"
    assert catalog_rows([_module("update", installed_version="2")], tr)[0].detail == (
        "installiert: 2")
