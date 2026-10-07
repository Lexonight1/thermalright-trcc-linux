"""VideoExportRunner implementations — the execution behind a ``.zt`` encode.

``ThreadVideoExportRunner`` drains a queue of submitted clips on one daemon
thread (production).  ``SyncVideoExportRunner`` encodes inline so tests stay
deterministic — no threads, no sleeps, no ffmpeg timing.

Both are injected at the composition root; the Command that submits a clip
never names a thread.  Mirrors ``data_install_runner.py`` and
``send_scheduler.py``, the other background workers in this package.
"""
from __future__ import annotations

import logging
import shutil
from collections.abc import Callable
from pathlib import Path

from ...core import toolchain
from ...core.events import EventBus, VideoExportFinished, VideoExportProgress
from ...core.models import VideoExportRequest
from ...core.ports import ContentStore, VideoExportRunner
from ._worker import QueueWorker

log = logging.getLogger(__name__)

#: (token, request) — one queued encode.
_Job = tuple[str, VideoExportRequest]


class _ProgressPublisher:
    """Turns the exporter's callback into bus events for one token.

    A named object rather than a closure so the token it carries is
    inspectable when a subscriber misbehaves, and so the exporter's
    ``(percent, message)`` contract stays the only thing it knows about.
    """

    __slots__ = ("_events", "_token")

    def __init__(self, events: EventBus, token: str) -> None:
        log.debug("_ProgressPublisher.__init__: token=%s", token)
        self._events = events
        self._token = token

    def __call__(self, percent: int, message: str) -> None:
        log.debug("progress: token=%s %d%% %s", self._token, percent, message)
        self._events.publish(VideoExportProgress(
            token=self._token, percent=percent, message=message,
        ))

    def __repr__(self) -> str:
        return f"<progress publisher for {self._token}>"


def _keep_in_library(
    library: ContentStore, produced: Path, request: VideoExportRequest,
) -> Path:
    """Move a finished encode into the user's background library.

    The clip stayed in the exporter's ``/tmp`` directory, and every UI decided
    for itself what to do with it: gui copied it somewhere that survives a
    reboot (#271), qtgui and the API handed the temp path on, and nothing ever
    removed the directory.  Stored here, the path is stable for every UI, an
    identical clip is kept once, and the temp directory goes -- the same move
    ``SingleFileTheme.adopt`` makes for ``LoadVideo``.
    """
    from ...services.video_export import VideoExportError
    try:
        ref = library.store_background(produced.read_bytes(), produced.suffix,
                                       request.target_w, request.target_h)
    finally:
        shutil.rmtree(produced.parent, ignore_errors=True)
    kept = library.resolve_ref(ref)
    if kept is None:
        raise VideoExportError(f"Stored the clip as {ref}, but it did not "
                               "resolve in the background library")
    log.info("_keep_in_library: %s kept as %s", produced.name, kept)
    return kept


def _export_and_publish(
    events: EventBus, library: ContentStore, token: str,
    request: VideoExportRequest,
    install_hint: Callable[[str], str] = toolchain.generic_install_hint,
) -> bool:
    """Encode one clip and announce the outcome.  Never raises.

    The worker thread outlives any single export, so an exception here
    would kill every queued clip behind it.  Failure is published as
    ``VideoExportFinished(ok=False)`` instead — which is the only way a
    caller can hear about it anyway, since it submitted and returned long
    before ffmpeg started.
    """
    log.info("_export_and_publish: token=%s source=%s %d-%dms target=%dx%d "
             "rotation=%d", token, request.source, request.start_ms,
             request.end_ms, request.target_w, request.target_h,
             request.rotation)
    # Imported here, not at module scope: the exporter warns about a missing
    # ffmpeg on construction, and a headless run that never exports anything
    # should not pay for that probe.
    from ...services.video_export import VideoExporter, VideoExportError
    try:
        path = _keep_in_library(library, VideoExporter(install_hint).export_zt(
            request, _ProgressPublisher(events, token),
        ), request)
    except VideoExportError as e:
        # Actionable by contract — the exporter words these for a user.
        log.warning("_export_and_publish: token=%s failed — %s", token, e)
        events.publish(VideoExportFinished(
            token=token, ok=False, message=str(e),
        ))
        return False
    except Exception as e:
        log.exception("_export_and_publish: token=%s raised unexpectedly",
                      token)
        events.publish(VideoExportFinished(
            token=token, ok=False,
            message=f"Video export failed unexpectedly: {e}",
        ))
        return False
    log.info("_export_and_publish: token=%s wrote %s", token, path)
    events.publish(VideoExportFinished(
        token=token, ok=True, path=str(path),
        message=f"Exported {request.source.name} to {path.name}",
    ))
    return True


class ThreadVideoExportRunner(VideoExportRunner):
    """One daemon thread draining submitted clips — production."""

    def __init__(
        self,
        events: EventBus,
        library: ContentStore,
        *,
        join_timeout: float = 2.0,
        install_hint: Callable[[str], str] = toolchain.generic_install_hint,
    ) -> None:
        log.info("ThreadVideoExportRunner.__init__: join_timeout=%.1fs",
                 join_timeout)
        self._events = events
        self._library = library
        self._install_hint = install_hint
        self._worker: QueueWorker[_Job] = QueueWorker(
            "trcc-video-export", self._export,
            stall_hint="mid-encode", join_timeout=join_timeout,
        )

    def submit(self, token: str, request: VideoExportRequest) -> None:
        log.info("submit: token=%s source=%s", token, request.source)
        self._worker.submit((token, request))

    def _export(self, job: _Job) -> None:
        """Unpack one queued job for the worker — the seam it calls back on."""
        log.debug("_export: token=%s source=%s", job[0], job[1].source)
        _export_and_publish(self._events, self._library, *job,
                            install_hint=self._install_hint)

    def shutdown(self) -> None:
        log.info("shutdown: stopping video-export worker")
        self._worker.shutdown()


class SyncVideoExportRunner(VideoExportRunner):
    """Encodes inline on the caller's thread — deterministic tests."""

    def __init__(self, events: EventBus, library: ContentStore, *,
                 install_hint: Callable[[str], str]
                 = toolchain.generic_install_hint) -> None:
        log.info("SyncVideoExportRunner.__init__")
        self._events = events
        self._library = library
        self._install_hint = install_hint

    def submit(self, token: str, request: VideoExportRequest) -> None:
        log.info("submit: token=%s source=%s (inline)", token, request.source)
        _export_and_publish(self._events, self._library, token, request,
                            install_hint=self._install_hint)

    def shutdown(self) -> None:
        log.info("shutdown: nothing to stop (inline runner)")
