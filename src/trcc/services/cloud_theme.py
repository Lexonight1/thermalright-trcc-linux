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

  1. ``materialise(theme_id, (w, h))`` — download the MP4.  The tile's
     animated GIF (120x120, fps=8) and first-frame PNG are
     ``ensure_previews``, run by ``DownloadCloudTheme`` and by the App's
     backfill -- never on a click, where ffmpeg held the dispatch.
  2. ``PlayVideo(key, mp4_path)`` — loads MediaService playback; the
     render path then composites the video frames over whatever
     overlay (theme + mask) is already active.
"""
from __future__ import annotations

import logging
from pathlib import Path

from ..core import toolchain
from ..core.models import CloudCategory, CloudThemeEntry
from ..core.ports import CloudCatalog, Paths, VideoTool

log = logging.getLogger(__name__)


class CloudThemeService:
    """Cloud catalog reads + per-theme background staging."""

    def __init__(
        self,
        catalog: CloudCatalog,
        paths: Paths,
        *,
        video_tool: VideoTool,
    ) -> None:
        log.debug("__init__: catalog=%s paths=%s", catalog, paths)
        self._catalog = catalog
        self._paths = paths
        #: What draws the tiles (``VideoTool``) -- injected, never run here.
        self._tool = video_tool

    # ── Read-only catalog ─────────────────────────────────────────────

    def categories(self) -> tuple[CloudCategory, ...]:
        log.info("categories: called")
        return self._catalog.categories()

    def list_themes(self, category: str = "all") -> list[CloudThemeEntry]:
        log.info("list_themes: category=%s", category)
        return self._catalog.list_themes(category)

    # ── Network + materialisation ─────────────────────────────────────

    def materialise(
        self, theme_id: str, resolution: tuple[int, int],
    ) -> Path:
        """Download the cloud video and stage it FLAT alongside the
        preview thumbnails.  Returns the MP4 path.  Runs no ffmpeg: the
        thumbnails are :meth:`ensure_previews`.

        Layout (matches legacy):

            paths.cloud_theme_dir(w, h)/
                <theme_id>.mp4    ← downloaded video
                <theme_id>.png    ← extracted first-frame (ffmpeg)
                <theme_id>.gif    ← 120×120 animated thumbnail (ffmpeg)
                <other>.png       ← preview thumbnails from the 7z archive

        Idempotent — if the MP4 already exists, the download is skipped.
        It used to make the thumbnails too, so a tile click
        (``LoadCloudTheme``) whose GIF had once failed re-ran up to 40 s of
        ffmpeg inside the dispatch, every click.
        """
        log.info("materialise: %s @ %dx%d", theme_id, *resolution)
        # The catalog writes into ``cache_dir/<resolution>`` (= the device's
        # ``paths.cloud_theme_dir(w, h)``), so pass the DEVICE resolution — not
        # the catalog's construction-time default — or every theme lands in one
        # folder (320320) and no device finds its background.  Returns the exact
        # path the GUI grid scans — no duplicate copy step needed.
        mp4_target = self._catalog.download_theme(
            theme_id, f"{resolution[0]}x{resolution[1]}",
        )
        log.info("materialise: mp4 ready at %s", mp4_target)
        return mp4_target

    def ensure_previews(self, theme_id: str,
                        resolution: tuple[int, int]) -> None:
        """The tile's first-frame PNG and animated GIF, when missing.

        Best-effort: an ffmpeg failure logs and the catalog's static PNG
        stays the thumbnail -- which is all the C# ever shows
        (``FormCZTV.cs:5526``); the animated tile is ours.
        """
        target_dir = self._paths.cloud_theme_dir(*resolution)
        mp4_target = target_dir / f"{theme_id}.mp4"
        log.info("ensure_previews: %s", mp4_target)

        # First-frame static PNG (overwrites the catalog's stock thumb
        # only on first generation — legacy uses the first frame so the
        # static fallback matches the video's actual content).
        png_target = target_dir / f"{theme_id}.png"
        if not _is_first_frame_png(png_target, mp4_target):
            self._tool.still(mp4_target, png_target)

        # Animated 120×120 GIF thumbnail for the GUI tile (QMovie).
        gif_target = target_dir / f"{theme_id}.gif"
        if not gif_target.is_file():
            self._tool.tile_gif(mp4_target, gif_target)

    def backfill_previews(self, resolution: tuple[int, int]) -> int:
        """Give every downloaded cloud video at *resolution* its thumbnails.

        A video fetched while ffmpeg was missing, or whose GIF failed, kept a
        static tile forever: nothing made the GIF after the download.  Slow
        (ffmpeg per missing GIF), so the App runs it off every UI's thread;
        :meth:`ensure_previews` skips what is already there.  Returns how many
        videos were checked.
        """
        ids = self._catalog.downloaded(f"{resolution[0]}x{resolution[1]}")
        log.info("backfill_previews: %dx%d — %d downloaded video(s)",
                 *resolution, len(ids))
        if ids and not toolchain.present("ffmpeg"):
            log.info("backfill_previews: ffmpeg not installed — tiles stay "
                     "static")
            return len(ids)
        for theme_id in ids:
            self.ensure_previews(theme_id, resolution)
        return len(ids)


def _is_first_frame_png(png: Path, mp4: Path) -> bool:
    """True if *png* exists AND is newer than *mp4* — used to skip
    regeneration when the catalog already shipped a first-frame PNG
    and the MP4 hasn't been redownloaded.  Older-than-mp4 PNGs (the
    bundled catalog thumbnails from the 7z) get replaced so the
    fallback shows the actual video's content.
    """
    log.debug("_is_first_frame_png: png=%s mp4=%s", png, mp4)
    if not png.is_file() or not mp4.is_file():
        return False
    try:
        return png.stat().st_mtime >= mp4.stat().st_mtime
    except OSError:
        return False
