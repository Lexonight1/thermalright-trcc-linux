#!/usr/bin/env python
"""Migrate legacy flat-layout user themes into the per-resolution tree.

Pre-cutover ``SaveTheme`` wrote user-saved themes flat under
``~/.trcc-user/Theme1/``.  Post-cutover the canonical layout is
``~/.trcc-user/data/theme{w}{h}/Theme1/`` (matches legacy's own
``_migrate_user_content_themes`` target).  This script does that
one-shot move for any flat-layout themes still on disk so the GUI
local-theme browser picks them up.

Lives under ``tools/`` because it has a finite life: when the
``src/trcc/legacy/`` subtree is deleted, this file gets deleted with
it.  Core never imports it.

Usage
-----
    python tools/migrate_legacy_themes.py             # ~/.trcc-user/
    python tools/migrate_legacy_themes.py --dry-run   # show without moving
    python tools/migrate_legacy_themes.py \\
        --user-content-dir /alt/.trcc-user/

What it does
------------
1. Scans direct child dirs of ``user_content_dir`` that aren't
   ``data/`` (the destination subtree).
2. For each dir with a theme marker (``00.png`` / ``Theme.png`` /
   ``config1.dc`` / ``trcc.json``), reads ``00.png`` to determine
   the theme's resolution.
3. Moves ``user_content_dir / Theme1/`` →
   ``user_content_dir / data / theme{w}{h} / Theme1/``.
4. Idempotent — skips a theme whose target already exists.
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
from pathlib import Path

# Resolve PNG size via PIL when available; fall back to raw header
# parsing so the script runs in environments without Pillow.
try:
    from PIL import Image as _PIL_Image  # type: ignore[import-untyped]
    _HAS_PIL = True
except ImportError:
    _PIL_Image = None
    _HAS_PIL = False

log = logging.getLogger("migrate_legacy_themes")

_THEME_MARKERS = ("00.png", "Theme.png", "config1.dc", "trcc.json")


def _read_png_size(path: Path) -> tuple[int, int] | None:
    """Return ``(width, height)`` for a PNG, or None if unreadable.

    Uses Pillow when present; otherwise parses the 16-byte IHDR header
    directly (PNG signature 8 bytes + IHDR length+type 8 bytes + width
    big-endian uint32 + height big-endian uint32).
    """
    if _HAS_PIL and _PIL_Image is not None:
        try:
            with _PIL_Image.open(path) as img:
                return img.size
        except (OSError, ValueError) as e:
            log.warning("PIL failed to read %s: %s", path, e)
            return None
    try:
        with path.open("rb") as f:
            header = f.read(24)
        if len(header) < 24 or header[:8] != b"\x89PNG\r\n\x1a\n":
            log.warning("%s is not a PNG header", path)
            return None
        width = int.from_bytes(header[16:20], "big")
        height = int.from_bytes(header[20:24], "big")
        return width, height
    except OSError as e:
        log.warning("OS error reading %s: %s", path, e)
        return None


def _has_theme_marker(theme_dir: Path) -> bool:
    return any((theme_dir / m).is_file() for m in _THEME_MARKERS)


def _resolve_resolution(theme_dir: Path) -> tuple[int, int] | None:
    """Read ``00.png`` (or ``Theme.png`` as fallback) for size."""
    for candidate in ("00.png", "Theme.png"):
        png = theme_dir / candidate
        if png.is_file():
            size = _read_png_size(png)
            if size is not None:
                return size
    log.warning("No readable PNG in %s — cannot determine resolution",
                theme_dir)
    return None


def migrate(
    user_content_dir: Path,
    *,
    dry_run: bool = False,
) -> int:
    """Move every flat-layout theme into ``data/theme{w}{h}/``.

    Returns the exit code: 0 = ok (even if nothing to do).
    """
    if not user_content_dir.is_dir():
        log.error("user-content dir %s does not exist", user_content_dir)
        return 1

    log.info("user-content dir: %s", user_content_dir)
    log.info("dry-run:          %s", dry_run)

    data_root = user_content_dir / "data"
    candidates = [
        p for p in sorted(user_content_dir.iterdir())
        if p.is_dir() and p.name != "data" and not p.name.startswith(".")
    ]
    if not candidates:
        log.info("no flat-layout themes found — nothing to do")
        return 0

    moved = 0
    skipped = 0
    for theme_dir in candidates:
        if not _has_theme_marker(theme_dir):
            log.info("skip %s (no theme marker)", theme_dir)
            continue
        resolution = _resolve_resolution(theme_dir)
        if resolution is None:
            skipped += 1
            continue
        w, h = resolution
        target_root = data_root / f"theme{w}{h}"
        target = target_root / theme_dir.name
        if target.exists():
            log.info(
                "skip %s — target already exists at %s",
                theme_dir, target,
            )
            skipped += 1
            continue

        log.info("move %s → %s", theme_dir, target)
        if dry_run:
            moved += 1
            continue
        try:
            target_root.mkdir(parents=True, exist_ok=True)
            shutil.move(str(theme_dir), str(target))
            moved += 1
        except OSError as e:
            log.error("failed to move %s → %s: %s", theme_dir, target, e)
            skipped += 1

    log.info(
        "%s%d moved, %d skipped",
        "DRY-RUN: would have " if dry_run else "",
        moved, skipped,
    )
    return 0


def _default_user_content_dir() -> Path:
    """Match LinuxPaths.user_content_dir."""
    return Path.home() / ".trcc-user"


def main(argv: list[str] | None = None) -> int:
    description = (__doc__ or "").splitlines()[0]
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        "--user-content-dir",
        type=Path,
        default=_default_user_content_dir(),
        help="root user-content dir to scan (default: ~/.trcc-user)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="report what would move without touching the filesystem",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="enable DEBUG logging",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    return migrate(args.user_content_dir, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
