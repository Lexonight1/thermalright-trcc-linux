"""
Cloud Theme Downloader for TRCC Linux.

Downloads themes from Thermalright cloud servers (czhorde.cc/czhorde.com).
Used by legacy gui.py for cloud theme downloads.

Windows TRCC downloads from: http://www.czhorde.com/tr/bj{resolution}/
Theme files are MP4 videos named by category:
- a001.mp4 - a020.mp4 (Gallery)
- b001.mp4 - b015.mp4 (Tech)
- c001.mp4 - c010.mp4 (HUD)
- d001.mp4 - d010.mp4 (Light)
- e001.mp4 - e010.mp4 (Nature)
- y001.mp4 - y005.mp4 (Aesthetic)

Usage:
    from trcc.cloud_downloader import CloudThemeDownloader, CATEGORIES

    downloader = CloudThemeDownloader(
        resolution="320x320",
        cache_dir="~/.trcc/cloud_themes"
    )

    # Download single theme
    result = downloader.download_theme("a001")
    if result:
        print(f"Downloaded to: {result}")

    # Download preview only
    preview_path = downloader.download_preview("a001")

    # Download all themes in category
    results = downloader.download_category("a", max_themes=20)
"""

import os
import threading
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError
from typing import Optional, List, Tuple, Callable, Dict

# Category definitions matching Windows FormCZTV.CheakWebFile
# (prefix, display_name, count)
# Counts updated to match actual server/preview availability
CATEGORIES = [
    ('all', 'All', 0),
    ('a', 'Gallery', 82),
    ('b', 'Tech', 25),
    ('c', 'HUD', 72),
    ('d', 'Light', 55),
    ('e', 'Nature', 54),
    ('y', 'Aesthetic', 10),
]

# Category name lookup
CATEGORY_NAMES = {cat[0]: cat[1] for cat in CATEGORIES}

# Server URLs (Windows pattern: http://www.czhorde.com/tr/bj{resolution}/)
SERVERS = {
    'international': 'http://www.czhorde.cc/tr/bj{resolution}/',
    'china': 'http://www.czhorde.com/tr/bj{resolution}/',
}

# Resolution URL suffixes (matches Windows FormCZTV.CLOUD_URLS)
RESOLUTION_URLS = {
    "240x240": "bj240240",
    "240x320": "bj240320",
    "320x320": "bj320320",
    "360x360": "bj360360",
    "480x480": "bj480480",
    "640x480": "bj640480",
    "480x640": "bj480640",
    "800x480": "bj800480",
    "480x800": "bj480800",
    "1280x480": "bj1280480",
    "480x1280": "bj4801280",
    "1600x720": "bj1600720",
}


def get_known_themes() -> List[str]:
    """Get list of known cloud theme IDs.

    Returns theme IDs based on Windows category patterns:
    - a001-a020 (Gallery)
    - b001-b015 (Tech)
    - c001-c010 (HUD)
    - d001-d010 (Light)
    - e001-e010 (Nature)
    - y001-y005 (Aesthetic)
    """
    themes = []
    for prefix, _, count in CATEGORIES[1:]:  # Skip 'all'
        for i in range(1, count + 1):
            themes.append(f"{prefix}{i:03d}")
    return themes


def get_themes_by_category(category: str) -> List[str]:
    """Get theme IDs for a specific category.

    Args:
        category: Category prefix ('a', 'b', 'c', 'd', 'e', 'y') or 'all'

    Returns:
        List of theme IDs in that category
    """
    if category == 'all':
        return get_known_themes()

    for prefix, _, count in CATEGORIES[1:]:
        if prefix == category:
            return [f"{prefix}{i:03d}" for i in range(1, count + 1)]

    return []


class CloudThemeDownloader:
    """
    Downloads cloud themes from Thermalright servers.

    Provides methods for:
    - Downloading single themes
    - Downloading preview images
    - Downloading by category
    - Progress callbacks

    Thread-safe for use in GUI applications.
    """

    def __init__(
        self,
        resolution: str = "320x320",
        cache_dir: Optional[str] = None,
        server: str = 'international'
    ):
        """
        Initialize cloud theme downloader.

        Args:
            resolution: LCD resolution (e.g., "320x320", "480x480")
            cache_dir: Directory to save downloaded themes (default: ~/.trcc/cloud_themes)
            server: Server to use ('international' or 'china')
        """
        self.resolution = resolution
        self.server = server

        # Set cache directory (per-resolution, matching Windows Data\USBLCD\Web\{res}\)
        if cache_dir:
            self.cache_dir = Path(cache_dir)
        else:
            res_dir = resolution.replace('x', '')
            self.cache_dir = Path.home() / ".trcc" / "cloud_themes" / res_dir

        self.cache_dir.mkdir(parents=True, exist_ok=True)

        # Build base URL
        self._update_base_url()

        # Download state
        self._lock = threading.Lock()
        self._cancelled = False

    def _update_base_url(self):
        """Update base URL based on resolution and server."""
        base = SERVERS.get(self.server, SERVERS['international'])
        res_dir = self.resolution.replace('x', '')  # "320x320" -> "320320"
        self.base_url = base.replace('{resolution}', res_dir)

    def set_resolution(self, resolution: str):
        """Change the target resolution and cache directory."""
        self.resolution = resolution
        self._update_base_url()
        # Switch to resolution-specific cache directory
        res_dir = resolution.replace('x', '')
        self.cache_dir = Path.home() / ".trcc" / "cloud_themes" / res_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def set_server(self, server: str):
        """Change the server ('international' or 'china')."""
        self.server = server
        self._update_base_url()

    def get_theme_url(self, theme_id: str) -> str:
        """Get download URL for a theme.

        Args:
            theme_id: Theme ID (e.g., 'a001') without .mp4 extension

        Returns:
            Full URL to download the theme
        """
        # Ensure we have just the ID without extension
        if theme_id.endswith('.mp4'):
            theme_id = theme_id[:-4]
        return f"{self.base_url}{theme_id}.mp4"

    def get_preview_url(self, theme_id: str) -> str:
        """Get preview image URL for a theme.

        Note: Windows uses same MP4 file, extracts frame for preview.
        Some servers may have separate preview images.

        Args:
            theme_id: Theme ID (e.g., 'a001')

        Returns:
            URL for preview (same as theme URL since previews are extracted from video)
        """
        return self.get_theme_url(theme_id)

    def get_cached_path(self, theme_id: str) -> Optional[Path]:
        """Get path to cached theme file if it exists.

        Args:
            theme_id: Theme ID

        Returns:
            Path to cached file, or None if not cached
        """
        if theme_id.endswith('.mp4'):
            theme_id = theme_id[:-4]

        mp4_path = self.cache_dir / f"{theme_id}.mp4"
        if mp4_path.exists():
            return mp4_path
        return None

    def is_cached(self, theme_id: str) -> bool:
        """Check if theme is already downloaded."""
        return self.get_cached_path(theme_id) is not None

    def download_preview_png(self, theme_id: str) -> Optional[str]:
        """Download PNG preview image for a theme from the server.

        Small file (~few KB), much faster than downloading the full MP4.

        Args:
            theme_id: Theme ID (e.g., 'a001')

        Returns:
            Path to downloaded PNG, or None if not available
        """
        if theme_id.endswith('.png'):
            theme_id = theme_id[:-4]

        dest = self.cache_dir / f"{theme_id}.png"
        if dest.exists():
            return str(dest)

        url = f"{self.base_url}{theme_id}.png"
        return self._download_file(url, dest)

    def download_theme(
        self,
        theme_id: str,
        on_progress: Optional[Callable[[int, int, int], None]] = None,
        force: bool = False
    ) -> Optional[str]:
        """
        Download a cloud theme.

        Args:
            theme_id: Theme ID (e.g., 'a001')
            on_progress: Progress callback(bytes_done, total_bytes, percent)
            force: Re-download even if cached

        Returns:
            Path to downloaded file, or None on failure
        """
        if theme_id.endswith('.mp4'):
            theme_id = theme_id[:-4]

        dest_path = self.cache_dir / f"{theme_id}.mp4"

        # Check cache
        if not force and dest_path.exists():
            return str(dest_path)

        url = self.get_theme_url(theme_id)

        try:
            return self._download_file(url, dest_path, on_progress)
        except Exception as e:
            print(f"[!] Failed to download {theme_id}: {e}")
            return None

    def download_preview(
        self,
        theme_id: str,
        on_progress: Optional[Callable[[int, int, int], None]] = None
    ) -> Optional[str]:
        """
        Download theme preview.

        For cloud themes, this downloads the MP4 and the preview
        is extracted from the first frame.

        Args:
            theme_id: Theme ID
            on_progress: Progress callback

        Returns:
            Path to downloaded file (MP4 which can be used for preview extraction)
        """
        # For cloud themes, preview = full theme file
        return self.download_theme(theme_id, on_progress)

    def download_category(
        self,
        category: str,
        max_themes: int = 0,
        on_progress: Optional[Callable[[int, int, str], None]] = None,
        force: bool = False
    ) -> Dict[str, Optional[str]]:
        """
        Download all themes in a category.

        Args:
            category: Category prefix ('a', 'b', etc.) or 'all'
            max_themes: Maximum themes to download (0 = all)
            on_progress: Progress callback(current, total, theme_id)
            force: Re-download even if cached

        Returns:
            Dict mapping theme_id to downloaded path (or None on failure)
        """
        themes = get_themes_by_category(category)
        if max_themes > 0:
            themes = themes[:max_themes]

        results = {}
        total = len(themes)

        self._cancelled = False

        for i, theme_id in enumerate(themes):
            if self._cancelled:
                break

            if on_progress:
                on_progress(i, total, theme_id)

            result = self.download_theme(theme_id, force=force)
            results[theme_id] = result

        return results

    def download_all(
        self,
        on_progress: Optional[Callable[[int, int, str], None]] = None,
        force: bool = False
    ) -> Dict[str, Optional[str]]:
        """Download all known cloud themes."""
        return self.download_category('all', on_progress=on_progress, force=force)

    def cancel(self):
        """Cancel ongoing downloads."""
        with self._lock:
            self._cancelled = True

    def _download_file(
        self,
        url: str,
        dest: Path,
        on_progress: Optional[Callable[[int, int, int], None]] = None
    ) -> Optional[str]:
        """
        Download a file with progress tracking.

        Args:
            url: URL to download
            dest: Destination path
            on_progress: Progress callback(bytes_done, total_bytes, percent)

        Returns:
            Path to downloaded file, or None on failure
        """
        try:
            req = Request(url, headers={"User-Agent": "TRCC-Linux/1.0"})

            with urlopen(req, timeout=30) as response:
                total_size = int(response.headers.get('content-length', 0))

                # Create parent directory
                dest.parent.mkdir(parents=True, exist_ok=True)

                # Download to temp file first
                temp_path = dest.with_suffix('.tmp')

                try:
                    with open(temp_path, 'wb') as f:
                        downloaded = 0
                        block_size = 8192

                        while True:
                            with self._lock:
                                if self._cancelled:
                                    raise InterruptedError("Download cancelled")

                            chunk = response.read(block_size)
                            if not chunk:
                                break

                            f.write(chunk)
                            downloaded += len(chunk)

                            if on_progress and total_size > 0:
                                percent = int((downloaded / total_size) * 100)
                                on_progress(downloaded, total_size, percent)

                    # Move temp to final destination
                    temp_path.rename(dest)
                    return str(dest)

                except Exception:
                    # Clean up temp file on error
                    if temp_path.exists():
                        temp_path.unlink()
                    raise

        except HTTPError as e:
            if e.code == 404:
                print(f"[!] Theme not found: {url}")
            else:
                print(f"[!] HTTP Error {e.code}: {url}")
            return None

        except URLError as e:
            print(f"[!] Network Error: {e.reason}")
            return None

        except InterruptedError:
            print("[!] Download cancelled")
            return None

        except Exception as e:
            print(f"[!] Download error: {e}")
            return None

    def get_all_theme_ids(self) -> List[str]:
        """Get all known theme IDs."""
        return get_known_themes()

    def get_cached_themes(self) -> List[str]:
        """Get list of cached theme IDs."""
        cached = []
        if self.cache_dir.exists():
            for f in self.cache_dir.glob("*.mp4"):
                cached.append(f.stem)
        return sorted(cached)


# Convenience function for quick downloads
def download_theme(
    theme_id: str,
    resolution: str = "320x320",
    cache_dir: Optional[str] = None
) -> Optional[str]:
    """
    Quick download of a single theme.

    Args:
        theme_id: Theme ID (e.g., 'a001')
        resolution: LCD resolution
        cache_dir: Cache directory

    Returns:
        Path to downloaded file, or None on failure
    """
    downloader = CloudThemeDownloader(resolution=resolution, cache_dir=cache_dir)
    return downloader.download_theme(theme_id)


if __name__ == "__main__":
    # Test download
    import sys

    if len(sys.argv) > 1:
        theme_id = sys.argv[1]
    else:
        theme_id = "a001"

    resolution = sys.argv[2] if len(sys.argv) > 2 else "320x320"

    print(f"Downloading {theme_id} ({resolution})...")

    def progress(done, total, pct):
        print(f"\r  {done}/{total} bytes ({pct}%)", end="", flush=True)

    result = download_theme(theme_id, resolution)
    print()

    if result:
        print(f"[OK] Downloaded to: {result}")
    else:
        print("[FAIL] Download failed")
