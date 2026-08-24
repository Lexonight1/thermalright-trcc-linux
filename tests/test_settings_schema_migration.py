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


def test_v2_empty_layer_stays_empty(tmp_path: Path) -> None:
    """Once stamped, ``[]`` means what it says — the user emptied it."""
    _write_config(tmp_path, {
        "schema": _SCHEMA_VERSION, "app": {}, "led_devices": {},
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
