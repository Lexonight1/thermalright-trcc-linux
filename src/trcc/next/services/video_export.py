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
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


_ZT_MAGIC = 0xDC
_EXPORT_FPS = 24
_FRAME_INTERVAL_MS = 1000.0 / _EXPORT_FPS
_MAX_DURATION_MS = 300_000  # 5 minutes — matches legacy soft cap.
_FFMPEG_TIMEOUT_S = 600


class VideoExportError(RuntimeError):
    """Raised when video export fails for an actionable reason."""


ProgressCallback = Callable[[int, str], None]
"""``(percent_0_100, message)`` — UIs render this verbatim."""


@dataclass(frozen=True)
class VideoExportRequest:
    """Inputs for :meth:`VideoExporter.export_zt`.

    ``start_ms`` / ``end_ms`` clip the source video; ``rotation`` is
    multiples of 90.  ``target_w/h`` are the device's native pixels —
    we fit-resize to those exactly (the firmware doesn't crop).
    """
    source: Path
    start_ms: int
    end_ms: int
    target_w: int
    target_h: int
    rotation: int = 0


class VideoExporter:
    """ffmpeg-driven ``Theme.zt`` encoder."""

    def __init__(self) -> None:
        if shutil.which("ffmpeg") is None:
            log.warning(
                "VideoExporter: ffmpeg not on PATH — exports will fail until "
                "you install it (e.g. 'dnf install ffmpeg' / 'apt install ffmpeg').",
            )

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
        self._validate(request)
        return self._do_export(request, progress or _noop_progress)

    # ── Internals ────────────────────────────────────────────────────

    def _validate(self, req: VideoExportRequest) -> None:
        if shutil.which("ffmpeg") is None:
            raise VideoExportError(
                "ffmpeg not found on PATH.  Install it via your package "
                "manager (e.g. 'dnf install ffmpeg' / 'apt install ffmpeg').",
            )
        if not req.source.is_file():
            raise VideoExportError(f"Video file not found: {req.source}")
        if req.end_ms <= req.start_ms:
            raise VideoExportError(
                f"Invalid clip range {req.start_ms}-{req.end_ms} ms "
                "— end must be greater than start.",
            )
        if req.end_ms - req.start_ms > _MAX_DURATION_MS:
            raise VideoExportError(
                f"Clip is {(req.end_ms - req.start_ms) / 1000:.1f}s, "
                f"max is {_MAX_DURATION_MS / 1000:.0f}s.  Pick a shorter range.",
            )
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
        progress(0, "Preparing export…")
        temp_dir = Path(tempfile.mkdtemp(prefix="trcc-next-videoexport-"))
        frames_dir = temp_dir / "frames"
        frames_dir.mkdir(parents=True, exist_ok=True)
        try:
            self._run_ffmpeg(req, frames_dir, progress)
            jpegs = self._collect_frames(frames_dir, progress)
            output_path = temp_dir / "Theme.zt"
            self._write_zt(output_path, jpegs, progress)
            progress(100, "Done")
            return output_path
        except VideoExportError:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise

    def _run_ffmpeg(
        self,
        req: VideoExportRequest,
        frames_dir: Path,
        progress: ProgressCallback,
    ) -> None:
        progress(5, "Extracting frames…")
        vf: list[str] = []
        if req.rotation == 90:
            vf.append("transpose=1")
        elif req.rotation == 180:
            vf.append("transpose=1,transpose=1")
        elif req.rotation == 270:
            vf.append("transpose=2")

        cmd: list[str] = [
            "ffmpeg",
            "-ss", f"{req.start_ms / 1000.0}",
            "-t", f"{(req.end_ms - req.start_ms) / 1000.0}",
            "-i", str(req.source),
            "-y",
            "-r", str(_EXPORT_FPS),
            "-s", f"{req.target_w}x{req.target_h}",
        ]
        if vf:
            cmd.extend(["-vf", ",".join(vf)])
        cmd.extend([
            "-f", "image2", "-q:v", "5",
            str(frames_dir / "%04d.jpg"),
        ])

        try:
            result = subprocess.run(
                cmd, capture_output=True, timeout=_FFMPEG_TIMEOUT_S, check=False,
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
    ) -> None:
        progress(85, "Writing Theme.zt…")
        try:
            with output_path.open("wb") as f:
                f.write(struct.pack("B", _ZT_MAGIC))
                f.write(struct.pack("<i", len(jpegs)))
                for i in range(len(jpegs)):
                    f.write(struct.pack("<i", int(i * _FRAME_INTERVAL_MS)))
                for jpeg in jpegs:
                    f.write(struct.pack("<i", len(jpeg)))
                    f.write(jpeg)
        except OSError as e:
            raise VideoExportError(
                f"Could not write {output_path}: {e}",
            ) from e


def _noop_progress(_percent: int, _msg: str) -> None:
    pass


def probe_duration_ms(source: Path) -> int:
    """Best-effort duration probe via ffprobe; ``0`` if unavailable.

    Used by the CLI/GUI to default ``end_ms`` to the full clip.  Caller
    is expected to fall back to a sane default if the probe returns 0
    (e.g. "5 seconds from the start").
    """
    if shutil.which("ffprobe") is None:
        return 0
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "csv=p=0",
        str(source),
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, timeout=10, check=False,
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


# Constants exported for downstream callers (CLI, GUI labels) so the
# limits aren't re-defined in two places.
MAX_DURATION_MS = _MAX_DURATION_MS
EXPORT_FPS = _EXPORT_FPS
