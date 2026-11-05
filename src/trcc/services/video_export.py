"""VideoExporter — turn an arbitrary video into a ``Theme.zt`` archive.

``Theme.zt`` is the legacy Thermalright animation container: a stream
of JPEGs with per-frame timestamps that the LCD firmware plays back at
its native rate.  Producing one from any video lets users drop in
arbitrary clips (subject to copyright!) without learning the format.

Pipeline:

1.  The ``VideoTool`` (ffmpeg, ``adapters/media/ffmpeg.py``) extracts the
    clip's JPEG frames, composed for the panel.
2.  The exporter reads each JPEG, appends a header + payload to the
    output file, then deletes the temporary frame file.
3.  Progress is reported via an optional callback so the GUI can show
    a real progress bar.

This service is intentionally headless — the GUI ``VideoCropDialog``
spins a QThread that calls it, and the CLI can call it directly.
Errors raise :class:`VideoExportError` with a useful message; no
sentinels, no silent failures.
"""
from __future__ import annotations

import logging
import shutil
import struct
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from ..core import toolchain
from ..core.errors import VideoExportError
from ..core.models import (
    ZT_MAGIC,
    ZT_MAX_DURATION_MS,
    ThemeDir,
    VideoExportRequest,
)
from ..core.ports import VideoTool

log = logging.getLogger(__name__)


# The container's constants live in ``core.models`` — one owner for a
# format three modules touch.  See the "Theme.zt container" section there.


ProgressCallback = Callable[[int, str], None]
"""``(percent_0_100, message)`` — UIs render this verbatim."""


class VideoExporter:
    """``Theme.zt`` encoder: the ``VideoTool`` extracts the frames."""

    def __init__(self, install_hint: Callable[[str], str]
                 = toolchain.generic_install_hint, *, video_tool: VideoTool) -> None:
        self._install_hint = install_hint
        #: What extracts the clip's frames (``VideoTool``) -- injected, never
        #: run here; this service packs them into the ``.zt``.
        self._tool = video_tool
        if not toolchain.present("ffmpeg"):
            log.warning("VideoExporter: exports will fail — %s",
                        toolchain.missing("ffmpeg", install_hint("ffmpeg")))

    def export_zt(
        self,
        request: VideoExportRequest,
        progress: ProgressCallback | None = None,
    ) -> Path:
        """Encode *request* into a freshly-written Theme.zt; return its path.

        The output path is created inside a temporary directory so
        callers can move it into the final theme location after seeing
        success.  We never clobber an existing path — the temp dir
        guarantees uniqueness.
        """
        log.info("export_zt: source=%s start_ms=%d end_ms=%d "
                 "target=%dx%d rotation=%d",
                 request.source, request.start_ms, request.end_ms,
                 request.target_w, request.target_h, request.rotation)
        self._validate(request)
        return self._do_export(request, progress or _noop_progress)

    @contextmanager
    def baked(
        self,
        request: VideoExportRequest,
        progress: ProgressCallback | None = None,
    ) -> Iterator[Path]:
        """:meth:`export_zt` for a caller that needs the file only while it
        works -- a ``.tr`` export packs it and is done.  The Theme.zt and the
        directory it was made in are gone on exit, success or not."""
        produced = self.export_zt(request, progress)
        log.info("baked: %s held for the caller", produced)
        try:
            yield produced
        finally:
            _discard(produced.parent)

    # ── Internals ────────────────────────────────────────────────────

    def _validate(self, req: VideoExportRequest) -> None:
        log.debug("_validate: req=%s", req)
        if not toolchain.present("ffmpeg"):
            raise VideoExportError(toolchain.missing(
                "ffmpeg", self._install_hint("ffmpeg")))
        if not req.source.is_file():
            raise VideoExportError(f"Video file not found: {req.source}")
        if req.end_ms <= req.start_ms:
            raise VideoExportError(
                f"Invalid clip range {req.start_ms}-{req.end_ms} ms "
                "— end must be greater than start.",
            )
        if req.end_ms - req.start_ms > ZT_MAX_DURATION_MS:
            raise VideoExportError(
                f"Clip is {(req.end_ms - req.start_ms) / 1000:.1f}s, "
                f"max is {ZT_MAX_DURATION_MS / 1000:.0f}s.  Pick a shorter range.",
            )
        if req.fps <= 0:
            raise VideoExportError(f"Frame rate must be positive, got {req.fps}")
        if req.target_w <= 0 or req.target_h <= 0:
            raise VideoExportError(
                f"Target resolution must be positive, got "
                f"{req.target_w}x{req.target_h}",
            )
        if req.rotation not in (0, 90, 180, 270):
            raise VideoExportError(
                f"Rotation must be one of 0/90/180/270, got {req.rotation}",
            )

    def _do_export(
        self, req: VideoExportRequest, progress: ProgressCallback,
    ) -> Path:
        log.debug("_do_export: req=%s progress=%s", req, progress)
        progress(0, "Preparing export…")
        temp_dir = Path(tempfile.mkdtemp(prefix="trcc-videoexport-"))
        frames_dir = temp_dir / "frames"
        frames_dir.mkdir(parents=True, exist_ok=True)
        try:
            progress(5, "Extracting frames…")
            self._tool.extract_frames(req, frames_dir)
            jpegs = self._collect_frames(frames_dir, progress)
            output_path = ThemeDir(temp_dir).zt
            self._write_zt(output_path, jpegs, progress, req.fps)
            progress(100, "Done")
            return output_path
        except BaseException:
            # Every failure, not only the worded ones: a raise from the
            # progress callback or the frame reader left the directory behind.
            _discard(temp_dir)
            raise

    def _collect_frames(
        self, frames_dir: Path, progress: ProgressCallback,
    ) -> list[bytes]:
        log.debug("_collect_frames: frames_dir=%s progress=%s", frames_dir, progress)
        jpeg_paths = sorted(frames_dir.glob("*.jpg"))
        if not jpeg_paths:
            raise VideoExportError(
                "ffmpeg ran but produced no frames — the source clip may "
                "be empty or corrupt at the requested time range.",
            )
        total = len(jpeg_paths)
        progress(20, f"Packing {total} frames…")
        jpegs: list[bytes] = []
        for i, p in enumerate(jpeg_paths):
            jpegs.append(p.read_bytes())
            try:
                p.unlink()
            except OSError:
                pass
            if (i + 1) % 25 == 0 or i + 1 == total:
                pct = 20 + int(60 * (i + 1) / total)
                progress(pct, f"Packing {i + 1}/{total}…")
        return jpegs

    def _write_zt(
        self,
        output_path: Path,
        jpegs: list[bytes],
        progress: ProgressCallback,
        fps: int,
    ) -> None:
        log.debug("_write_zt: output_path=%s %d jpeg(s) at %d fps",
                  output_path, len(jpegs), fps)
        interval_ms = 1000.0 / fps
        progress(85, "Writing Theme.zt…")
        try:
            with output_path.open("wb") as f:
                f.write(struct.pack("B", ZT_MAGIC))
                f.write(struct.pack("<i", len(jpegs)))
                # Each frame's END time, as ``UCVideoCut.BmpToThemeFile``
                # writes it: ``(int)(1000.0 / fps * i)`` for i = 1..n,
                # never 0 (``UCVideoCut.cs:1416-1418``, :1464-1468).
                for i in range(1, len(jpegs) + 1):
                    f.write(struct.pack("<i", int(interval_ms * i)))
                for jpeg in jpegs:
                    f.write(struct.pack("<i", len(jpeg)))
                    f.write(jpeg)
        except OSError as e:
            raise VideoExportError(
                f"Could not write {output_path}: {e}",
            ) from e


def _noop_progress(_percent: int, _msg: str) -> None:
    pass


def _discard(temp_dir: Path) -> None:
    """Remove an export's working directory and everything in it."""
    log.debug("_discard: %s", temp_dir)
    shutil.rmtree(temp_dir, ignore_errors=True)
