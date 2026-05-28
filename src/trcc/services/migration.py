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
from pathlib import Path

from ..core.ports import Paths

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
