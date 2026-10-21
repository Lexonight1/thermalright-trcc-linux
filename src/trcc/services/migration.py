"""One-time migration of user content into the ``data/`` library root.

Early cutover builds (before the per-resolution user dirs were rerouted
under :meth:`Paths.user_data_dir`) saved user themes/masks directly
beneath :meth:`Paths.user_content_dir` — ``theme{w}{h}/`` and ``web/``.
The current layout roots them under ``user_content_dir()/data/`` so the
user tree mirrors the shipped ``.trcc/data/`` tree byte-for-byte.

``LibraryMigration`` moves any old-location dirs into ``data/``,
merge-style and **never clobbering**: a destination that already exists
is left untouched.  Idempotent — once moved, later runs find nothing and
no-op.  Run at ``App`` construction; ``run()`` swallows its own I/O
errors so a migration hiccup can never block startup.
"""
from __future__ import annotations

import logging
import shutil
from collections.abc import Callable
from pathlib import Path

from ..core.models import Theme
from ..core.ports import ContentStore, Paths
from .settings import Settings

log = logging.getLogger(__name__)


class LibraryMigration:
    """Merge-move pre-``data/`` user content into the user data root.

    Scope is deliberately narrow — only the ``data/`` twin dirs
    (``theme{w}{h}`` + ``web``) are migrated.  Working dirs that
    legitimately live at the ``user_content_dir`` top level (``uploads``,
    ``masks``, ``single-image``, ``single-video``, ``backgrounds``) and
    the ``data/`` root itself are left alone.
    """

    def __init__(self, paths: Paths) -> None:
        log.debug("__init__: paths=%s", paths)
        self._paths = paths

    def run(self) -> int:
        """Move stray old-location library dirs under ``data/``.

        Returns the number of files moved.  Safe to call on every boot:
        with nothing to migrate it is a single ``iterdir``.  Never raises
        — per-entry I/O failures are logged and the entry left in place.
        """
        root = self._paths.user_content_dir()
        data_root = self._paths.user_data_dir()
        if not root.is_dir():
            log.debug("LibraryMigration: %s absent — nothing to migrate", root)
            return 0

        try:
            entries = [
                e for e in sorted(root.iterdir())
                if e.is_dir() and self._is_library_dir(e.name)
            ]
        except OSError as e:
            log.warning("LibraryMigration: cannot scan %s: %s", root, e)
            return 0

        moved = 0
        for entry in entries:
            dst = data_root / entry.name
            log.info("LibraryMigration: migrating %s → %s", entry, dst)
            try:
                moved += self._merge_move(entry, dst)
            except OSError as e:
                log.warning("LibraryMigration: failed on %s: %s — leaving "
                            "it in place", entry, e)
        if moved:
            log.info("LibraryMigration: moved %d file(s) into %s",
                     moved, data_root)
        return moved

    @staticmethod
    def _is_library_dir(name: str) -> bool:
        """True only for the ``data/`` twin dirs: ``web`` or ``theme{w}{h}``."""
        log.debug("_is_library_dir: name=%s", name)
        return name == "web" or (name.startswith("theme") and name[5:].isdigit())

    def _merge_move(self, src: Path, dst: Path) -> int:
        """Recursively move *src* into *dst*, never overwriting an existing
        *dst* file.  Emptied source dirs are removed; a dir still holding
        left-behind conflicts is kept.  Returns the number of files moved.
        """
        if not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            return sum(1 for p in dst.rglob("*") if p.is_file())

        moved = 0
        for child in sorted(src.iterdir()):
            target = dst / child.name
            if child.is_dir():
                moved += self._merge_move(child, target)
            elif target.exists():
                log.info("LibraryMigration: %s already exists — leaving old "
                         "%s in place", target, child)
            else:
                shutil.move(str(child), str(target))
                moved += 1
        try:
            src.rmdir()
        except OSError:
            pass  # not empty (left-behind conflicts) — keep it
        return moved


class ModuleThemeMove:
    """Once per user: every saved theme whose background a module draws
    moves into that module's folder -- where it is listed only while the
    module is on (the user's decision, 2026-10-21).

    Built for the Audio Visualizer becoming a module: until then a visual
    theme was saved with the ordinary ones.  Never overwrites -- a name
    already taken there becomes "Name (2)" -- and every saved path to a
    moved theme follows it (``Settings.rename_theme_path``), or the next
    start would load Theme1 in its place and drop its visual.  A theme that
    cannot move is said and left where it was.  The disk work is the theme
    store's; this decides.  Runs once (``module_themes_moved``), so no later
    start -- a one-shot CLI command's included -- rescans the user's themes.
    """

    def __init__(self, themes: ContentStore, paths: Paths, settings: Settings,
                 owner: Callable[[str], str | None]) -> None:
        log.debug("ModuleThemeMove.__init__")
        self._themes, self._paths, self._settings = themes, paths, settings
        #: The installed module that draws a background ref prefix, if any.
        self._owner = owner

    def run(self) -> int:
        """Move them; how many moved.  Never raises."""
        if self._settings.module_themes_moved():
            log.debug("ModuleThemeMove: already done")
            return 0
        moved = 0
        for folder in self._themes.user_theme_folders():
            for theme in self._themes.list(folder):
                moved += self._move(theme, folder.name)
        self._settings.set_module_themes_moved()
        log.info("ModuleThemeMove: moved %d theme(s)", moved)
        return moved

    def _move(self, theme: Theme, folder: str) -> int:
        """Move *theme* if a module draws its background; 1 if it moved."""
        found = self._themes.module_background(theme)
        module = self._owner(found[1]) if found is not None else None
        if module is None:
            if found is not None:
                log.warning("ModuleThemeMove: no installed module draws %s's "
                            "background (%s) -- left where it is",
                            theme.name, found[0])
            return 0
        old = str(theme.path)              # resolved: see user_theme_folders
        try:
            new = self._themes.move_theme(
                theme.path, self._paths.user_module_dir(module) / folder)
        except OSError as e:
            log.warning("ModuleThemeMove: %s could not move (%s) -- left where "
                        "it is", theme.path, e)
            return 0
        self._settings.rename_theme_path(old, str(new))
        log.info("ModuleThemeMove: %s -> %s (the %s module's)", old, new, module)
        return 1
