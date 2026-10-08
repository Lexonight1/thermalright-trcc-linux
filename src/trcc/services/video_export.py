"""VideoExporter — turn an arbitrary video into a ``Theme.zt`` archive.

``Theme.zt`` is the legacy Thermalright animation container: a stream
of JPEGs with per-frame timestamps that the LCD firmware plays back at
its native rate.  Producing one from any video lets users drop in
arbitrary clips (subject to copyright!) without learning the format.

Pipeline:

1.  ``ffmpeg -ss <start> -t <duration> -r 24 -s WxH ... %04d.jpg``
    extracts JPEG frames at 24 fps.
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
import subprocess
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from ..core import toolchain
from ..core.geometry import fit_rect_for_mode
from ..core.models import (
    SUBPROCESS_NO_WINDOW,
    ZT_MAGIC,
    ZT_MAX_DURATION_MS,
    ThemeDir,
    VideoExportRequest,
)

log = logging.getLogger(__name__)


# The container's constants live in ``core.models`` — one owner for a
# format three modules touch.  See the "Theme.zt container" section there.
_FFMPEG_TIMEOUT_S = 600


class VideoExportError(RuntimeError):
    """Raised when video export fails for an actionable reason."""


ProgressCallback = Callable[[int, str], None]
"""``(percent_0_100, message)`` — UIs render this verbatim."""


class VideoExporter:
    """ffmpeg-driven ``Theme.zt`` encoder."""

    def __init__(self, install_hint: Callable[[str], str]
                 = toolchain.generic_install_hint) -> None:
        self._install_hint = install_hint
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
            self._run_ffmpeg(req, frames_dir, progress)
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

    def _run_ffmpeg(
        self,
        req: VideoExportRequest,
        frames_dir: Path,
        progress: ProgressCallback,
    ) -> None:
        log.debug("_run_ffmpeg: req=%s frames_dir=%s", req, frames_dir)
        progress(5, "Extracting frames…")
        vf, size_args = _composite_filters(req)

        cmd: list[str] = [
            toolchain.executable("ffmpeg"),
            "-ss", f"{req.start_ms / 1000.0}",
            "-t", f"{(req.end_ms - req.start_ms) / 1000.0}",
            "-i", str(req.source),
            "-y",
            "-r", str(req.fps),
            *size_args,
        ]
        if vf:
            cmd.extend(["-vf", ",".join(vf)])
        cmd.extend([
            "-f", "image2", "-q:v", "5",
            str(frames_dir / "%04d.jpg"),
        ])

        try:
            result = subprocess.run(
                cmd, capture_output=True, timeout=_FFMPEG_TIMEOUT_S,
                check=False, creationflags=SUBPROCESS_NO_WINDOW,
            )
        except subprocess.TimeoutExpired as e:
            raise VideoExportError(
                f"ffmpeg timed out after {_FFMPEG_TIMEOUT_S}s.  Try a shorter clip.",
            ) from e
        if result.returncode != 0:
            stderr_tail = result.stderr.decode(errors="replace")[-400:]
            raise VideoExportError(
                f"ffmpeg exited {result.returncode}.  Last output:\n{stderr_tail}",
            )

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


def _composite_filters(
    req: VideoExportRequest, source_wh: tuple[int, int] | None = None,
) -> tuple[list[str], list[str]]:
    """ffmpeg ``-vf`` filters (and any ``-s``) that compose *req* as the panel
    shows it: rotate, then scale/crop/pad onto the panel canvas.

    ONE chain for the export and the trimmer's preview, so what the preview
    shows is what the export encodes.  A panel size of 0 (unknown) composes
    nothing beyond the rotation.  *source_wh* is the probed size, when the
    caller already has it; otherwise it is probed here.
    """
    log.debug("_composite_filters: %s rotation=%d fit=%s -> %dx%d",
              req.source.name, req.rotation, req.fit_mode, req.target_w,
              req.target_h)
    vf: list[str] = []
    if req.rotation == 90:
        vf.append("transpose=1")
    elif req.rotation == 180:
        vf.append("transpose=1,transpose=1")
    elif req.rotation == 270:
        vf.append("transpose=2")

    # The oracle hands ffmpeg a rect derived from the SOURCE's aspect and
    # composites it onto a panel-sized canvas; a bare ``-s panel`` stretches
    # anything that is not already the panel's shape (a 1920x1080 clip came
    # out 480x480, squashed 1.78x).  ``scale``+``pad`` is that composite in
    # one filter.  Rotation stays FIRST and the probed size is swapped to
    # match, because the C# reads post-rotation dimensions
    # (``buttonXuanzhuan_Click`` swaps bitAngleW/H at 90/270).
    if req.target_w <= 0 or req.target_h <= 0:
        log.debug("_composite_filters: panel size unknown — rotation only")
        return vf, []
    if source_wh is None:
        source_wh = probe_dimensions(req.source)
    if req.rotation in (90, 270):
        source_wh = (source_wh[1], source_wh[0])
    panel = (req.target_w, req.target_h)
    if source_wh[0] > 0 and source_wh[1] > 0:
        fit = fit_rect_for_mode(source_wh, panel, req.fit_mode)
        # scale -> crop -> pad, ALWAYS all three, because the forced-axis
        # arms can overflow the canvas (a negative offset, which ``pad``
        # cannot express) while the auto arm never does.  Written as one
        # unconditional chain rather than a branch: on the auto path the
        # crop is the full rect and the pad does the letterboxing, exactly
        # as before; on a forced axis the crop takes the overflow and the
        # pad becomes the no-op.  Same filters, no arm to get wrong.
        vf.append(f"scale={fit.width}:{fit.height}")
        vf.append(
            f"crop={min(fit.width, req.target_w)}:"
            f"{min(fit.height, req.target_h)}:"
            f"{max(0, -fit.x)}:{max(0, -fit.y)}",
        )
        vf.append(
            f"pad={req.target_w}:{req.target_h}:"
            f"{max(0, fit.x)}:{max(0, fit.y)}",
        )
        size_args: list[str] = []
    else:
        # Without the source shape the aspect cannot be preserved.  Say so
        # rather than silently shipping a stretched clip.
        log.warning(
            "export_zt: source size unknown for %s — filling %dx%d, which "
            "STRETCHES a clip whose aspect differs (install ffprobe)",
            req.source, req.target_w, req.target_h,
        )
        size_args = ["-s", f"{req.target_w}x{req.target_h}"]
    return vf, size_args


def preview_command(
    req: VideoExportRequest, out_dir: Path, box: tuple[int, int],
    source_wh: tuple[int, int], *, still: bool = False,
) -> list[str]:
    """The trimmer's preview of *req*: composed as the export encodes it,
    shrunk into *box*, written as numbered JPEGs into *out_dir*.

    The C# previews this way (``UCVideoCut.cs:1507-1537``): ONE ffmpeg
    decodes the selected range at the chosen rate into numbered files, and
    its timer shows the ones already written.  *still* writes only the frame
    at ``req.start_ms`` -- the C#'s ``GetOneImage`` (:1580-1630).
    """
    log.debug("preview_command: %s %d-%d ms fps=%d box=%s still=%s",
              req.source.name, req.start_ms, req.end_ms, req.fps, box, still)
    vf, _ = _composite_filters(req, source_wh)
    vf.append(f"scale={box[0]}:{box[1]}:force_original_aspect_ratio=decrease")
    cmd = [toolchain.executable("ffmpeg"), "-v", "error",
           "-ss", f"{req.start_ms / 1000.0}"]
    if not still:
        cmd += ["-t", f"{(req.end_ms - req.start_ms) / 1000.0}"]
    cmd += ["-i", str(req.source), "-y", "-vf", ",".join(vf)]
    cmd += ["-frames:v", "1"] if still else ["-r", str(req.fps)]
    cmd += ["-q:v", "5", "-f", "image2", str(out_dir / "%05d.jpg")]
    return cmd


def _noop_progress(_percent: int, _msg: str) -> None:
    pass


def _discard(temp_dir: Path) -> None:
    """Remove an export's working directory and everything in it."""
    log.debug("_discard: %s", temp_dir)
    shutil.rmtree(temp_dir, ignore_errors=True)


def probe_dimensions(source: Path) -> tuple[int, int]:
    """Best-effort ``(width, height)`` probe via ffprobe; ``(0, 0)`` if unavailable.

    Needed to preserve the source's aspect: the fit rect is derived from the
    SOURCE shape, so without this the exporter cannot know whether a clip is
    16:9 or square and can only fill the panel — which stretches.
    """
    log.info("probe_dimensions: source=%s", source)
    if not toolchain.present("ffprobe"):
        log.warning("probe_dimensions: no ffprobe — aspect cannot be preserved")
        return (0, 0)
    cmd = [
        toolchain.executable("ffprobe"), "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "csv=p=0",
        str(source),
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, timeout=10, check=False,
            creationflags=SUBPROCESS_NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning("probe_dimensions: ffprobe failed on %s (%s)", source, e)
        return (0, 0)
    if result.returncode != 0:
        log.warning("probe_dimensions: ffprobe returned %d for %s",
                    result.returncode, source)
        return (0, 0)
    try:
        w, h = (int(v) for v in result.stdout.decode().strip().split(",")[:2])
    except ValueError:
        log.warning("probe_dimensions: unparseable ffprobe output %r",
                    result.stdout[:80])
        return (0, 0)
    log.info("probe_dimensions: %s is %dx%d", source, w, h)
    return (w, h)


def probe_duration_ms(source: Path) -> int:
    """Best-effort duration probe via ffprobe; ``0`` if unavailable.

    Used by the CLI/GUI to default ``end_ms`` to the full clip.  Caller
    is expected to fall back to a sane default if the probe returns 0
    (e.g. "5 seconds from the start").
    """
    log.info("probe_duration_ms: source=%s", source)
    if not toolchain.present("ffprobe"):
        return 0
    cmd = [
        toolchain.executable("ffprobe"), "-v", "error",
        "-show_entries", "format=duration",
        "-of", "csv=p=0",
        str(source),
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, timeout=10, check=False,
            creationflags=SUBPROCESS_NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 0
    if result.returncode != 0:
        return 0
    out = result.stdout.decode().strip()
    try:
        seconds = float(out)
    except ValueError:
        return 0
    return max(0, int(seconds * 1000))
