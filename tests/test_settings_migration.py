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


def test_gpu_reader_offer_suppressed_flag_round_trips(tmp_path: Path) -> None:
    """The "Don't ask again" GPU-reader opt-out persists and reloads."""
    paths = FakePaths(tmp_path)
    s = Settings(paths)
    assert s.app.gpu_reader_offer_suppressed is False  # default
    s.set_gpu_reader_offer_suppressed(True)

    reloaded = Settings(paths)
    assert reloaded.app.gpu_reader_offer_suppressed is True


def test_stale_declined_flag_is_dropped_on_load(tmp_path: Path) -> None:
    """A config written by the old, over-eager ``gpu_reader_install_declined``
    key is ignored — anyone previously silenced by a stray dismissal gets a
    fresh offer under the corrected opt-out semantics (#161)."""
    paths = FakePaths(tmp_path)
    config = paths.config_dir() / "trcc.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text('{"app": {"gpu_reader_install_declined": true}}')

    s = Settings(paths)
    assert s.app.gpu_reader_offer_suppressed is False


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
