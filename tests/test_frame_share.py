"""``trcc.frame_share`` -- the panel's picture through shared memory.

What a window must never do with a shared frame: show one half-overwritten,
or crash on a file that is damaged or not ours.  Both are pinned here, without
a socket or Qt.
"""
from __future__ import annotations

import struct
from pathlib import Path

import pytest

from trcc import frame_share
from trcc.frame_share import FrameReader, FrameWriter, frame_path


def _frame(width: int, height: int, value: int) -> bytes:
    return bytes([value]) * (width * 4 * height)


def test_a_frame_round_trips(tmp_path: Path) -> None:
    writer, reader = FrameWriter(tmp_path), FrameReader(tmp_path)
    seq = writer.write("0402:3922", _frame(4, 3, 7), 4, 3, 16)
    assert reader.read("0402:3922", seq, 4, 3, 16) == _frame(4, 3, 7)
    writer.close()
    reader.close()


def test_two_slots_keep_the_frame_a_window_is_reading(tmp_path: Path) -> None:
    """The App writes the OTHER slot next, so frame N survives frame N+1."""
    writer, reader = FrameWriter(tmp_path), FrameReader(tmp_path)
    first = writer.write("k", _frame(2, 2, 1), 2, 2, 8)
    writer.write("k", _frame(2, 2, 2), 2, 2, 8)
    assert reader.read("k", first, 2, 2, 8) == _frame(2, 2, 1)
    writer.close()


def test_an_overwritten_slot_is_dropped_not_shown_torn(tmp_path: Path) -> None:
    """Two frames later the slot is someone else's: dropped, never mixed.

    MUTATION CHECK: drop the after-copy ``seq`` check in ``FrameReader.read``
    and make the overwrite land between header check and copy."""
    writer, reader = FrameWriter(tmp_path), FrameReader(tmp_path)
    first = writer.write("k", _frame(2, 2, 1), 2, 2, 8)
    writer.write("k", _frame(2, 2, 2), 2, 2, 8)
    writer.write("k", _frame(2, 2, 3), 2, 2, 8)       # reuses frame 1's slot
    assert reader.read("k", first, 2, 2, 8) is None
    writer.close()


def test_a_slot_rewritten_during_the_copy_is_dropped(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The App outruns a slow window mid-copy: the after-copy check catches it."""
    writer, reader = FrameWriter(tmp_path), FrameReader(tmp_path)
    seq = writer.write("k", _frame(2, 2, 1), 2, 2, 8)
    real_unpack = struct.unpack_from

    def overwrite_before_recheck(fmt, view, offset=0):  # type: ignore[no-untyped-def]
        # The after-copy check is the module-level unpack_from's one call:
        # the App lapping the window lands exactly between copy and check.
        if fmt == "<Q":
            writer.write("k", _frame(2, 2, 2), 2, 2, 8)
            writer.write("k", _frame(2, 2, 3), 2, 2, 8)
        return real_unpack(fmt, view, offset)

    monkeypatch.setattr(frame_share.struct, "unpack_from", overwrite_before_recheck)
    assert reader.read("k", seq, 2, 2, 8) is None
    writer.close()


def test_a_slot_being_written_is_never_read(tmp_path: Path) -> None:
    """The writer zeroes a slot's ``seq`` while it fills it.  An event naming
    seq 0 -- malformed, or hostile -- must not read that half-written slot.

    MUTATION CHECK: drop the ``seq <= 0`` refusal in ``FrameReader.read``."""
    writer, reader = FrameWriter(tmp_path), FrameReader(tmp_path)
    writer.write("k", _frame(2, 2, 1), 2, 2, 8)
    writer.write("k", _frame(2, 2, 2), 2, 2, 8)         # seq 2 -> slot 0
    path = frame_path(tmp_path, "k")
    data = bytearray(path.read_bytes())
    struct.pack_into("<Q", data, 4, 0)                   # slot 0: in progress
    path.write_bytes(bytes(data))
    assert reader.read("k", 0, 2, 2, 8) is None
    writer.close()


@pytest.mark.parametrize("seq, width, height, stride", [
    (1, 0, 2, 8), (1, 9000, 2, 36000), (1, 2, 2, 4)],
    ids=["zero-width", "too-wide", "short-stride"])
def test_a_shape_that_is_not_a_frame_reads_nothing(
        tmp_path: Path, seq: int, width: int, height: int, stride: int) -> None:
    writer, reader = FrameWriter(tmp_path), FrameReader(tmp_path)
    writer.write("k", _frame(2, 2, 1), 2, 2, 8)
    assert reader.read("k", seq, width, height, stride) is None
    writer.close()


def test_a_damaged_file_costs_a_frame_not_a_crash(tmp_path: Path) -> None:
    writer, reader = FrameWriter(tmp_path), FrameReader(tmp_path)
    seq = writer.write("k", _frame(2, 2, 1), 2, 2, 8)
    path = frame_path(tmp_path, "k")
    path.write_bytes(b"not a frame file at all")       # truncated + garbage
    assert reader.read("k", seq, 2, 2, 8) is None
    assert reader.read("absent", 1, 2, 2, 8) is None
    writer.close()


def test_a_bigger_frame_grows_the_file_and_the_reader_follows(
        tmp_path: Path) -> None:
    """Rotation or a theme change: the App grows the file, the window remaps."""
    writer, reader = FrameWriter(tmp_path), FrameReader(tmp_path)
    small = writer.write("k", _frame(2, 2, 1), 2, 2, 8)
    assert reader.read("k", small, 2, 2, 8) == _frame(2, 2, 1)
    big = writer.write("k", _frame(64, 32, 9), 64, 32, 256)
    assert reader.read("k", big, 64, 32, 256) == _frame(64, 32, 9)
    writer.close()


def test_a_file_the_app_replaced_is_read_fresh(tmp_path: Path) -> None:
    """An App restart makes a new file at the same path: a new inode."""
    reader = FrameReader(tmp_path)
    old = FrameWriter(tmp_path)
    seq = old.write("k", _frame(2, 2, 1), 2, 2, 8)
    assert reader.read("k", seq, 2, 2, 8) == _frame(2, 2, 1)
    old.close()                                      # unlinks
    new = FrameWriter(tmp_path)
    seq = new.write("k", _frame(2, 2, 5), 2, 2, 8)
    assert reader.read("k", seq, 2, 2, 8) == _frame(2, 2, 5)
    new.close()


def test_the_writer_refuses_what_is_not_a_frame(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        FrameWriter(tmp_path).write("k", b"\0" * 4, 2, 2, 8)


def test_shared_frames_only_where_the_runtime_dir_is_memory(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """macOS and root fall back to ~/.cache -- a disk -- so: JPEG there."""
    monkeypatch.setattr(frame_share.sys, "platform", "linux")
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/1000")
    monkeypatch.setattr(frame_share.os, "geteuid", lambda: 1000)
    assert frame_share.shared_frames_available()
    monkeypatch.setattr(frame_share.os, "geteuid", lambda: 0)
    assert not frame_share.shared_frames_available()
    monkeypatch.setattr(frame_share.os, "geteuid", lambda: 1000)
    monkeypatch.delenv("XDG_RUNTIME_DIR")
    assert not frame_share.shared_frames_available()
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/1000")
    monkeypatch.setattr(frame_share.sys, "platform", "darwin")
    assert not frame_share.shared_frames_available()


def test_an_existing_open_directory_is_closed_to_other_users(
        tmp_path: Path) -> None:
    """``mkdir`` leaves an existing directory's mode alone -- and the runtime
    ``trcc/`` directory is 0755 on a real machine.  Frames are this user's.

    MUTATION CHECK: drop the ``chmod`` in ``FrameWriter._mapping``."""
    frames = tmp_path / "frames"
    frames.mkdir(mode=0o755)
    frames.chmod(0o755)
    writer = FrameWriter(frames)
    writer.write("k", _frame(2, 2, 1), 2, 2, 8)
    assert oct(frames.stat().st_mode & 0o777) == "0o700"
    assert oct(frame_path(frames, "k").stat().st_mode & 0o777) == "0o600"
    writer.close()
