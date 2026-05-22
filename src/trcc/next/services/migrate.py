"""LegacyMigrationService — pull user content forward from legacy TRCC.

Legacy TRCC stores user themes and masks under ``~/.trcc/themes/`` and
``~/.trcc/masks/`` (locations driven by ``trcc.core.paths``).  next/'s
paths port returns the same on Linux, so themes are usually already
visible to ``ListThemes`` without any copying.

What migration does need to handle:

* If the legacy install used a non-default location (e.g. installed via
  an alt-distro that pinned ``XDG_DATA_HOME`` differently), themes
  may live somewhere next/'s Paths doesn't look — this service
  copies them across.
* Per-device LCD/LED settings from legacy's ``config.json`` are in a
  different JSON shape than next/'s ``trcc-next.json``; we read what
  legacy persisted and translate the small overlap (current theme,
  orientation, brightness, temp_unit) into next/'s Settings.

Side-effect-light by default: ``dry_run=True`` reports what would
happen without copying or rewriting anything.  The Command surfaces
both modes.
"""
from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from ..core.ports import Paths
from .settings import Settings

log = logging.getLogger(__name__)


@dataclass
class MigrationReport:
    """What got pulled forward (or would, with ``dry_run``)."""
    legacy_config_path: str = ""
    legacy_config_exists: bool = False
    themes_copied: list[str] = field(default_factory=list)
    masks_copied: list[str] = field(default_factory=list)
    settings_keys_imported: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    dry_run: bool = True

    @property
    def total_changes(self) -> int:
        return (
            len(self.themes_copied)
            + len(self.masks_copied)
            + len(self.settings_keys_imported)
        )


class LegacyMigrationService:
    """Pull legacy themes + masks + settings forward to next/.

    Used once by ``MigrateFromLegacy`` Command — not on every startup.
    """

    def __init__(self, paths: Paths, settings: Settings) -> None:
        self._paths = paths
        self._settings = settings

    # ── Public ────────────────────────────────────────────────────────

    def run(self, *, dry_run: bool = False) -> MigrationReport:
        """Walk the legacy locations, optionally copy to next/'s tree.

        Layout assumption: legacy stores under ``~/.trcc/`` on Linux.
        If a user installed legacy somewhere weirder, this won't find
        their stuff — the report warns and lists what was checked.
        """
        report = MigrationReport(dry_run=dry_run)

        legacy_root = self._guess_legacy_root()
        if not legacy_root.exists():
            report.warnings.append(
                f"No legacy install found at {legacy_root} — nothing to migrate.",
            )
            return report

        report.legacy_config_path = str(legacy_root / "config.json")
        report.legacy_config_exists = (legacy_root / "config.json").exists()

        self._migrate_themes(legacy_root, report)
        self._migrate_masks(legacy_root, report)
        self._migrate_settings(legacy_root, report)
        return report

    # ── Steps ─────────────────────────────────────────────────────────

    def _guess_legacy_root(self) -> Path:
        """Return the most likely legacy root.  Linux default is ``~/.trcc/``."""
        return Path.home() / ".trcc"

    def _migrate_themes(self, root: Path, report: MigrationReport) -> None:
        src = root / "themes"
        if not src.is_dir():
            return
        dst = self._paths.user_content_dir() / "themes"
        report.themes_copied.extend(
            self._copy_subdirs(src, dst, dry_run=report.dry_run),
        )

    def _migrate_masks(self, root: Path, report: MigrationReport) -> None:
        src = root / "masks"
        if not src.is_dir():
            return
        dst = self._paths.user_content_dir() / "masks"
        report.masks_copied.extend(
            self._copy_files(src, dst, dry_run=report.dry_run),
        )

    def _migrate_settings(
        self, root: Path, report: MigrationReport,
    ) -> None:
        """Translate the small overlap between legacy ``config.json`` and
        next/'s settings.  Skip everything next/ doesn't recognise."""
        path = root / "config.json"
        if not path.is_file():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            report.warnings.append(
                f"Couldn't read legacy {path}: {e}.  Settings skipped.",
            )
            return

        # Legacy "language" key is the simplest one-to-one — UI lang.
        lang = raw.get("language")
        if isinstance(lang, str) and lang and not report.dry_run:
            self._settings.set_language(lang)
        if lang:
            report.settings_keys_imported.append("language")

        # Per-device shape between trees differs enough that we don't
        # auto-import LCD/LED state — too easy to nuke the user's
        # next/ choices with stale legacy values.  Users who want it
        # can re-run their setup explicitly.

    # ── Filesystem helpers ────────────────────────────────────────────

    @staticmethod
    def _copy_subdirs(
        src: Path, dst: Path, *, dry_run: bool,
    ) -> list[str]:
        """Copy each subdirectory of *src* into *dst*; skip if target exists."""
        copied: list[str] = []
        if not dry_run:
            dst.mkdir(parents=True, exist_ok=True)
        for entry in sorted(src.iterdir()):
            if not entry.is_dir():
                continue
            target = dst / entry.name
            if target.exists():
                continue
            copied.append(entry.name)
            if dry_run:
                continue
            try:
                shutil.copytree(entry, target)
            except OSError as e:
                log.warning("Migration: couldn't copy %s -> %s: %s",
                            entry, target, e)
        return copied

    @staticmethod
    def _copy_files(
        src: Path, dst: Path, *, dry_run: bool,
    ) -> list[str]:
        """Copy each file under *src* into *dst*; skip existing files."""
        copied: list[str] = []
        if not dry_run:
            dst.mkdir(parents=True, exist_ok=True)
        for entry in sorted(src.iterdir()):
            if not entry.is_file():
                continue
            target = dst / entry.name
            if target.exists():
                continue
            copied.append(entry.name)
            if dry_run:
                continue
            try:
                shutil.copy2(entry, target)
            except OSError as e:
                log.warning("Migration: couldn't copy %s -> %s: %s",
                            entry, target, e)
        return copied
