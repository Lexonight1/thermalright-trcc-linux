#!/usr/bin/env python3
"""Smoke: can you SEE which device you are looking at in the mock?

Reported from ``dev/mock_gui.py`` on 2026-08-18: the device buttons look very
dark and do not highlight when selected.

THE WIDGET MATTERS, and getting it wrong wasted a diagnosis.  ``DevMockPlatform
.scan_devices()`` returns ``[]`` on purpose ("Dev rule: NO auto-handshake"), so
the sidebar's own ``uc_device`` buttons are never built — its device scroll is
hidden outright by ``VariantPanel.__init__``.  Every button visible in the mock
belongs to ``dev_console.VariantPanel``.  Measuring ``uc_device`` instead says
"works fine", because ITS buttons are checkable and do swap to the active
image; the variant panel's did neither.

Those two facts are why this measures ``VariantPanel._variant_button`` and
grabs the real widget: grabbing composites the icon, the stylesheet border and
the button frame the way a user sees them, so a border hidden under a
full-bleed icon would read as "no visible change" rather than passing.

MUTATION CHECK -- in ``dev_console._variant_button``, drop the
``icon.addPixmap(active_pix, QIcon.Mode.Normal, QIcon.State.On)`` line so only
the stylesheet accent border marks the selection.

MEASURED 2026-08-18: **all three go INVISIBLE** -- +5.4 mean brightness, 3.9%
of pixels, under both thresholds.  With the active image it is +41.3 / 31.3%.

That number is worth keeping, because it refutes a comment in the shipping GUI.
``uc_device.py`` says the active "a" image is "too subtle to tell which device
is current" and substitutes an accent border for exactly that reason.  Measured
on the real widget it is the other way round by a factor of eight: the image is
the visible signal, the border is the subtle one.

Run:  QT_QPA_PLATFORM=offscreen PYTHONPATH=src:. python3.12 dev/smoke_device_buttons.py
Gate: prints PASS and exits 0 when a selected variant is visibly different.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_DEV = Path(__file__).resolve().parent
sys.path.insert(0, str(_DEV))
sys.path.insert(0, str(_DEV.parent / "src"))
sys.path.insert(0, str(_DEV.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

# A selection you can see.  5% of pixels moving, or 8/255 of mean brightness,
# is the floor for "visibly different" — the shipped active image is roughly
# double the normal one, so a working highlight clears this by a wide margin.
_MIN_CHANGED_PCT = 5.0
_MIN_MEAN_DELTA = 8.0


def _luma(img: QImage) -> list[int]:
    img = img.convertToFormat(QImage.Format.Format_RGB32)
    out: list[int] = []
    for y in range(img.height()):
        for x in range(img.width()):
            p = img.pixel(x, y)
            out.append((((p >> 16) & 255) + ((p >> 8) & 255) + (p & 255)) // 3)
    return out


def main() -> int:
    app = QApplication(sys.argv)
    import dev_console
    from trcc.ui.gui.assets import Assets
    from trcc.ui.gui.constants import Colors
    from trcc.ui.gui.uc_device import _get_device_images

    # Build buttons without constructing the whole panel: __init__ needs a live
    # TRCCApp window (it re-parents onto uc_device and hides its scroll area).
    # Only _variant_button is under test, and it needs just the two lists.
    # QWidget subclasses need their own __new__; object.__new__ is refused.
    panel = dev_console.VariantPanel.__new__(dev_console.VariantPanel)
    panel._buttons = []
    panel._current = None

    variants = dev_console._variant_dicts()
    # One per wire, plus a text-fallback row if the catalog has one — the
    # fallback takes the other stylesheet branch and highlights differently.
    picked: list[dict] = []
    seen: set[str] = set()
    for d in variants:
        if d["protocol"] not in seen:
            seen.add(d["protocol"])
            picked.append(d)
    fallback = next(
        (d for d in variants if _get_device_images(d) == (None, None)), None)
    if fallback is not None and fallback not in picked:
        picked.append(fallback)

    print(f"Variant-panel selection visibility — {len(picked)} of "
          f"{len(variants)} variants, real widgets grabbed\n")
    print(f"  {'variant':<26} {'wire':<9} {'off':>6} {'on':>6} "
          f"{'Δmean':>7} {'Δpx%':>6}  verdict")
    print("  " + "-" * 78)

    failures: list[str] = []
    for d in picked:
        btn = dev_console.VariantPanel._variant_button(
            panel, d, _get_device_images, Assets, Colors)
        btn.resize(btn.sizeHint().width() or 140, btn.height())

        btn.setChecked(False)
        off = _luma(btn.grab().toImage())
        btn.setChecked(True)
        on = _luma(btn.grab().toImage())

        off_m = sum(off) / max(len(off), 1)
        on_m = sum(on) / max(len(on), 1)
        changed = (100.0 * sum(1 for a, b in zip(off, on, strict=True)
                              if abs(a - b) > 6)
                   / max(len(off), 1)) if len(off) == len(on) else 0.0
        visible = changed >= _MIN_CHANGED_PCT or abs(on_m - off_m) >= _MIN_MEAN_DELTA
        if not visible:
            failures.append(d["model"])
        print(f"  {d['model'][:26]:<26} {d['protocol']:<9} {off_m:6.1f} "
              f"{on_m:6.1f} {on_m - off_m:+7.1f} {changed:5.1f}%  "
              f"{'visible' if visible else '** INVISIBLE **'}")

    print()
    print(f"  thresholds: >={_MIN_CHANGED_PCT}% of pixels changed, or "
          f">={_MIN_MEAN_DELTA}/255 mean brightness")
    print()
    if failures:
        print(f"FAIL — selecting these shows no visible change: "
              f"{', '.join(failures)}")
        return 1
    print("PASS — a selected variant is visibly different from an unselected one.")
    del app
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
