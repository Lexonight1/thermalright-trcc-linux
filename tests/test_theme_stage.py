"""``ThemeService.stage`` — the property the hand-rolled version bought.

A save that fails part-way must leave the PREVIOUS unit exactly as it was.
That is the whole reason the code stages into a sibling directory instead of
writing into the target, and it is the property a refactor can silently lose
while every other test stays green.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from trcc.core.errors import ThemeError
from trcc.core.models import ThemeDir
from trcc.services.theme import ThemeService


class _Paths:
    """Minimal Paths port — ``single_file_theme`` needs user_content_dir."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def user_content_dir(self) -> Path:
        return self._root


def _existing_unit(root: Path) -> Path:
    d = root / "Existing"
    d.mkdir(parents=True)
    ThemeDir(d).json.write_text(json.dumps({"name": "original"}))
    ThemeDir(d).preview.write_bytes(b"ORIGINAL-THUMB")
    return d


def test_a_clean_exit_swaps_the_unit_into_place(tmp_path: Path) -> None:
    svc = ThemeService()
    target = tmp_path / "New"

    with svc.stage(target) as staging:
        ThemeDir(staging).json.write_text(json.dumps({"name": "new"}))

    assert target.is_dir()
    assert json.loads(ThemeDir(target).json.read_text())["name"] == "new"
    assert not (tmp_path / ".New.saving").exists(), "staging dir left behind"


def test_a_failure_leaves_the_previous_unit_untouched(tmp_path: Path) -> None:
    """The load-bearing one."""
    svc = ThemeService()
    target = _existing_unit(tmp_path)

    with pytest.raises(ThemeError):
        with svc.stage(target) as staging:
            ThemeDir(staging).json.write_text(json.dumps({"name": "half"}))
            raise ThemeError("assembly blew up")

    assert target.is_dir(), "the previous unit was destroyed by a failed save"
    assert json.loads(ThemeDir(target).json.read_text())["name"] == "original"
    assert ThemeDir(target).preview.read_bytes() == b"ORIGINAL-THUMB"
    assert not (tmp_path / ".Existing.saving").exists()


def test_overwriting_replaces_every_file_not_just_the_written_ones(
    tmp_path: Path,
) -> None:
    """A swap, not a merge — a stale file from the old unit must not survive."""
    svc = ThemeService()
    target = _existing_unit(tmp_path)

    with svc.stage(target) as staging:
        ThemeDir(staging).json.write_text(json.dumps({"name": "replaced"}))

    assert json.loads(ThemeDir(target).json.read_text())["name"] == "replaced"
    assert not ThemeDir(target).preview.exists(), (
        "the old thumbnail survived — stage() merged instead of swapping"
    )


def test_an_abandoned_staging_dir_does_not_block_the_next_save(
    tmp_path: Path,
) -> None:
    """A previous crash leaves .X.saving behind; the next save must proceed."""
    svc = ThemeService()
    target = tmp_path / "Crashed"
    abandoned = tmp_path / ".Crashed.saving"
    abandoned.mkdir(parents=True)
    (abandoned / "junk").write_text("from a dead process")

    with svc.stage(target) as staging:
        ThemeDir(staging).json.write_text(json.dumps({"name": "recovered"}))

    assert json.loads(ThemeDir(target).json.read_text())["name"] == "recovered"
    assert not (target / "junk").exists(), "carried the dead run's files over"


# ── single_file_theme: the shape LoadImage and LoadVideo shared ───────────


def test_a_one_file_theme_gets_its_payload_and_a_marker(tmp_path: Path) -> None:
    svc = ThemeService(_Paths(tmp_path))
    src = tmp_path / "holiday.jpg"
    src.write_bytes(b"IMAGE-BYTES")

    with svc.single_file_theme(src, "image") as unit:
        unit.install(src, ThemeDir.BG)

    d = tmp_path / "single-image" / "holiday"
    assert ThemeDir(d).bg.read_bytes() == b"IMAGE-BYTES"
    assert json.loads(ThemeDir(d).json.read_text())["name"] == "image:holiday"


def test_a_failed_payload_leaves_no_marker_so_it_is_not_listed(
    tmp_path: Path,
) -> None:
    """The ordering that matters: marker LAST.

    A half-staged directory with a ``trcc.json`` would show up in ``theme
    list`` and load as a black canvas.  Without one, ``_has_theme_marker``
    skips it — which is why the marker is written on clean exit only.
    """
    svc = ThemeService(_Paths(tmp_path))
    src = tmp_path / "broken.mp4"
    src.write_bytes(b"x")

    with pytest.raises(ThemeError):
        with svc.single_file_theme(src, "video") as unit:
            (unit.path / "partial").write_bytes(b"half a file")
            raise ThemeError("transcode blew up")

    d = tmp_path / "single-video" / "broken"
    assert not ThemeDir(d).json.exists(), (
        "a failed stage left a marker — the broken theme will be listed"
    )


def test_installing_an_unchanged_payload_is_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Re-loading the same file must not re-copy it — both callers had their
    own copy of this size guard.

    Counts the ``copy2`` calls rather than comparing mtimes: ``shutil.copy2``
    PRESERVES the source mtime, so an mtime assertion passes whether the skip
    works or not.  The first version of this test did exactly that and stayed
    green when the guard was mutated away.
    """
    import shutil as _shutil

    from trcc.services import theme as theme_mod

    copies: list[tuple[Path, Path]] = []
    real = _shutil.copy2

    def _counting_copy2(src: Path, dst: Path, **kw: object) -> object:
        copies.append((Path(src), Path(dst)))
        return real(src, dst)

    monkeypatch.setattr(theme_mod.shutil, "copy2", _counting_copy2)

    svc = ThemeService(_Paths(tmp_path))
    src = tmp_path / "same.png"
    src.write_bytes(b"PAYLOAD")

    with svc.single_file_theme(src, "image") as unit:
        unit.install(src, ThemeDir.BG)
    assert len(copies) == 1, "the first install must actually copy"

    with svc.single_file_theme(src, "image") as unit:
        unit.install(src, ThemeDir.BG)

    assert len(copies) == 1, "unchanged payload was re-copied"


def test_a_changed_payload_is_re_copied(tmp_path: Path) -> None:
    """The other half — the skip must not make a real edit invisible."""
    svc = ThemeService(_Paths(tmp_path))
    src = tmp_path / "edited.png"
    src.write_bytes(b"BEFORE")

    with svc.single_file_theme(src, "image") as unit:
        dest = unit.install(src, ThemeDir.BG)

    src.write_bytes(b"AFTER-AND-LONGER")
    with svc.single_file_theme(src, "image") as unit:
        unit.install(src, ThemeDir.BG)

    assert dest.read_bytes() == b"AFTER-AND-LONGER"
