"""Every button image a variant NAMES must exist on disk.

``core/variants.py`` maps a (PM, SUB) handshake to a ``button_image``, and the
GUI sidebar draws that picture for the connected cooler.  Naming an image that
was never extracted does not raise — it draws a blank button, which looks like
a styling bug and is actually a missing asset.

Nothing fast checked this.  ``test_variant_presentation_audit`` covers it end to
end, but it is opt-in (``TRCC_GUI_AUDIT=1``, ~5 minutes) and so does not run on
an ordinary ``pytest tests/``.  This is the cheap always-on half: a pure table
walk with no Qt, no GUI and no hardware.

It earns itself on every Thermalright release.  TRCC 2.1.8 added six button
images across eight (PM, SUB) rows -- and three of those rows are on PMs that
already existed and merely gained a sub-variant, which is exactly the shape a
human scanning for "new devices" skips.

MUTATION CHECK -- point any row at a name that was never extracted (e.g.
``_v('A1NOPE')``) and this must fail naming that row.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from trcc.core.variants import _BULK_VARIANTS, _VARIANT_REGISTRY

_ASSETS = (Path(__file__).resolve().parents[1]
           / "src" / "trcc" / "ui" / "gui" / "assets")


def _named_images() -> set[tuple[str, str]]:
    """(button_image, where it was named) for every variant row."""
    out: set[tuple[str, str]] = set()
    for pm, subs in _BULK_VARIANTS.items():
        for sub, override in subs.items():
            if override.button_image:
                out.add((override.button_image, f"_BULK_VARIANTS pm={pm} sub={sub}"))
    for (vid, pid), table in _VARIANT_REGISTRY.items():
        for pm, subs in table.items():
            for sub, override in subs.items():
                if override.button_image:
                    out.add((override.button_image,
                             f"{vid:04x}:{pid:04x} pm={pm} sub={sub}"))
    return out


@pytest.mark.parametrize(
    ("image", "where"), sorted(_named_images()), ids=lambda v: str(v)[:40],
)
def test_every_named_button_image_exists(image: str, where: str) -> None:
    assert (_ASSETS / f"{image}.png").is_file(), (
        f"{where} names button_image {image!r}, but "
        f"src/trcc/ui/gui/assets/{image}.png does not exist — the sidebar "
        f"draws a blank button for that cooler.  Extract it with "
        f"dev/tools/extract_resx_images.py --names {image},{image}a"
    )


def test_the_walk_actually_finds_rows() -> None:
    """A table walk that silently found nothing would pass every case above."""
    found = _named_images()
    assert len(found) >= 100, (
        f"only {len(found)} variant rows named an image — the walk is looking "
        f"in the wrong place, and the parametrized test above is vacuous"
    )
