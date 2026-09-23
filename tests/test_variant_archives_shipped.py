"""Every library suffix the code can RETURN must have an archive we ship.

``theme_variant`` / ``background_variant`` / ``mask_variant`` pick a directory
suffix from the handshake, and ``DataInstallService`` turns that straight into
an archive name — ``theme{w}{h}{variant}.7z``, ``{w}{h}{variant}.7z``,
``zt{w}{h}{variant}.7z`` — fetched from ``src/trcc/data/`` on this repo's
``main``.  So a suffix the code can produce but the repo does not carry is a
download that 404s and a device left with an EMPTY library.

That is not hypothetical, it is the exact trap this file was written to close.
TRCC 2.1.8 added ``ThemeML360360m`` and ``GifDirectoryWebMB360360m`` and NO
``GifDirectoryWeb360360m`` — three directory families that had always agreed
finally disagreed.  Enabling one suffix for all three would have sent the
background installer after ``360360m.7z``, which does not exist in the C#
either.

MUTATION CHECK -- make ``background_variant`` return ``"m"`` for 360x360 (the
bug this guards), or delete ``src/trcc/data/theme360360m.7z``, and this fails
naming the missing archive.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from trcc.core.protocol import (
    FBL_PROFILES,
    background_variant,
    mask_variant,
    theme_variant,
)

_DATA = Path(__file__).resolve().parents[1] / "src" / "trcc" / "data"

#: Every SUB a handshake can carry.  The byte is one octet; the rules only ever
#: test small values, so the whole space is cheap to sweep and cannot miss an
#: arm someone adds later.
_SUBS = range(0, 256)


def _canvases() -> set[tuple[int, int]]:
    """Every panel geometry the catalog can resolve, both orientations.

    Derived from ``FBL_PROFILES`` rather than listed, so a resolution added to
    the catalog is swept here without anyone remembering to add it.
    """
    out: set[tuple[int, int]] = set()
    for profile in FBL_PROFILES.values():
        out.add((profile.width, profile.height))
        out.add((profile.height, profile.width))
    return out


def _required() -> set[tuple[str, str]]:
    """(archive path relative to data/, what produced it)."""
    need: set[tuple[str, str]] = set()
    for w, h in _canvases():
        for sub in _SUBS:
            t = theme_variant((w, h), sub)
            b = background_variant((w, h), sub)
            need.add((f"theme{w}{h}{t}.7z", f"theme_variant({w}x{h}, sub={sub})"))
            need.add((f"web/{w}{h}{b}.7z", f"background_variant({w}x{h}, sub={sub})"))
            for pm in (0, 3):
                m = mask_variant((w, h), sub, pm)
                need.add((f"web/zt{w}{h}{m}.7z",
                          f"mask_variant({w}x{h}, sub={sub}, pm={pm})"))
    return need


#: Canvases the project ships no artwork for at all — a SUFFIX gap is this
#: file's subject, a whole missing resolution is not.  Anchored on the
#: unsuffixed archive being absent too, so a resolution we DO ship can never
#: hide in here.
def _ships_anything(archive: str) -> bool:
    base = archive.replace(".7z", "")
    stem = base.rstrip("abcdefghijklmnopqrstuvwxyz")
    return (_DATA / f"{stem}.7z").is_file()


@pytest.mark.parametrize(
    ("archive", "produced_by"),
    sorted(a for a in _required() if _ships_anything(a[0])),
    ids=lambda v: str(v)[:44],
)
def test_every_producible_suffix_has_an_archive(
    archive: str, produced_by: str,
) -> None:
    assert (_DATA / archive).is_file(), (
        f"{produced_by} returns a suffix whose archive is not shipped: "
        f"src/trcc/data/{archive} does not exist.  The installer fetches that "
        f"name from this repo's main branch, so the device gets an EMPTY "
        f"library.  Pack it with dev/tools/pack_theme_archives.py, or stop "
        f"the rule returning that suffix."
    )


def test_the_sweep_is_not_vacuous() -> None:
    """A filter that excluded everything would make every case above pass."""
    checked = [a for a in _required() if _ships_anything(a[0])]
    assert len(checked) >= 50, (
        f"only {len(checked)} archive(s) checked — the shipped-anything filter "
        f"is excluding real resolutions and this gate proves nothing"
    )


def test_the_360x360_m_library_is_actually_carried() -> None:
    """The 2.1.8 case, pinned by name so a silent revert is visible."""
    assert theme_variant((360, 360), 0) == "m"
    assert mask_variant((360, 360), 0) == "m"
    assert background_variant((360, 360), 0) == "", (
        "backgrounds must NOT take the m suffix — the C# ships no "
        "GifDirectoryWeb360360m"
    )
    assert (_DATA / "theme360360m.7z").is_file()
    assert (_DATA / "web" / "zt360360m.7z").is_file()
    assert not (_DATA / "web" / "360360m.7z").exists(), (
        "a background archive for 360360m exists — the C# has no such library"
    )
