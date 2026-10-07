"""Settings file rename: ``trcc-next.json`` → ``trcc.json``.

next/'s persistence filename baked the temporal label ``next`` into a
file that becomes the durable shape at cutover.  The rename to
``trcc.json`` lands the new name with a read-old-write-new migration
so existing users don't lose state.

These tests cover:
  * fresh installs write ``trcc.json``
  * pre-cutover ``trcc-next.json`` is read when no ``trcc.json`` exists
  * next save promotes state to ``trcc.json`` (and leaves the old file
    for rollback)
"""
from __future__ import annotations

import json
from pathlib import Path

from trcc.services.settings import Settings

from .conftest import FakePaths


def test_fresh_install_writes_trcc_json(tmp_path: Path) -> None:
    """A new Settings on an empty config dir saves to ``trcc.json``."""
    paths = FakePaths(tmp_path)
    s = Settings(paths)
    s.set_orientation("0402:3922", 90)

    assert (tmp_path / "trcc.json").is_file()
    assert not (tmp_path / "trcc-next.json").exists()


def test_reads_pre_cutover_filename_when_only_old_exists(
    tmp_path: Path,
) -> None:
    """A user upgrading from pre-cutover next/ keeps their settings —
    the loader reads ``trcc-next.json`` when ``trcc.json`` is absent."""
    paths = FakePaths(tmp_path)
    payload = {
        "app": {"language": "fr", "temp_unit": "F"},
        "devices": {
            "0402:3922": {"orientation": 180, "brightness": 25},
        },
        "led_devices": {},
    }
    (tmp_path / "trcc-next.json").write_text(
        json.dumps(payload), encoding="utf-8",
    )

    s = Settings(paths)

    assert s.app.language == "fr"
    assert s.app.temp_unit == "F"
    dev = s.for_device("0402:3922")
    assert dev.orientation == 180
    assert dev.brightness == 25


def test_removed_gpu_reader_keys_are_ignored_on_load(tmp_path: Path) -> None:
    """Configs written by older versions carried GPU-reader-offer keys
    (``gpu_reader_offer_suppressed`` / the older ``gpu_reader_install_declined``)
    that no longer exist — nvidia-ml-py is now a core dependency, so the prompt
    was retired.  A config still carrying them must load cleanly (unknown keys
    are dropped), not crash — otherwise the retirement would break upgraders."""
    paths = FakePaths(tmp_path)
    config = paths.config_dir() / "trcc.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        '{"app": {"gpu_reader_offer_suppressed": true, '
        '"gpu_reader_install_declined": true, "language": "en"}}'
    )

    s = Settings(paths)
    assert s.app.language == "en"                     # real keys still load
    assert not hasattr(s.app, "gpu_reader_offer_suppressed")  # removed key dropped


def test_led_test_mode_is_not_restored_from_disk(tmp_path: Path) -> None:
    """``test_mode`` is a momentary diagnostic, not a saved preference.  A True
    flag left on disk by a past ``EnableLedTestMode`` (CLI / smoke run) must NOT
    reload — otherwise every summon of a PAGE-style device restores it and the
    panel paints near-black on load.  Other LED fields still load normally."""
    paths = FakePaths(tmp_path)
    config = paths.config_dir() / "trcc.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(json.dumps({
        "app": {"language": "en"},
        "devices": {},
        "led_devices": {"0416:8001": {
            "test_mode": True, "brightness": 98, "color": [0, 13, 255],
        }},
    }), encoding="utf-8")

    s = Settings(paths)
    led = s.for_led("0416:8001")
    assert led.test_mode is False          # diagnostic flag not restored
    assert led.brightness == 98            # real preferences still load
    assert led.color == (0, 13, 255)


def test_next_save_promotes_to_trcc_json(tmp_path: Path) -> None:
    """After reading the pre-cutover file, the next mutation writes
    ``trcc.json`` (the new name) without touching the old file."""
    paths = FakePaths(tmp_path)
    (tmp_path / "trcc-next.json").write_text(json.dumps({
        "app": {"language": "en"},
        "devices": {"0402:3922": {"brightness": 50}},
        "led_devices": {},
    }), encoding="utf-8")

    s = Settings(paths)
    s.set_brightness("0402:3922", 75)

    # New name now holds the truth; old file kept for rollback.
    assert (tmp_path / "trcc.json").is_file()
    assert (tmp_path / "trcc-next.json").is_file()
    raw = json.loads((tmp_path / "trcc.json").read_text(encoding="utf-8"))
    assert raw["devices"]["0402:3922"]["brightness"] == 75


def test_prefers_trcc_json_over_pre_cutover(tmp_path: Path) -> None:
    """When both filenames exist (rollback scenario), ``trcc.json``
    wins — no merging of stale state."""
    paths = FakePaths(tmp_path)
    (tmp_path / "trcc.json").write_text(json.dumps({
        "app": {"language": "de"},
        "devices": {}, "led_devices": {},
    }), encoding="utf-8")
    (tmp_path / "trcc-next.json").write_text(json.dumps({
        "app": {"language": "ja"},   # should be ignored
        "devices": {}, "led_devices": {},
    }), encoding="utf-8")

    s = Settings(paths)

    assert s.app.language == "de"


def test_a_config_from_before_game_mode_loads_it_off(tmp_path: Path) -> None:
    """Configs and per-folder slots written before game mode existed carry
    neither field; both read back as the C#'s defaults (UCThemeLocal.cs:122-123),
    off at 75%."""
    payload = {
        "devices": {"0402:3922": {
            "brightness": 40, "active_catalog": "theme320320",
            "orientation_slots": {"theme240320": {"brightness": 80}},
        }},
    }
    (tmp_path / "trcc.json").write_text(json.dumps(payload), encoding="utf-8")

    dev = Settings(FakePaths(tmp_path)).for_device("0402:3922")

    assert (dev.game_enabled, dev.game_threshold) == (False, 75)
    slot = dev.orientation_slots["theme240320"]
    assert (slot.brightness, slot.game_enabled, slot.game_threshold) == (80, False, 75)


def test_the_game_threshold_is_held_to_two_digits(tmp_path: Path) -> None:
    """The C#'s threshold box takes two digits; a value outside 0-99 from the
    CLI or API is clamped, and ``None`` leaves that half alone."""
    s = Settings(FakePaths(tmp_path))
    dev = s.for_device("0402:3922")
    s.set_game_mode("0402:3922", enabled=True)
    s.set_game_mode("0402:3922", threshold=150)
    assert (dev.game_enabled, dev.game_threshold) == (True, 99)
    s.set_game_mode("0402:3922", threshold=-5)
    assert (dev.game_enabled, dev.game_threshold) == (True, 0)
    s.set_game_mode("0402:3922", threshold=42)
    s.set_game_mode("0402:3922", enabled=False)
    saved = Settings(FakePaths(tmp_path)).for_device("0402:3922")
    assert (saved.game_enabled, saved.game_threshold) == (False, 42)
