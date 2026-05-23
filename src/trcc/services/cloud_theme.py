"""``CloudThemeService`` — orchestrator that downloads a cloud video and
stages it as a background, matching legacy's file layout exactly.

A "cloud theme" is just a background MP4 — picking one swaps the
device's background without touching the active theme, mask, or
overlay elements.  Legacy makes the distinction explicit:

  * Cloud videos live FLAT under ``data/web/{w}{h}/`` next to their
    static PNG previews (extracted from the bundled .7z archive).
  * Picking one wraps the MP4 into a ``ThemeInfo.from_video`` shape
    and starts a 30fps animation timer that ticks frames to the LCD.

next/'s ``LoadCloudTheme`` Command turns this into:

  1. ``materialise(theme_id, (w, h))`` — download MP4 + render an
     animated GIF (120x120, fps=8) for the GUI tile + a static PNG
     for the first-frame preview.
  2. ``PlayVideo(key, mp4_path)`` — loads MediaService playback; the
     render path then composites the video frames over whatever
     overlay (theme + mask) is already active.
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from ..adapters.theme.cloud import (
    CloudCategory,
    CloudThemeEntry,
    CzhordeCatalog,
)
from ..core.ports import Paths

log = logging.getLogger(__name__)


# Hide the console window on Windows when shelling out (parity with
# the rest of the codebase that invokes subprocess).
_NO_WINDOW: int = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class CloudThemeService:
    """Cloud catalog reads + per-theme background staging."""

    def __init__(
        self,
        catalog: CzhordeCatalog,
        paths: Paths,
    ) -> None:
        self._catalog = catalog
        self._paths = paths

    # ── Read-only catalog ─────────────────────────────────────────────

    def categories(self) -> tuple[CloudCategory, ...]:
        return self._catalog.categories()

    def list_themes(self, category: str = "all") -> list[CloudThemeEntry]:
        return self._catalog.list_themes(category)

    # ── Network + materialisation ─────────────────────────────────────

    def materialise(
        self, theme_id: str, resolution: tuple[int, int],
    ) -> Path:
        """Download the cloud video and stage it FLAT alongside the
        preview thumbnails.  Returns the MP4 path.

        Layout (matches legacy):

            paths.cloud_theme_dir(w, h)/
                <theme_id>.mp4    ← downloaded video
                <theme_id>.png    ← extracted first-frame (ffmpeg)
                <theme_id>.gif    ← 120×120 animated thumbnail (ffmpeg)
                <other>.png       ← preview thumbnails from the 7z archive

        Idempotent — if the MP4 already exists, the download is
        skipped and the (png, gif) preview generation only runs when
        the targets are missing.  ffmpeg failures log a warning but
        don't fail the call; the static catalog PNG remains as a
        fallback thumbnail.
        """
        log.info("materialise: %s @ %dx%d", theme_id, *resolution)
        # The catalog is wired to write directly into
        # ``paths.cloud_theme_dir(w, h)`` (see App.__init__), so
        # ``download_theme`` returns the exact path the GUI grid
        # scans — no duplicate copy step needed.
        mp4_target = self._catalog.download_theme(theme_id)
        target_dir = mp4_target.parent
        log.info("materialise: mp4 ready at %s", mp4_target)

        # First-frame static PNG (overwrites the catalog's stock thumb
        # only on first generation — legacy uses the first frame so the
        # static fallback matches the video's actual content).
        png_target = target_dir / f"{theme_id}.png"
        if not _is_first_frame_png(png_target, mp4_target):
            _extract_first_frame_png(mp4_target, png_target)

        # Animated 120×120 GIF thumbnail for the GUI tile (QMovie).
        gif_target = target_dir / f"{theme_id}.gif"
        if not gif_target.is_file():
            _generate_animated_gif(mp4_target, gif_target)

        return mp4_target


# =========================================================================
# ffmpeg helpers — both shell-outs are best-effort; log + continue on fail
# =========================================================================


def _extract_first_frame_png(mp4: Path, png: Path) -> None:
    """Write the MP4's first frame to *png*.  Best-effort — logs a
    warning on failure and leaves any existing file in place.
    """
    log.info("materialise: extracting first-frame PNG → %s", png)
    try:
        result = subprocess.run(
            ["ffmpeg", "-i", str(mp4), "-vframes", "1", "-y", str(png)],
            capture_output=True, timeout=10,
            creationflags=_NO_WINDOW,
        )
    except FileNotFoundError:
        log.warning(
            "materialise: ffmpeg not on PATH — first-frame PNG skipped "
            "(install ffmpeg to enable rich thumbnails)",
        )
        return
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("materialise: ffmpeg first-frame failed for %s: %s: %s",
                    mp4.name, type(e).__name__, e)
        return
    if result.returncode != 0:
        log.warning(
            "materialise: ffmpeg first-frame rc=%d for %s: %s",
            result.returncode, mp4.name,
            result.stderr.decode("utf-8", errors="replace")[:200],
        )


def _generate_animated_gif(mp4: Path, gif: Path) -> None:
    """Write a 120×120 8fps animated GIF preview of *mp4* to *gif*.
    Same filter chain legacy uses (``uc_theme_web._ensure_thumb_gif``).
    """
    log.info("materialise: generating animated GIF → %s", gif)
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-i", str(mp4),
                "-vf", "scale=120:120,pad=120:120,fps=8",
                "-loop", "0", "-y", str(gif),
            ],
            capture_output=True, timeout=30,
            creationflags=_NO_WINDOW,
        )
    except FileNotFoundError:
        log.warning(
            "materialise: ffmpeg not on PATH — animated GIF skipped",
        )
        return
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("materialise: ffmpeg gif failed for %s: %s: %s",
                    mp4.name, type(e).__name__, e)
        return
    if result.returncode != 0:
        log.warning(
            "materialise: ffmpeg gif rc=%d for %s: %s",
            result.returncode, mp4.name,
            result.stderr.decode("utf-8", errors="replace")[:200],
        )


def _is_first_frame_png(png: Path, mp4: Path) -> bool:
    """True if *png* exists AND is newer than *mp4* — used to skip
    regeneration when the catalog already shipped a first-frame PNG
    and the MP4 hasn't been redownloaded.  Older-than-mp4 PNGs (the
    bundled catalog thumbnails from the 7z) get replaced so the
    fallback shows the actual video's content.
    """
    if not png.is_file() or not mp4.is_file():
        return False
    try:
        return png.stat().st_mtime >= mp4.stat().st_mtime
    except OSError:
        return False
