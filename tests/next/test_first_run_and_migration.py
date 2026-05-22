"""First-run detection + legacy migration — service + Command surface."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from trcc.next.app import App
from trcc.next.core.commands import (
    GetFirstRunStatus,
    MarkFirstRunDone,
    MigrateFromLegacy,
)
from trcc.next.services.first_run import FirstRunService

from .conftest import FakePaths

# =========================================================================
# FirstRunService
# =========================================================================


def test_first_run_starts_true_then_marks(tmp_path: Path) -> None:
    paths = FakePaths(tmp_path)
    svc = FirstRunService(paths)
    assert svc.is_first_run() is True
    svc.mark_completed()
    assert svc.is_first_run() is False
    # Idempotent — second call doesn't raise.
    svc.mark_completed()
    assert svc.is_first_run() is False


def test_first_run_reset_re_arms(tmp_path: Path) -> None:
    paths = FakePaths(tmp_path)
    svc = FirstRunService(paths)
    svc.mark_completed()
    svc.reset()
    assert svc.is_first_run() is True


def test_first_run_tolerates_unwritable_dir(tmp_path: Path) -> None:
    """If the config dir is read-only, mark_completed shouldn't raise."""
    paths = FakePaths(tmp_path / "nonexistent" / "deeply" / "nested")
    svc = FirstRunService(paths)
    # Should not raise even if the marker can't be written.
    svc.mark_completed()


# =========================================================================
# LegacyMigrationService
# =========================================================================


def test_migration_no_legacy_install_warns(
    fake_platform, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """If ~/.trcc doesn't exist, migration reports no work + a warning."""
    # Force the "legacy root" to a path that doesn't exist by pointing
    # HOME at an empty dir.
    monkeypatch.setenv("HOME", str(tmp_path / "empty-home"))
    app = App(fake_platform)
    report = app.migration.run(dry_run=True)
    assert report.total_changes == 0
    assert any("nothing to migrate" in w for w in report.warnings)


def test_migration_dry_run_lists_themes(
    fake_platform, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Dry-run reports what would copy without touching disk."""
    legacy_root = tmp_path / "home" / ".trcc"
    (legacy_root / "themes" / "AlphaTheme").mkdir(parents=True)
    (legacy_root / "themes" / "BetaTheme").mkdir(parents=True)
    (legacy_root / "masks").mkdir(parents=True)
    (legacy_root / "masks" / "ring.png").write_bytes(b"x")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    app = App(fake_platform)
    report = app.migration.run(dry_run=True)
    assert sorted(report.themes_copied) == ["AlphaTheme", "BetaTheme"]
    assert report.masks_copied == ["ring.png"]
    # Dry-run doesn't write to next/'s tree.
    next_themes = fake_platform.paths().user_content_dir() / "themes"
    assert not (next_themes / "AlphaTheme").exists()


def test_migration_commits_when_dry_run_false(
    fake_platform, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """``dry_run=False`` actually copies files."""
    legacy_root = tmp_path / "home" / ".trcc"
    theme_dir = legacy_root / "themes" / "CommittedTheme"
    theme_dir.mkdir(parents=True)
    (theme_dir / "trcc-next.json").write_text('{"width": 320, "height": 320}')
    (legacy_root / "masks").mkdir(parents=True)
    (legacy_root / "masks" / "logo.png").write_bytes(b"bytes")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    app = App(fake_platform)
    report = app.migration.run(dry_run=False)
    assert "CommittedTheme" in report.themes_copied
    next_theme_dir = (
        fake_platform.paths().user_content_dir() / "themes" / "CommittedTheme"
    )
    assert next_theme_dir.is_dir()
    assert (next_theme_dir / "trcc-next.json").is_file()


def test_migration_imports_language_from_legacy_config(
    fake_platform, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    legacy_root = tmp_path / "home" / ".trcc"
    legacy_root.mkdir(parents=True)
    (legacy_root / "config.json").write_text(json.dumps({"language": "fr"}))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    app = App(fake_platform)
    # Default language is "en"; migration should flip it to "fr".
    assert app.settings.app.language == "en"
    report = app.migration.run(dry_run=False)
    assert "language" in report.settings_keys_imported
    assert app.settings.app.language == "fr"


def test_migration_handles_corrupt_legacy_config(
    fake_platform, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Garbage in legacy config.json doesn't kill the migration."""
    legacy_root = tmp_path / "home" / ".trcc"
    legacy_root.mkdir(parents=True)
    (legacy_root / "config.json").write_text("{this isn't valid json")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    app = App(fake_platform)
    report = app.migration.run(dry_run=False)
    assert any("Couldn't read" in w for w in report.warnings)


# =========================================================================
# Commands
# =========================================================================


def test_get_first_run_status_command(fake_platform) -> None:
    app = App(fake_platform)
    r1 = app.dispatch(GetFirstRunStatus())
    assert r1.is_first_run is True
    assert "Welcome" in r1.message
    app.dispatch(MarkFirstRunDone())
    r2 = app.dispatch(GetFirstRunStatus())
    assert r2.is_first_run is False


def test_migrate_command_dry_run_default(fake_platform) -> None:
    app = App(fake_platform)
    r = app.dispatch(MigrateFromLegacy())
    assert r.ok is True
    assert r.dry_run is True


def test_migrate_command_with_yes(
    fake_platform, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """``MigrateFromLegacy(dry_run=False)`` actually copies."""
    legacy_root = tmp_path / "home" / ".trcc"
    (legacy_root / "themes" / "X").mkdir(parents=True)
    (legacy_root / "themes" / "X" / "trcc-next.json").write_text(
        '{"width": 240, "height": 240}',
    )
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    app = App(fake_platform)
    r = app.dispatch(MigrateFromLegacy(dry_run=False))
    assert r.ok is True
    assert r.dry_run is False
    assert "X" in r.themes_copied
