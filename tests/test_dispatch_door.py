"""One door for every Command (``ui/_dispatch_door``): what it knows, what
the API door refuses, and how the CLI reads a value."""
from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import pytest

from trcc.ui import _dispatch_door as door


def test_every_module_command_is_behind_the_door() -> None:
    """The point of the door: a module's Command needs no route and no CLI
    command of its own to be reachable.  MUTATION CHECK: drop the
    ``load_core`` loop from ``known`` -- a fresh CLI process then lacks them."""
    from trcc import modules

    known = door.known()
    for key in modules.shipped():
        core = modules.part(key, "core")
        exported = [getattr(core, name) for name in getattr(core, "__all__", ())]
        commands = [c.__name__ for c in exported
                    if isinstance(c, type) and hasattr(c, "MODULE")]
        assert set(commands) <= set(known), key


@pytest.mark.parametrize(("name", "files"), [
    ("LoadTheme", True),        # a theme folder path
    ("RamLighting", False),
    ("OpenRgbPorts", False),
    ("PlayVisual", False),      # its settings: dict[str, float]
    ("SetLedMode", False),
])
def test_a_command_with_a_file_field_carries_files(name: str, files: bool) -> None:
    """The API door's one rule.  MUTATION CHECK: drop ``PurePath`` from
    ``_holds_files`` -> LoadTheme reads as file-free."""
    assert door.carries_files(door.known()[name]) is files


@dataclasses.dataclass(frozen=True)
class _Inner:
    """A dataclass whose path is optional -- module level, as a Command is,
    so its string annotations resolve."""
    where: Path | None = None


def test_a_path_inside_a_nested_dataclass_or_optional_counts() -> None:
    Inner = _Inner
    assert door._holds_files(Inner, set())
    assert door._holds_files(tuple[Inner, ...], set())
    assert not door._holds_files(dict[str, float], set())


@pytest.mark.parametrize(("text", "value"), [
    ("5", 5), ("true", True), ("[1, 2]", [1, 2]), ('{"a": 1}', {"a": 1}),
    ("0416:8001", "0416:8001"), ("rainbow", "rainbow"),
])
def test_a_cli_value_is_json_when_it_parses(text: str, value: Any) -> None:
    assert door.parse_value(text) == value


def test_an_unknown_name_says_where_the_list_is() -> None:
    with pytest.raises(ValueError, match="Unknown command: 'NoSuch'"):
        door.build("NoSuch", {})
