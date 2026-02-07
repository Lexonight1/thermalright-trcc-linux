#!/usr/bin/env python3
"""
Theme Downloader for TRCC Linux.

Downloads and installs theme packs similar to spaCy's model download pattern.

Usage:
    trcc download themes-320     # Download 320x320 themes
    trcc download themes-480     # Download 480x480 themes
    trcc download list           # List available theme packs
    trcc download info themes-320  # Show info about a pack

Theme packs are stored in ~/.trcc/themes/{resolution}/
"""

import hashlib
import json
import os
import shutil
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path
from typing import Dict, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# Theme pack registry - maps pack names to download info
# In production, this would be fetched from a remote registry
THEME_REGISTRY = {
    "themes-320": {
        "name": "TRCC Themes 320x320",
        "version": "1.0.0",
        "resolution": "320x320",
        "description": "125 themes for 320x320 LCD displays",
        "size_mb": 19,
        "url": "https://github.com/thermalright/trcc-linux/releases/download/themes-v1.0.0/themes-320.tar.gz",
        "sha256": None,  # Will be computed after first release
    },
    "themes-480": {
        "name": "TRCC Themes 480x480",
        "version": "1.0.0",
        "resolution": "480x480",
        "description": "Premium themes for 480x480 LCD displays",
        "size_mb": 33,
        "url": "https://github.com/thermalright/trcc-linux/releases/download/themes-v1.0.0/themes-480.tar.gz",
        "sha256": None,
    },
    "themes-240": {
        "name": "TRCC Themes 240x240",
        "version": "1.0.0",
        "resolution": "240x240",
        "description": "Compact themes for 240x240 LCD displays",
        "size_mb": 8,
        "url": "https://github.com/thermalright/trcc-linux/releases/download/themes-v1.0.0/themes-240.tar.gz",
        "sha256": None,
    },
}

# Alternative URLs for faster downloads (CDN mirrors)
MIRROR_URLS = {
    # Add CDN mirrors here when available
}

# User theme directory
def get_user_themes_dir() -> Path:
    """Get user's theme directory (~/.trcc/themes/)."""
    return Path.home() / ".trcc" / "themes"


def get_cache_dir() -> Path:
    """Get download cache directory (~/.cache/trcc/)."""
    cache = Path.home() / ".cache" / "trcc"
    cache.mkdir(parents=True, exist_ok=True)
    return cache


def get_installed_packs() -> Dict[str, dict]:
    """Get list of installed theme packs with metadata."""
    themes_dir = get_user_themes_dir()
    installed = {}

    if not themes_dir.exists():
        return installed

    for resolution_dir in themes_dir.iterdir():
        if not resolution_dir.is_dir():
            continue

        meta_file = resolution_dir / ".trcc-meta.json"
        if meta_file.exists():
            try:
                with open(meta_file) as f:
                    meta = json.load(f)
                    pack_name = meta.get("pack_name", f"themes-{resolution_dir.name}")
                    installed[pack_name] = meta
            except Exception:
                pass
        else:
            # Count themes in directory
            theme_count = sum(1 for d in resolution_dir.iterdir() if d.is_dir())
            if theme_count > 0:
                pack_name = f"themes-{resolution_dir.name.replace('x', '')}"
                installed[pack_name] = {
                    "pack_name": pack_name,
                    "resolution": resolution_dir.name,
                    "theme_count": theme_count,
                    "version": "unknown",
                }

    return installed


def list_available() -> None:
    """List available theme packs."""
    installed = get_installed_packs()

    print("Available theme packs:")
    print("=" * 60)

    for pack_id, info in THEME_REGISTRY.items():
        status = ""
        if pack_id in installed:
            inst = installed[pack_id]
            if inst.get("version") == info["version"]:
                status = " [installed]"
            else:
                status = f" [update available: {inst.get('version')} -> {info['version']}]"

        print(f"\n  {pack_id}{status}")
        print(f"    {info['description']}")
        print(f"    Resolution: {info['resolution']}, Size: ~{info['size_mb']}MB")

    print("\n" + "=" * 60)
    print("Install with: trcc download <pack-name>")
    print("Example: trcc download themes-320")


def show_info(pack_name: str) -> None:
    """Show detailed info about a theme pack."""
    if pack_name not in THEME_REGISTRY:
        print(f"Unknown theme pack: {pack_name}")
        print("Use 'trcc download list' to see available packs")
        return

    info = THEME_REGISTRY[pack_name]
    installed = get_installed_packs()

    print(f"\n{info['name']}")
    print("=" * 40)
    print(f"Pack ID:     {pack_name}")
    print(f"Version:     {info['version']}")
    print(f"Resolution:  {info['resolution']}")
    print(f"Size:        ~{info['size_mb']}MB")
    print(f"Description: {info['description']}")

    if pack_name in installed:
        inst = installed[pack_name]
        print("\nInstalled:   Yes")
        print(f"  Version:   {inst.get('version', 'unknown')}")
        print(f"  Themes:    {inst.get('theme_count', 'unknown')}")
        print(f"  Location:  {get_user_themes_dir() / info['resolution'].replace('x', '')}")
    else:
        print("\nInstalled:   No")

    print(f"\nDownload URL: {info['url']}")


def download_with_progress(url: str, dest: Path, desc: str = "Downloading") -> bool:
    """Download a file with progress display."""
    try:
        req = Request(url, headers={"User-Agent": "TRCC-Linux/1.0"})

        with urlopen(req, timeout=30) as response:
            total_size = int(response.headers.get('content-length', 0))

            with open(dest, 'wb') as f:
                downloaded = 0
                block_size = 8192

                while True:
                    chunk = response.read(block_size)
                    if not chunk:
                        break

                    f.write(chunk)
                    downloaded += len(chunk)

                    if total_size > 0:
                        percent = (downloaded / total_size) * 100
                        mb_done = downloaded / (1024 * 1024)
                        mb_total = total_size / (1024 * 1024)
                        print(f"\r{desc}: {mb_done:.1f}/{mb_total:.1f}MB ({percent:.0f}%)", end="", flush=True)
                    else:
                        mb_done = downloaded / (1024 * 1024)
                        print(f"\r{desc}: {mb_done:.1f}MB", end="", flush=True)

                print()  # Newline after progress

        return True

    except HTTPError as e:
        print(f"\nHTTP Error: {e.code} - {e.reason}")
        return False
    except URLError as e:
        print(f"\nNetwork Error: {e.reason}")
        return False
    except Exception as e:
        print(f"\nDownload Error: {e}")
        return False


def verify_checksum(filepath: Path, expected_sha256: Optional[str]) -> bool:
    """Verify file checksum."""
    if not expected_sha256:
        return True  # No checksum to verify

    sha256 = hashlib.sha256()
    with open(filepath, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            sha256.update(chunk)

    actual = sha256.hexdigest()
    if actual != expected_sha256:
        print("Checksum mismatch!")
        print(f"  Expected: {expected_sha256}")
        print(f"  Got:      {actual}")
        return False

    return True


def _is_safe_archive_member(name: str) -> bool:
    """Check that an archive member path doesn't escape the destination."""
    return not (os.path.isabs(name) or '..' in name.split('/'))


def extract_archive(archive_path: Path, dest_dir: Path) -> bool:
    """Extract tar.gz or zip archive with path traversal protection."""
    try:
        if str(archive_path).endswith('.tar.gz') or str(archive_path).endswith('.tgz'):
            with tarfile.open(archive_path, 'r:gz') as tar:
                for member in tar.getmembers():
                    if not _is_safe_archive_member(member.name):
                        print(f"Skipping unsafe path: {member.name}")
                        continue
                    tar.extract(member, dest_dir)
        elif str(archive_path).endswith('.zip'):
            with zipfile.ZipFile(archive_path, 'r') as z:
                for info in z.infolist():
                    if not _is_safe_archive_member(info.filename):
                        print(f"Skipping unsafe path: {info.filename}")
                        continue
                    z.extract(info, dest_dir)
        else:
            print(f"Unknown archive format: {archive_path}")
            return False

        return True
    except Exception as e:
        print(f"Extract error: {e}")
        return False


def download_pack(pack_name: str, force: bool = False) -> int:
    """
    Download and install a theme pack.

    Returns:
        0 on success, non-zero on failure
    """
    if pack_name not in THEME_REGISTRY:
        print(f"Unknown theme pack: {pack_name}")
        print("Use 'trcc download list' to see available packs")
        return 1

    info = THEME_REGISTRY[pack_name]
    installed = get_installed_packs()

    # Check if already installed
    if pack_name in installed and not force:
        inst = installed[pack_name]
        if inst.get("version") == info["version"]:
            print(f"{pack_name} is already installed (version {info['version']})")
            print("Use --force to reinstall")
            return 0

    print(f"Downloading {info['name']} ({info['resolution']})...")

    # Prepare paths
    cache_dir = get_cache_dir()
    archive_name = f"{pack_name}-{info['version']}.tar.gz"
    archive_path = cache_dir / archive_name

    # Download (use cache if exists and valid)
    if archive_path.exists() and verify_checksum(archive_path, info.get("sha256")):
        print(f"Using cached download: {archive_path}")
    else:
        # Try primary URL
        url = info["url"]
        if not download_with_progress(url, archive_path, f"Downloading {pack_name}"):
            # Try mirror if available
            if pack_name in MIRROR_URLS:
                print("Trying mirror...")
                if not download_with_progress(MIRROR_URLS[pack_name], archive_path, "Downloading"):
                    return 1
            else:
                return 1

        # Verify checksum
        if not verify_checksum(archive_path, info.get("sha256")):
            archive_path.unlink()
            return 1

    # Extract to temp directory first
    print("Extracting themes...")
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)

        if not extract_archive(archive_path, temp_path):
            return 1

        # Find the theme directory in extracted content
        # Could be themes-320/, Theme320320/, or directly theme folders
        extracted_themes = None
        for item in temp_path.iterdir():
            if item.is_dir():
                # Check if it contains themes (has subdirs with Theme.png or config1.dc)
                theme_count = sum(1 for d in item.iterdir()
                                if d.is_dir() and (
                                    (d / "Theme.png").exists() or
                                    (d / "config1.dc").exists() or
                                    (d / "00.png").exists()
                                ))
                if theme_count > 0:
                    extracted_themes = item
                    break

        if not extracted_themes:
            # Maybe themes are directly in temp_path
            theme_count = sum(1 for d in temp_path.iterdir()
                            if d.is_dir() and (
                                (d / "Theme.png").exists() or
                                (d / "config1.dc").exists() or
                                (d / "00.png").exists()
                            ))
            if theme_count > 0:
                extracted_themes = temp_path

        if not extracted_themes:
            print("Error: Could not find themes in archive")
            return 1

        # Install to user themes directory
        resolution = info["resolution"].replace("x", "")  # "320x320" -> "320320"
        dest_dir = get_user_themes_dir() / resolution

        # Remove existing if present
        if dest_dir.exists():
            print(f"Removing existing themes in {dest_dir}...")
            shutil.rmtree(dest_dir)

        dest_dir.mkdir(parents=True, exist_ok=True)

        # Copy themes
        theme_count = 0
        for item in extracted_themes.iterdir():
            if item.is_dir():
                shutil.copytree(item, dest_dir / item.name)
                theme_count += 1

        # Write metadata
        meta = {
            "pack_name": pack_name,
            "version": info["version"],
            "resolution": info["resolution"],
            "theme_count": theme_count,
            "installed_at": str(Path.home()),
        }
        with open(dest_dir / ".trcc-meta.json", "w") as f:
            json.dump(meta, f, indent=2)

    print(f"\n[OK] Installed {theme_count} themes to {dest_dir}")
    print("Themes are now available in TRCC GUI")
    return 0


def remove_pack(pack_name: str) -> int:
    """Remove an installed theme pack."""
    installed = get_installed_packs()

    if pack_name not in installed:
        print(f"Theme pack '{pack_name}' is not installed")
        return 1

    info = installed[pack_name]
    resolution = info.get("resolution", "").replace("x", "")
    if not resolution:
        print(f"Cannot determine resolution for {pack_name}")
        return 1

    dest_dir = get_user_themes_dir() / resolution

    if not dest_dir.exists():
        print(f"Theme directory not found: {dest_dir}")
        return 1

    print(f"Removing {pack_name} from {dest_dir}...")
    shutil.rmtree(dest_dir)
    print(f"[OK] Removed {pack_name}")
    return 0


def create_local_pack(source_dir: str, pack_name: str, resolution: str) -> int:
    """
    Create a theme pack from local directory.

    Useful for creating distributable theme packs.
    """
    source = Path(source_dir)
    if not source.exists():
        print(f"Source directory not found: {source_dir}")
        return 1

    # Count themes
    theme_count = sum(1 for d in source.iterdir()
                     if d.is_dir() and (
                         (d / "Theme.png").exists() or
                         (d / "config1.dc").exists()
                     ))

    if theme_count == 0:
        print("No valid themes found in source directory")
        return 1

    print(f"Found {theme_count} themes")

    # Create archive
    archive_name = f"{pack_name}.tar.gz"

    print(f"Creating {archive_name}...")
    with tarfile.open(archive_name, "w:gz") as tar:
        for item in source.iterdir():
            if item.is_dir():
                tar.add(item, arcname=f"{pack_name}/{item.name}")

    # Compute checksum
    sha256 = hashlib.sha256()
    with open(archive_name, 'rb') as f:
        for chunk in iter(lambda: f.read(8192), b''):
            sha256.update(chunk)

    size_mb = os.path.getsize(archive_name) / (1024 * 1024)

    print(f"\n[OK] Created {archive_name}")
    print(f"  Size:    {size_mb:.1f}MB")
    print(f"  SHA256:  {sha256.hexdigest()}")
    print(f"  Themes:  {theme_count}")
    print("\nRegistry entry:")
    print(f'    "{pack_name}": {{')
    print(f'        "name": "TRCC Themes {resolution}",')
    print('        "version": "1.0.0",')
    print(f'        "resolution": "{resolution}",')
    print(f'        "description": "{theme_count} themes for {resolution} LCD displays",')
    print(f'        "size_mb": {int(size_mb + 0.5)},')
    print(f'        "url": "https://github.com/thermalright/trcc-linux/releases/download/themes-v1.0.0/{archive_name}",')
    print(f'        "sha256": "{sha256.hexdigest()}",')
    print('    },')

    return 0


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="TRCC Theme Downloader")
    parser.add_argument("command", choices=["list", "info", "download", "remove", "create"],
                       help="Command to run")
    parser.add_argument("pack", nargs="?", help="Theme pack name")
    parser.add_argument("--force", "-f", action="store_true", help="Force reinstall")
    parser.add_argument("--source", "-s", help="Source directory (for create)")
    parser.add_argument("--resolution", "-r", help="Resolution (for create)")

    args = parser.parse_args()

    if args.command == "list":
        list_available()
    elif args.command == "info":
        if not args.pack:
            print("Usage: theme_downloader.py info <pack-name>")
            sys.exit(1)
        show_info(args.pack)
    elif args.command == "download":
        if not args.pack:
            print("Usage: theme_downloader.py download <pack-name>")
            sys.exit(1)
        sys.exit(download_pack(args.pack, force=args.force))
    elif args.command == "remove":
        if not args.pack:
            print("Usage: theme_downloader.py remove <pack-name>")
            sys.exit(1)
        sys.exit(remove_pack(args.pack))
    elif args.command == "create":
        if not args.pack or not args.source or not args.resolution:
            print("Usage: theme_downloader.py create <pack-name> --source <dir> --resolution <WxH>")
            sys.exit(1)
        sys.exit(create_local_pack(args.source, args.pack, args.resolution))
