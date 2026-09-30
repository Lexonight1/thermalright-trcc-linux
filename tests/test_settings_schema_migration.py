"""Config schema v1 → v2 — the overlay working layer changed MEANING.

Under v1 an empty ``user_overlay_elements`` meant "this device has no overlay
layout of its own" and the render fell back to the theme's.  Under v2 it means
"the layout is empty — draw nothing", and no-layout is ``None``.

``Settings._save`` writes every field via ``asdict``, so essentially every
config already on disk carries an empty list for the majority of users who
never edited an overlay.  Read at face value under v2, those users lose their
overlay on upgrade — a silent regression affecting almost everyone, which is
why the meaning change is versioned and migrated rather than just shipped.

The load-bearing test is the last one: it writes a REAL pre-upgrade config to
disk and checks what ends up on screen.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from trcc.core.models import OverlayElement
from trcc.services.settings import _SCHEMA_VERSION, Settings

_KEY = "87ad:70db"


class _Paths:
    """Minimal Paths port — Settings only needs ``config_dir``."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def config_dir(self) -> Path:
        return self._root

    def __getattr__(self, name: str) -> object:
        def _dir(*a: object, **k: object) -> Path:
            return self._root
        return _dir


def _write_config(root: Path, payload: dict) -> None:
    (root / "trcc.json").write_text(json.dumps(payload), encoding="utf-8")


def test_v1_empty_layer_reads_as_no_layout(tmp_path: Path) -> None:
    """The whole point: a v1 ``[]`` is NOT a deliberate empty."""
    _write_config(tmp_path, {
        "app": {}, "led_devices": {},
        "devices": {_KEY: {"user_overlay_elements": []}},
    })

    s = Settings(_Paths(tmp_path))

    assert s.for_device(_KEY).user_overlay_elements is None, (
        "a pre-v2 empty list means 'no layout of its own' and must migrate "
        "to None, or every user who never edited an overlay loses it"
    )


def test_v1_populated_layer_is_carried_through(tmp_path: Path) -> None:
    """An established layout is already unambiguous — leave it alone."""
    _write_config(tmp_path, {
        "app": {}, "led_devices": {},
        "devices": {_KEY: {"user_overlay_elements": [
            {"id": "u1", "type": "text", "x": 1, "y": 2, "text": "hi"},
        ]}},
    })

    s = Settings(_Paths(tmp_path))

    layer = s.for_device(_KEY).user_overlay_elements
    assert layer is not None
    assert [e.id for e in layer] == ["u1"]


@pytest.mark.parametrize("schema", range(2, _SCHEMA_VERSION + 1))
def test_v2_empty_layer_stays_empty(tmp_path: Path, schema: int) -> None:
    """Once stamped, ``[]`` means what it says — the user emptied it.

    EVERY schema from 2 on, literally.  This wrote ``_SCHEMA_VERSION`` — the
    current version, not 2 — so when the LED work bumped it to 3 the test
    moved with it and stopped testing v2, while the migration began rewriting
    every released v2 config.
    """
    _write_config(tmp_path, {
        "schema": schema, "app": {}, "led_devices": {},
        "devices": {_KEY: {"user_overlay_elements": []}},
    })

    s = Settings(_Paths(tmp_path))

    assert s.for_device(_KEY).user_overlay_elements == [], (
        "a v2 empty layer is a deliberate clear and must NOT be migrated"
    )


def test_a_save_stamps_the_current_schema(tmp_path: Path) -> None:
    """Migration runs once: the next save records the version."""
    _write_config(tmp_path, {
        "app": {}, "led_devices": {},
        "devices": {_KEY: {"user_overlay_elements": []}},
    })

    s = Settings(_Paths(tmp_path))
    s.set_overlay_enabled(_KEY, False)          # any setter saves

    written = json.loads((tmp_path / "trcc.json").read_text())
    assert written["schema"] == _SCHEMA_VERSION


def test_the_emptied_layer_survives_a_restart(tmp_path: Path) -> None:
    """The round trip, not the write half.

    Emptying the layer must still read back as emptied — this is the pair the
    whole change exists for, and asserting only the in-memory value would pass
    for a serializer that drops the distinction (see
    ``feedback_gate_the_round_trip_not_the_write_half``).
    """
    s = Settings(_Paths(tmp_path))
    s.set_user_overlay_elements(_KEY, [
        OverlayElement(id="u1", type="text", x=1, y=1, text="hi"),
    ])
    s.set_user_overlay_elements(_KEY, [])       # the user deletes the last one

    reloaded = Settings(_Paths(tmp_path)).for_device(_KEY)

    assert reloaded.user_overlay_elements == [], (
        "a deliberately emptied layer came back as something else after a "
        "restart — the exact shape of #276"
    )


def test_a_corrupt_schema_value_is_treated_as_v1(tmp_path: Path) -> None:
    """Never trust the file: a junk version must fail SAFE (migrate), not
    skip the migration and blank someone's overlay."""
    _write_config(tmp_path, {
        "schema": "two", "app": {}, "led_devices": {},
        "devices": {_KEY: {"user_overlay_elements": []}},
    })

    s = Settings(_Paths(tmp_path))

    assert s.for_device(_KEY).user_overlay_elements is None


# ── v4 -> v5: the clock format moves onto each element ─────────────────────


def _v4(tmp_path: Path, elements: list | None, **device: str) -> Settings:
    _write_config(tmp_path, {
        "schema": 4, "app": {"time_format": device.get("time_format", "24h")},
        "led_devices": {},
        "devices": {_KEY: {"user_overlay_elements": elements, **device}},
    })
    return Settings(_Paths(tmp_path))


def _clock(eid: str, source: str, fmt: str) -> dict:
    return {"id": eid, "type": "clock", "source": source, "format": fmt}


def _formats(s: Settings) -> dict[str, str]:
    return {e.id: e.format for e in s.for_device(_KEY).user_overlay_elements or ()}


def test_a_device_set_to_12h_upgrades_into_its_time_elements(tmp_path: Path) -> None:
    """Up to v4 every time element drew in the device's ``time_format``."""
    s = _v4(tmp_path, [_clock("a", "time", "%H:%M"), _clock("b", "time", "{value}"),
                       _clock("d", "date", "%Y/%m/%d")], time_format="12h")
    assert _formats(s) == {"a": "%I:%M %p", "b": "%I:%M %p", "d": "%Y/%m/%d"}


def test_a_custom_date_format_fills_the_date_elements_it_drew_on(
    tmp_path: Path,
) -> None:
    """The old rule: the device pattern drew on a date element with no
    pattern or the default one; a theme's deliberate ``%m/%d`` kept its own."""
    s = _v4(tmp_path, [_clock("a", "date", "%Y/%m/%d"), _clock("b", "date", "{value}"),
                       _clock("c", "date", "%m/%d")], date_format="dd/MM/yyyy")
    assert _formats(s) == {"a": "%d/%m/%Y", "b": "%d/%m/%Y", "c": "%m/%d"}


def test_the_defaults_leave_every_element_its_own_format(tmp_path: Path) -> None:
    """Nobody chose anything, so nothing is written: a theme designed with a
    12h clock keeps it, which is what the C# draws."""
    s = _v4(tmp_path, [_clock("a", "time", "%I:%M %p"), _clock("b", "time", "%H:%M"),
                       _clock("d", "date", "%m/%d")])
    assert _formats(s) == {"a": "%I:%M %p", "b": "%H:%M", "d": "%m/%d"}


def test_a_12h_saved_without_am_pm_gets_it(tmp_path: Path) -> None:
    """A gui click saved ``%I:%M`` before 12h meant the C#'s ``hh:mm tt``."""
    s = _v4(tmp_path, [_clock("a", "time", "%I:%M")])
    assert _formats(s) == {"a": "%I:%M %p"}


def test_a_device_with_no_layout_of_its_own_upgrades_cleanly(tmp_path: Path) -> None:
    s = _v4(tmp_path, None, time_format="12h")
    assert s.for_device(_KEY).user_overlay_elements is None


def test_the_upgrade_is_saved_once_and_the_old_fields_are_gone(
    tmp_path: Path,
) -> None:
    """Saved at v5 with the formats in the elements; a second start does not
    re-apply the (now absent) device format over a later edit."""
    s = _v4(tmp_path, [_clock("a", "time", "%H:%M")], time_format="12h")
    s.set_overlay_enabled(_KEY, True)                        # any save
    written = json.loads((tmp_path / "trcc.json").read_text(encoding="utf-8"))
    device = written["devices"][_KEY]
    assert written["schema"] == _SCHEMA_VERSION
    assert "time_format" not in device and "date_format" not in device
    assert "time_format" not in written["app"]
    assert device["user_overlay_elements"][0]["format"] == "%I:%M %p"

    s.set_clock_format(_KEY, "time", "%H:%M")                # a later edit
    assert _formats(Settings(_Paths(tmp_path))) == {"a": "%H:%M"}
