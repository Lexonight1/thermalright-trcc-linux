#!/usr/bin/env python3
"""Audit a decompiled Thermalright TRCC version against our Python port.

Thermalright ships updates regularly; this turns "audit the new version" into one
re-runnable command. Given the version's extracted ``.resx`` (Forms + Resources,
from ``ilspycmd -p``) and its installer (the ``Data/USBLCD`` data tree), it diffs
each dimension against our registries and reports **new / missing / dropped**:

    devices       device-button assets (A1<model>) vs core.variants button_image
    assets        every A1<model>(+hover) vs src/trcc/assets/ files
    data          installer Theme{res}/Web trees vs src/trcc/data/*.7z
    resolutions   data-tree resolutions vs core.protocol FBL_PROFILES
    panels        Form*.resx families vs our ui/gui panels (known map)

Clean programmatic diffs only — the pm/sub handshake *fingerprints* (which byte
maps to which new device) still need a C#-switch parser; that's a future add and
is called out in the report, not silently skipped.

    PYTHONPATH=src python3 dev/tools/audit_csharp.py
    PYTHONPATH=src python3 dev/tools/audit_csharp.py --resx /tmp/trcc216_proj \
        --installer "/home/ignorant/Downloads/TRCC 2.1.6-Setup/TRCC 2.1.6-Setup.exe"
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rename_assets import RENAME_MAP  # C# (Chinese) name → our English name

REPO = Path(__file__).resolve().parent.parent.parent
ASSETS = REPO / "src" / "trcc" / "assets"
DATA = REPO / "src" / "trcc" / "data"

# 2.1.6 Form (.resx) → our analogue, or None if we have no panel for it.
_PANEL_MAP = {
    "LED.FormLED": "uc_led_control / uc_screen_led",
    "LCD.FormLCD": "lcd_handler / uc_preview",
    "LCD.FormLCDImageCut": "uc_image_cut",
    "FormSystemInfo": "uc_system_info",
    "FormStart": "splash",
    "Form1": "TRCCApp (main window)",
    "DCUserControl.UCThemeSetting": "uc_theme_setting",
    "DCUserControl.UCShortcut": None,
    "KVMALED6.FormKVMALED6": "(folded into uc_led_control via KVMALEDC6→PA120)",
    "CZTV.FormCZTV": None,
    "CZTV.FormScreenshot": None,
    "CZTV.FormScreenImage": None,
    "CZTV.FormGetColor": None,
}


def _h(title: str) -> None:
    print(f"\n=== {title} ===")


# Device models we DELIBERATELY renamed in our port — not "new" if the target
# is present (see variants.py: LED PM 17-31 → PA120, stock C# shows KVMALEDC6).
_KNOWN_RENAMES = {"A1KVMALEDC6": "A1PA120 DIGITAL"}

# A1<model> hover variants are base+"a"; a device name never ends in a bare 'a'.
_HOVER = re.compile(r"A1[A-Za-z ]*\d+a$")


def _resx_device_models(resx_dir: Path) -> set[str]:
    """Real device-button base names (A1<model>) across all .resx.

    Folds hover variants (…a) back to their base — even when the base art is
    absent (e.g. A1LF17a → A1LF17) — and drops Chinese-named A1* entries, which
    are sidebar/chrome buttons (传感器=sensor, 关于=about), not devices.
    """
    names: set[str] = set()
    for rx in resx_dir.glob("*.resx"):
        names |= set(re.findall(r'<data name="(A1[^"]+)"', rx.read_text(errors="ignore")))
    devices: set[str] = set()
    for n in names:
        if not n.isascii():                      # Chinese chrome button, not a device
            continue
        if n.endswith("a") and (n[:-1] in names or _HOVER.match(n)):
            devices.add(n[:-1])                  # hover → recover base device
        else:
            devices.add(n)
    return devices


def _our_device_models() -> set[str]:
    from trcc.core.variants import _VARIANT_REGISTRY
    return {ov.button_image
            for t in _VARIANT_REGISTRY.values()
            for subs in t.values() for ov in subs.values()}


def _our_asset_stems() -> set[str]:
    return {p.stem for p in ASSETS.rglob("*")
            if p.is_file() and p.suffix.lower() in (".png", ".jpg", ".jpeg", ".ico")}


def _covered(name: str, ours: set[str]) -> bool:
    """We have this 2.1.6 asset iff its name — or its rename-mapped English
    name — is present. (Device buttons keep the C# name; chrome is renamed.)"""
    return name in ours or RENAME_MAP.get(name, "\0") in ours


def _resx_all_assets(resx_dir: Path) -> set[str]:
    """Every image-ish resource name across all .resx (UI-asset prefixes)."""
    names: set[str] = set()
    pref = re.compile(r'<data name="(A\d[^"]+|App_[^"]+|[Pp][^"]+)"')
    for rx in resx_dir.glob("*.resx"):
        names |= set(pref.findall(rx.read_text(errors="ignore")))
    return names


def _installer_resolutions(setup: Path) -> set[str]:
    out = subprocess.run(["7z", "l", "-tzip", str(setup)],
                         capture_output=True, text=True).stdout
    return set(re.findall(r"Data/USBLCD/Theme(\d+)\b", out))


def _our_data_resolutions() -> set[str]:
    return {p.stem[len("theme"):] for p in DATA.glob("theme*.7z")}


def _our_profile_resolutions() -> set[str]:
    from trcc.core.protocol import FBL_PROFILES
    return {f"{w}{h}" for p in FBL_PROFILES.values() for (w, h) in [p.resolution]}


def _show(label: str, only_new: set, only_ours: set) -> None:
    print(f"  {label}: {len(only_new)} new in 2.1.6, {len(only_ours)} only-ours")
    for n in sorted(only_new):
        print(f"    + {n}")
    for n in sorted(only_ours):
        print(f"    - {n}  (ours, not in 2.1.6)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--resx", default="/tmp/trcc216_proj")
    ap.add_argument("--installer",
                    default="/home/ignorant/Downloads/TRCC 2.1.6-Setup/TRCC 2.1.6-Setup.exe")
    a = ap.parse_args()
    resx_dir, setup = Path(a.resx), Path(a.installer)
    if not resx_dir.is_dir():
        sys.exit(f"resx dir not found: {resx_dir} (run ilspycmd -p first)")

    _h("DEVICES (by button asset)")
    new_dev = _resx_device_models(resx_dir)
    our_dev = _our_device_models()
    # Fold deliberate renames: a 2.1.6 device we renamed isn't "new".
    genuinely_new = {d for d in new_dev - our_dev
                     if _KNOWN_RENAMES.get(d, d) not in our_dev}
    _show("device models", genuinely_new, our_dev - new_dev)

    _h("ASSETS — device buttons (reliable; ours keep A1<model> names)")
    our_assets = _our_asset_stems()
    missing = sorted(m for m in new_dev if not _covered(m, our_assets))
    print(f"  device-button images missing: {len(missing)}")
    for m in missing:
        print(f"    + {m} (+ {m}a hover)")

    _h("ASSETS — chrome (converted via rename_assets.RENAME_MAP, then checked)")
    chrome = {n for n in _resx_all_assets(resx_dir) if n not in new_dev}
    chrome_missing = sorted(n for n in chrome if not _covered(n, our_assets))
    print(f"  chrome assets present in 2.1.6 but not covered: {len(chrome_missing)} "
          f"(of {len(chrome)})")
    print("  (caveat: a miss here may just be an unmapped rename, not absent art)")
    for n in chrome_missing[:40]:
        print(f"    ? {n}")
    if len(chrome_missing) > 40:
        print(f"    … +{len(chrome_missing) - 40} more")

    if setup.is_file():
        _h("DATA (per-resolution archives)")
        inst_res = _installer_resolutions(setup)
        _show("data resolutions", inst_res - _our_data_resolutions(), set())
        _h("RESOLUTIONS (vs FBL_PROFILES)")
        _show("profile resolutions", inst_res - _our_profile_resolutions(), set())
    else:
        print(f"\n(installer not found at {setup} — skipping data/resolution diff)")

    _h("PANELS (Form*.resx → our analogue)")
    forms = sorted(f.stem.replace("TRCC.", "") for f in resx_dir.glob("*.resx")
                   if "Form" in f.stem or "UC" in f.stem)
    for f in forms:
        have = _PANEL_MAP.get(f, "?")
        mark = "MISSING" if have is None else ("?" if have == "?" else "have")
        print(f"  [{mark:7}] {f:32} {have or ''}")

    _h("NOT AUTOMATED YET")
    print("  pm/sub handshake fingerprints (which byte → which new device):")
    print("  needs a parser of ADDUserButton in the .cs decompile (v2).")


if __name__ == "__main__":
    main()
