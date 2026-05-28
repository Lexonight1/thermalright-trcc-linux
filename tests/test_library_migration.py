"""LibraryMigration — boot-time merge-move of pre-``data/`` user content."""
from __future__ import annotations

from pathlib import Path

from trcc.services.migration import LibraryMigration

from .conftest import FakePaths


def test_migrates_old_theme_dir_into_data(tmp_path: Path) -> None:
    paths = FakePaths(tmp_path)
    old = paths.user_content_dir() / "theme320320" / "mytheme"
    old.mkdir(parents=True)
    (old / "trcc.json").write_text("{}", encoding="utf-8")

    moved = LibraryMigration(paths).run()

    assert moved == 1
    new = paths.user_data_dir() / "theme320320" / "mytheme" / "trcc.json"
    assert new.is_file()
    assert not (paths.user_content_dir() / "theme320320").exists()


def test_migrates_old_web_into_data(tmp_path: Path) -> None:
    paths = FakePaths(tmp_path)
    old = paths.user_content_dir() / "web" / "zt320320" / "m1"
    old.mkdir(parents=True)
    (old / "01.png").write_bytes(b"MASK")

    LibraryMigration(paths).run()

    new = paths.user_data_dir() / "web" / "zt320320" / "m1" / "01.png"
    assert new.read_bytes() == b"MASK"
    assert not (paths.user_content_dir() / "web").exists()


def test_leave_if_exists_does_not_clobber(tmp_path: Path) -> None:
    paths = FakePaths(tmp_path)
    old = paths.user_content_dir() / "theme320320" / "t"
    old.mkdir(parents=True)
    (old / "00.png").write_bytes(b"OLD")
    new = paths.user_data_dir() / "theme320320" / "t"
    new.mkdir(parents=True)
    (new / "00.png").write_bytes(b"NEW")

    LibraryMigration(paths).run()

    # The existing (new) file is preserved, never overwritten…
    assert (new / "00.png").read_bytes() == b"NEW"
    # …and the conflicting old file is left in place (non-destructive).
    assert (old / "00.png").read_bytes() == b"OLD"


def test_merges_non_conflicting_files(tmp_path: Path) -> None:
    paths = FakePaths(tmp_path)
    old = paths.user_content_dir() / "theme320320" / "t"
    old.mkdir(parents=True)
    (old / "00.png").write_bytes(b"BG")
    new = paths.user_data_dir() / "theme320320" / "t"
    new.mkdir(parents=True)
    (new / "01.png").write_bytes(b"MASK")

    LibraryMigration(paths).run()

    # Non-conflicting files merge into the data location; old dir emptied.
    assert (new / "00.png").read_bytes() == b"BG"
    assert (new / "01.png").read_bytes() == b"MASK"
    assert not old.exists()


def test_idempotent_second_run_is_noop(tmp_path: Path) -> None:
    paths = FakePaths(tmp_path)
    old = paths.user_content_dir() / "theme320320" / "t"
    old.mkdir(parents=True)
    (old / "00.png").write_bytes(b"BG")

    first = LibraryMigration(paths).run()
    second = LibraryMigration(paths).run()

    assert first == 1
    assert second == 0


def test_ignores_data_root_and_working_dirs(tmp_path: Path) -> None:
    paths = FakePaths(tmp_path)
    # data/ already populated, plus top-level working dirs that must stay.
    keep = paths.user_data_dir() / "theme320320" / "keep"
    keep.mkdir(parents=True)
    (keep / "trcc.json").write_text("{}", encoding="utf-8")
    working = ("uploads", "masks", "single-image", "single-video", "backgrounds")
    for name in working:
        (paths.user_content_dir() / name / "x").mkdir(parents=True)

    moved = LibraryMigration(paths).run()

    assert moved == 0
    assert (keep / "trcc.json").is_file()
    # data/ was never nested into data/data/, working dirs untouched.
    assert not (paths.user_data_dir() / "data").exists()
    for name in working:
        assert (paths.user_content_dir() / name / "x").is_dir()


def test_noop_when_user_content_dir_absent(tmp_path: Path) -> None:
    paths = FakePaths(tmp_path)
    assert not paths.user_content_dir().exists()
    assert LibraryMigration(paths).run() == 0
