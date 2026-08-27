"""MEDIA — one owner for what a background file is.

The fact "which extensions are still images" used to be spelled out in ten
places.  They had already drifted twice: the GUI file dialog omitted ``.webp``
while the validator accepted it, and the API's create-theme endpoint listed
``.gif`` while every Command downstream rejected it.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from trcc.core.models import MEDIA, MediaFormat, MediaKind

SRC = Path(__file__).resolve().parent.parent / "src" / "trcc"


def test_every_extension_resolves_to_one_kind() -> None:
    assert MEDIA.kind_of(Path("bg.png")) is MediaKind.IMAGE
    assert MEDIA.kind_of(Path("bg.mp4")) is MediaKind.ANIMATED
    assert MEDIA[".png"] is MEDIA[".PNG"]


def test_lookup_is_case_insensitive() -> None:
    """A file picker hands back whatever case the filesystem has."""
    assert MEDIA.kind_of(Path("BG.PNG")) is MediaKind.IMAGE
    assert MEDIA.kind_of(Path("clip.MP4")) is MediaKind.ANIMATED
    assert MEDIA[".JPEG"].kind is MediaKind.IMAGE


def test_an_unsupported_extension_answers_None_rather_than_raising() -> None:
    """``None`` is a real answer — callers reject and say what they accept."""
    assert MEDIA.kind_of(Path("notes.txt")) is None
    assert MEDIA.kind_of(Path("archive.tar.gz")) is None


def test_gif_is_animated_because_the_c_sharp_says_so() -> None:
    """The C# groups GIF with video in its own dialogs.

    ``FormCZTV.cs:2552`` — ``Video(*.MP4;*.AVI;*.MKV;*.MOV;*.GIF)`` — and it
    ships dedicated ``GifToJPG``/``GifTo565`` encoders.  A GIF background was
    rejected outright before this catalog; our ffmpeg decoder handled it all
    along, only the table was missing.
    """
    assert MEDIA.kind_of(Path("loop.gif")) is MediaKind.ANIMATED
    assert ".gif" in MEDIA.exts(MediaKind.ANIMATED)
    assert ".gif" not in MEDIA.exts(MediaKind.IMAGE)


def test_the_two_kinds_do_not_overlap() -> None:
    """An extension that is both would make routing order-dependent."""
    assert not MEDIA.exts(MediaKind.IMAGE) & MEDIA.exts(MediaKind.ANIMATED)
    assert (len(MEDIA.exts(MediaKind.IMAGE))
            + len(MEDIA.exts(MediaKind.ANIMATED))) == len(MEDIA)


def test_patterns_are_toolkit_neutral_globs() -> None:
    """The three file dialogs derive their filter from this."""
    assert MEDIA.patterns(MediaKind.IMAGE) == "*.bmp *.jpeg *.jpg *.png *.webp"
    both = MEDIA.patterns(MediaKind.IMAGE, MediaKind.ANIMATED)
    assert both.count("*") == len(MEDIA)
    assert "*.gif" in both


def test_webp_reaches_the_file_dialogs() -> None:
    """The drift that motivated deriving them.

    ``gui/trcc_app.py`` offered ``*.png *.jpg *.jpeg *.bmp`` — no ``.webp`` —
    while the validator accepted ``.webp``, so a loadable file could not be
    picked.  Deriving the filter makes that unrepresentable.
    """
    assert "*.webp" in MEDIA.patterns(MediaKind.IMAGE)


# ── the ratchet ──────────────────────────────────────────────────────────

_EXT_LITERAL = re.compile(r'"\.(png|jpg|jpeg|bmp|webp|mp4|mov|webm|mkv|avi|zt|gif)"')


def _modules() -> list[Path]:
    return [p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts]


def test_no_module_re_spells_the_extension_table() -> None:
    """MUTATION CHECK: paste any of the deleted frozensets back and this fails.

    A collection literal holding 3+ media extensions is the shape every one of
    the ten duplicates had.  ``core/models.py`` owns the fact; the theme
    adapter is exempt because it answers a DIFFERENT question — which
    FILENAMES a theme directory may contain (``Theme.mp4`` …), the strict
    legacy convention — and derives its extensions from those filenames.
    """
    exempt = {SRC / "core" / "models.py", SRC / "adapters" / "theme" / "filesystem.py"}
    offenders: list[str] = []
    for path in _modules():
        if path in exempt:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Set, ast.List, ast.Tuple)):
                continue
            exts = [e.value for e in node.elts
                    if isinstance(e, ast.Constant) and isinstance(e.value, str)
                    and _EXT_LITERAL.fullmatch(f'"{e.value}"')]
            if len(exts) >= 3:
                offenders.append(
                    f"{path.relative_to(SRC)}:{node.lineno} {sorted(exts)}")
    assert not offenders, (
        "these re-spell what core.models.MEDIA owns — use "
        "MEDIA.exts(MediaKind.X) / MEDIA.kind_of(path):\n  "
        + "\n  ".join(offenders)
    )


@pytest.mark.parametrize("kind", list(MediaKind))
def test_every_kind_has_at_least_one_format(kind: MediaKind) -> None:
    """A kind nothing maps to would silently reject every file of it."""
    assert MEDIA.exts(kind), f"{kind} has no formats"


def test_catalog_is_a_mapping_over_its_extensions() -> None:
    assert set(MEDIA) == {f.ext for f in MEDIA.values()}
    assert all(isinstance(f, MediaFormat) for f in MEDIA.values())
    assert ".png" in MEDIA and ".xyz" not in MEDIA
