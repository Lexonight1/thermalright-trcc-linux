"""The panel's picture, shared with a window on the same machine -- not encoded.

A window shows the frame the App just sent to the panel.  Across the daemon
socket that used to mean a JPEG per frame: the App encoded it (2.0 ms on a real
320x320 video frame), base64 made it a 75 KB JSON line, and the window decoded
it again (0.85 ms) -- some 52 M instructions per frame in all, measured
2026-10-10.  Both processes run on the same machine as the same user, so the
App now writes the raw pixels into a small memory-backed file and the event
carries only which frame to read: 0.02 ms to copy on each side.

Layout of ``<frames dir>/<device>.frame``, two slots so the App never writes
the slot a window may still be reading::

    slot = header (magic, seq, width, height, stride) + stride * height bytes

The App writes slot ``seq % 2`` -- header ``seq`` zeroed first, pixels, then
``seq`` -- and only then sends the event over the socket.  A window copies the
slot and checks ``seq`` before and after; a slot overwritten meanwhile (the
window fell two frames behind) is dropped, never shown torn.  Every header is
bounds-checked, so a damaged or hostile file costs a frame, not a crash.

Only where the runtime directory is memory: a systemd session's
``XDG_RUNTIME_DIR`` (tmpfs).  macOS and root fall back to ``~/.cache`` -- a
disk -- and keep the JPEG path rather than write a frame file 15 times a
second.  The directory is 0700 and each file 0600: this user's only.
"""
from __future__ import annotations

import logging
import mmap
import os
import struct
import sys
from pathlib import Path

from .core.logs import per_frame

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)

_MAGIC = b"TRFS"
#: magic, seq, width, height, stride -- little-endian, padded to 32 bytes.
_HEADER = struct.Struct("<4sQIII")
_HEADER_SIZE = 32
_SEQ_OFFSET = 4
#: Larger than any panel TRCC drives; a header claiming more is not ours.
_MAX_EDGE = 8192
_BYTES_PER_PIXEL = 4              # ARGB32


def shared_frames_available() -> bool:
    """Whether this session's runtime directory is memory (systemd tmpfs)."""
    root = hasattr(os, "geteuid") and os.geteuid() == 0
    ok = (sys.platform.startswith("linux") and not root
          and bool(os.environ.get("XDG_RUNTIME_DIR")))
    log.debug("shared_frames_available: %s", ok)
    return ok


def frame_path(directory: Path, key: str) -> Path:
    """The file for device *key* -- ``0402:3922`` -> ``0402_3922.frame``."""
    name = "".join(c if c.isalnum() else "_" for c in key)
    frame_log.debug("frame_path: %s -> %s", key, name)
    return directory / f"{name}.frame"


def _slot_offset(seq: int, slot_size: int) -> int:
    """Where frame *seq*'s slot starts: even frames first, odd second."""
    frame_log.debug("_slot_offset: seq=%d", seq)
    return (seq % 2) * slot_size


class _Mapping:
    """One open frame file and its memory map."""

    def __init__(self, fd: int, size: int, writable: bool) -> None:
        log.debug("_Mapping: fd=%d size=%d writable=%s", fd, size, writable)
        self.fd, self.size = fd, size
        self.ino = os.fstat(fd).st_ino
        access = mmap.ACCESS_WRITE if writable else mmap.ACCESS_READ
        self.map = mmap.mmap(fd, size, access=access) if size else None

    def close(self) -> None:
        log.debug("_Mapping.close: fd=%d", self.fd)
        if self.map is not None:
            self.map.close()
        os.close(self.fd)


class FrameWriter:
    """The App's side: one writer thread, one file per device."""

    def __init__(self, directory: Path) -> None:
        log.info("FrameWriter: %s", directory)
        self._dir = directory
        self._files: dict[str, _Mapping] = {}
        self._seq: dict[str, int] = {}

    def write(self, key: str, pixels: bytes, width: int, height: int,
              stride: int) -> int:
        """Put one frame in *key*'s next slot; return its sequence number."""
        if not (0 < width <= _MAX_EDGE and 0 < height <= _MAX_EDGE
                and stride >= width * _BYTES_PER_PIXEL
                and len(pixels) >= stride * height):
            raise ValueError(f"frame {width}x{height} stride {stride} with "
                             f"{len(pixels)} bytes is not a frame")
        slot_size = _HEADER_SIZE + stride * height
        mapping = self._mapping(key, 2 * slot_size)
        assert mapping.map is not None
        seq = self._seq.get(key, 0) + 1
        self._seq[key] = seq
        at = _slot_offset(seq, slot_size)
        view = mapping.map
        view[at:at + _HEADER_SIZE] = _HEADER.pack(_MAGIC, 0, width, height,
                                                  stride).ljust(_HEADER_SIZE, b"\0")
        view[at + _HEADER_SIZE:at + slot_size] = pixels[:stride * height]
        struct.pack_into("<Q", view, at + _SEQ_OFFSET, seq)
        frame_log.debug("FrameWriter.write: %s seq=%d %dx%d", key, seq,
                        width, height)
        return seq

    def _mapping(self, key: str, size: int) -> _Mapping:
        """*key*'s file, at least *size* bytes -- created or grown as needed."""
        mapping = self._files.get(key)
        if mapping is not None and mapping.size >= size:
            return mapping
        if mapping is not None:
            mapping.close()
        self._dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._dir.chmod(0o700)          # mkdir does not change an existing one
        path = frame_path(self._dir, key)
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        os.fchmod(fd, 0o600)
        os.ftruncate(fd, size)
        log.info("FrameWriter: %s -> %s (%d bytes)", key, path, size)
        self._files[key] = mapping = _Mapping(fd, size, writable=True)
        return mapping

    def close(self) -> None:
        """Unmap and delete every frame file this writer made."""
        log.info("FrameWriter.close: %d file(s)", len(self._files))
        for key, mapping in self._files.items():
            mapping.close()
            try:
                frame_path(self._dir, key).unlink()
            except OSError:
                log.debug("FrameWriter.close: %s already gone", key)
        self._files.clear()


class FrameReader:
    """A window's side: read the slot an event names, or nothing."""

    def __init__(self, directory: Path) -> None:
        log.info("FrameReader: %s", directory)
        self._dir = directory
        self._files: dict[str, _Mapping] = {}

    def read(self, key: str, seq: int, width: int, height: int,
             stride: int) -> bytes | None:
        """Frame *seq* of *key*, or None if it is gone, damaged or overwritten."""
        # Zero is the writer's "slot being written" mark: never a frame.  The
        # shape needs no check of its own -- the slot's header must match it
        # exactly, and only the writer, which validates, writes headers.
        if seq <= 0:
            frame_log.debug("FrameReader.read: %s seq %d is not a frame", key,
                            seq)
            return None
        slot_size = _HEADER_SIZE + stride * height
        mapping = self._mapping(key, 2 * slot_size)
        if mapping is None or mapping.map is None:
            return None
        at = _slot_offset(seq, slot_size)
        view = mapping.map
        expected = (_MAGIC, seq, width, height, stride)
        if _HEADER.unpack_from(view, at) != expected:
            frame_log.debug("FrameReader.read: %s seq %d not in its slot", key,
                            seq)
            return None
        pixels = view[at + _HEADER_SIZE:at + slot_size]
        if struct.unpack_from("<Q", view, at + _SEQ_OFFSET)[0] != seq:
            frame_log.debug("FrameReader.read: %s seq %d overwritten", key, seq)
            return None
        return pixels

    def _mapping(self, key: str, size: int) -> _Mapping | None:
        """*key*'s file mapped, reopened if the App replaced or grew it."""
        mapping = self._files.get(key)
        path = frame_path(self._dir, key)
        try:
            stat = path.stat()
        except OSError:
            frame_log.debug("FrameReader: %s has no frame file", key)
            return None
        if (mapping is not None and mapping.ino == stat.st_ino
                and mapping.size >= size):
            return mapping
        if mapping is not None:
            mapping.close()
            del self._files[key]
        if stat.st_size < size:
            frame_log.debug("FrameReader: %s file smaller than its frame", key)
            return None
        try:
            fd = os.open(path, os.O_RDONLY)
        except OSError as e:
            log.warning("FrameReader: cannot open %s (%s)", path, e)
            return None
        log.info("FrameReader: mapped %s (%d bytes)", path, stat.st_size)
        self._files[key] = mapping = _Mapping(fd, stat.st_size, writable=False)
        return mapping

    def close(self) -> None:
        log.info("FrameReader.close: %d file(s)", len(self._files))
        for mapping in self._files.values():
            mapping.close()
        self._files.clear()
