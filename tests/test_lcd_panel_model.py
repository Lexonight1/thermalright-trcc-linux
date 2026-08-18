"""LcdPanelModel — the per-resolution LCD preview composition (toolkit-free).

Three layers:
* the preview offsets are preserved exactly (golden regression — moving the
  table out of ``uc_preview`` into the shared model changed nothing);
* the widescreen classification is correct for known panels + rotated forms;
* the model matches the C# truth — cross-checked against the audit parser
  (``dev/tools/audit_csharp._lcd_panel_composition``) whenever the decompile is
  present, so a vendor change surfaces here instead of silently drifting.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from trcc.ui.presentation.lcd_panel import (
    _DEFAULT_OFFSET,
    _PREVIEW_OFFSETS,
    lcd_panel_for,
)

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "dev" / "tools"))
# The decompile location is NOT spelled here: it is `core.csharp.DECOMPILE_ROOT`,
# re-exported by the audit tool this test already parses with.  This file used to
# hardcode a /tmp scratch path, so the parity check below skipped itself for two
# months and read as "decompile not installed" rather than "guard is broken".
from audit_csharp import (  # noqa: E402  # pyright: ignore[reportMissingImports]
    DECOMPILE_ROOT,
    _lcd_panel_composition,
)

# C#-canonical (landscape) widescreen panels — see lcd_panel._WIDESCREEN.
_WIDE = [
    (800, 480), (854, 480), (960, 320), (960, 540),
    (1280, 480), (1600, 720), (1920, 440), (1920, 462),
]
# Standard square / portrait / bar panels.
_STD = [(240, 240), (320, 320), (480, 480), (240, 320), (640, 172), (2560, 720)]


@pytest.mark.parametrize("res", list(_PREVIEW_OFFSETS), ids=lambda r: f"{r[0]}x{r[1]}")
def test_offsets_preserved_exactly(res) -> None:
    """Every preview offset is returned verbatim (golden regression guard)."""
    assert lcd_panel_for(res).offset_info == _PREVIEW_OFFSETS[res]


def test_unknown_resolution_falls_back_to_default() -> None:
    p = lcd_panel_for((123, 456))
    assert p.offset_info == _DEFAULT_OFFSET
    assert not p.widescreen


@pytest.mark.parametrize("res", _WIDE, ids=lambda r: f"{r[0]}x{r[1]}")
def test_widescreen_panels(res) -> None:
    assert lcd_panel_for(res).widescreen


@pytest.mark.parametrize("res", _STD, ids=lambda r: f"{r[0]}x{r[1]}")
def test_standard_panels(res) -> None:
    assert not lcd_panel_for(res).widescreen


@pytest.mark.parametrize("res", _WIDE, ids=lambda r: f"{r[0]}x{r[1]}")
def test_rotated_widescreen_also_widescreen(res) -> None:
    """A portrait/rotated widescreen panel (e.g. 480x854) is still widescreen."""
    w, h = res
    assert lcd_panel_for((h, w)).widescreen


@pytest.mark.skipif(not DECOMPILE_ROOT.exists(),
                    reason=f"C# decompile not present at {DECOMPILE_ROOT} "
                           f"(run `ilspycmd -p <exe>`, or set TRCC_DECOMPILE)")
def test_model_matches_csharp_audit() -> None:
    """Each C# FormCZTVInit resolution's widescreen flag matches the model.

    MUTATION CHECK -- comment out ``(1280, 480),`` in ``lcd_panel._WIDESCREEN``.
    MEASURED 2026-08-18 against the real 2.1.6 tree: **1 failed**.  Recorded
    because this gate spent two months skipping, and a green run is not evidence
    a guard works -- only a red one on a known-bad tree is.
    """
    rows = _lcd_panel_composition(DECOMPILE_ROOT)
    assert rows, "audit parsed no LCD panel blocks"
    for res, attrs in rows.items():
        assert lcd_panel_for(res).widescreen == attrs["widescreen"], (res, attrs)
