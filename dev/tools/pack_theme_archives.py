#!/usr/bin/env python3
"""Pack theme data into per-resolution .7z archives.

Creates two kinds of archives:
  src/data/Theme{W}{H}.7z      - Default themes (Theme1-Theme5)
  src/data/Web/zt{W}{H}.7z     - Cloud mask themes (000a-023e)

At runtime, the app extracts the matching archive on resolution detection.

Usage:
    python tools/pack_theme_archives.py              # pack all
    python tools/pack_theme_archives.py 320320       # single resolution
    python tools/pack_theme_archives.py --themes     # themes only
    python tools/pack_theme_archives.py --masks      # masks only
    python tools/pack_theme_archives.py --masks 320320  # single resolution masks

Requires: py7zr (pip install py7zr) or system 7z command.
"""
import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / 'src' / 'data'
WEB_DIR = DATA_DIR / 'Web'

def _discover_resolutions() -> list[str]:
    """Discover resolutions from existing Theme{res}/ and Web/zt{res}/ directories."""
    resolutions: set[str] = set()
    if DATA_DIR.exists():
        for d in DATA_DIR.iterdir():
            if d.is_dir() and d.name.startswith('Theme') and not d.name.endswith('.7z'):
                res = d.name[len('Theme'):]
                if res:
                    resolutions.add(res)
    if WEB_DIR.exists():
        for d in WEB_DIR.iterdir():
            if d.is_dir() and d.name.startswith('zt'):
                res = d.name[len('zt'):]
                if res:
                    resolutions.add(res)
    return sorted(resolutions)


def _discover_default_themes(theme_dir: Path) -> list[str]:
    """Discover Theme* subdirectories inside a resolution theme dir."""
    if not theme_dir.is_dir():
        return []
    return sorted(d.name for d in theme_dir.iterdir()
                  if d.is_dir() and d.name.startswith('Theme'))


def pack_dir_py7zr(source_dir: Path, archive_path: Path, subdirs: list[str]) -> bool:
    """Pack subdirectories using py7zr. Returns True on success."""
    try:
        import py7zr
    except ImportError:
        return False

    with py7zr.SevenZipFile(str(archive_path), 'w') as z:
        for name in subdirs:
            subdir = source_dir / name
            if not subdir.is_dir():
                continue
            for root, _, files in os.walk(subdir):
                for f in files:
                    full = Path(root) / f
                    arcname = str(full.relative_to(source_dir))
                    z.write(full, arcname)
    return True


def pack_dir_cli(source_dir: Path, archive_path: Path, subdirs: list[str]) -> bool:
    """Pack subdirectories using system 7z command. Returns True on success."""
    result = subprocess.run(
        ['7z', 'a', str(archive_path)] + subdirs,
        cwd=str(source_dir),
        capture_output=True,
    )
    return result.returncode == 0


def pack_archive(source_dir: Path, archive_path: Path, subdirs: list[str], label: str) -> bool:
    """Pack subdirs from source_dir into archive_path."""
    existing = [s for s in subdirs if (source_dir / s).is_dir()]
    if not existing:
        print(f"SKIP: {label} has no content")
        return False

    print(f"Packing {label} ({len(existing)} dirs) -> {archive_path.name} ...", end=' ')

    if archive_path.exists():
        archive_path.unlink()

    if pack_dir_py7zr(source_dir, archive_path, existing):
        size_kb = archive_path.stat().st_size / 1024
        print(f"OK ({size_kb:.0f} KB)")
        return True

    if pack_dir_cli(source_dir, archive_path, existing):
        size_kb = archive_path.stat().st_size / 1024
        print(f"OK ({size_kb:.0f} KB, 7z CLI)")
        return True

    print("FAILED (need py7zr or 7z)")
    return False


def pack_themes(resolution: str) -> bool:
    """Pack a resolution's Theme* subdirectories into .7z."""
    theme_dir = DATA_DIR / f'Theme{resolution}'
    archive = DATA_DIR / f'Theme{resolution}.7z'
    themes = _discover_default_themes(theme_dir)
    if not themes:
        print(f"SKIP: Theme{resolution}/ has no Theme* subdirs")
        return False
    return pack_archive(theme_dir, archive, themes, f'Theme{resolution}')


def pack_masks(resolution: str) -> bool:
    """Pack a resolution's cloud mask themes (zt*) into .7z."""
    masks_dir = WEB_DIR / f'zt{resolution}'
    archive = WEB_DIR / f'zt{resolution}.7z'
    if not masks_dir.is_dir():
        print(f"SKIP: zt{resolution}/ does not exist")
        return False
    subdirs = sorted(d.name for d in masks_dir.iterdir() if d.is_dir())
    return pack_archive(masks_dir, archive, subdirs, f'Web/zt{resolution}')


def main():
    args = sys.argv[1:]
    do_themes = '--themes' in args or ('--masks' not in args)
    do_masks = '--masks' in args or ('--themes' not in args)
    resolutions = [a for a in args if not a.startswith('--')]
    if not resolutions:
        resolutions = _discover_resolutions()
        if not resolutions:
            print(f"No Theme{{res}}/ or Web/zt{{res}}/ directories found under {DATA_DIR}")
            sys.exit(1)
        print(f"Discovered {len(resolutions)} resolution(s): {', '.join(resolutions)}")

    results = {}
    if do_themes:
        for r in resolutions:
            results[f'Theme{r}'] = pack_themes(r)
    if do_masks:
        for r in resolutions:
            results[f'zt{r}'] = pack_masks(r)

    print()
    ok = sum(1 for v in results.values() if v)
    print(f"Done: {ok}/{len(results)} archives created")

    if not all(results.values()):
        sys.exit(1)


if __name__ == '__main__':
    main()
