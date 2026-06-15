#!/usr/bin/env python3
"""Audit a decompiled Thermalright TRCC version against our Python port.

Thermalright ships updates regularly; this turns "audit the new version" into one
re-runnable command that tells you exactly what's new and what to pull. Inputs:

    --resx       extracted .resx (Forms + Resources, from ``ilspycmd -p <exe>``)
    --installer  the installer .exe (its ``Data/USBLCD`` data tree, read via 7z)
    --cs         the single-file decompile (``ilspycmd <exe>``) for the
                 resolution-fingerprint parser

It diffs each dimension against our registries and reports new / missing:

    devices       device-button models (A1<model>) vs core.variants button_image
    assets        device buttons + chrome vs src/trcc/assets/ files
    data          installer Theme{res} archives vs src/trcc/data/*.7z
    resolutions   the C# ``is{W}x{H}`` universe + the (mode,pm,sub,fbl) handshake
                  fingerprint that selects each (parsed from ``FormCZTVInit`` /
                  ``AddhidDeviceList``) vs our RESOLVED device catalog
    panels        Form*.resx families vs our ui/gui panels (known map)

…then a WHAT TO PULL checklist: the exact extract/pack commands + variant rows
for every genuinely-new device, resolution, and asset.  The tool REPORTS;
device data still gets dev-console validation before it lands in variants.py.

    PYTHONPATH=src python3 dev/tools/audit_csharp.py
    PYTHONPATH=src python3 dev/tools/audit_csharp.py --resx /tmp/trcc216_proj \
        --installer "/home/ignorant/Downloads/TRCC 2.1.6-Setup/TRCC 2.1.6-Setup.exe" \
        --cs /tmp/trcc216_src/TRCC.decompiled.cs
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


def _resx_a1_raw(resx_dir: Path) -> set[str]:
    """Raw ``A1<...>`` resource names across all .resx — no hover-folding.

    Lets the actionable summary tell a real device button (base art present)
    from a hover-only orphan (e.g. 2.1.6 ships ``A1LF17a`` but no ``A1LF17``).
    """
    names: set[str] = set()
    for rx in resx_dir.glob("*.resx"):
        names |= set(re.findall(r'<data name="(A1[^"]+)"', rx.read_text(errors="ignore")))
    return names


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


# ── C# resolution-fingerprint parser (FormCZTVInit / AddhidDeviceList) ──────

def _function_bodies(text: str, name: str) -> list[str]:
    """Brace-matched bodies of EVERY ``<returntype> name(...) { ... }``.

    The decompile carries one ``FormCZTVInit`` per device-family class (e.g. the
    2560×720 Trofeo form + the main LCD form), so we scan and merge all of them.
    """
    bodies: list[str] = []
    for m in re.finditer(rf"\b\w[\w<>]*\s+{re.escape(name)}\s*\(", text):
        start = text.find("{", m.end())
        if start < 0:
            continue
        depth = 0
        for j in range(start, len(text)):
            if text[j] == "{":
                depth += 1
            elif text[j] == "}":
                depth -= 1
                if depth == 0:
                    bodies.append(text[start:j + 1])
                    break
    return bodies


def _csharp_resolutions(cs: Path) -> set[tuple[int, int]]:
    """Every panel resolution the C# supports — the ``is{W}x{H}`` flag universe."""
    text = cs.read_text(errors="ignore")
    return {(int(w), int(h)) for w, h in re.findall(r"\bis(\d+)x(\d+)\b", text)}


def _norm_guard(cond: str) -> str:
    """Tidy a C# guard into a readable fingerprint string."""
    cond = re.sub(r"\s+", " ", cond).strip()
    return (cond.replace("myDeviceMode", "mode")
                .replace("myDevicePingMu", "pm")
                .replace("pmSub", "sub"))


def _resolution_fingerprints(cs: Path) -> dict[tuple[int, int], list[str]]:
    """(w,h) → the C# guard(s) that select it, parsed from ``FormCZTVInit``.

    The chain is regular: an ``if/else if (guard)`` block whose body sets
    ``is{W}x{H} = true``, plus direct ``is{W}x{H} = fbl == N;`` assignments.
    Line-scan tracks the current guard so the resolution maps to its fingerprint.
    """
    out: dict[tuple[int, int], list[str]] = {}
    guard_re = re.compile(r"(?:else\s+)?if\s*\((.+)\)\s*$")
    direct_re = re.compile(r"is(\d+)x(\d+)\s*=\s*(fbl == \d+)\s*;")
    flag_re = re.compile(r"is(\d+)x(\d+)\s*=\s*true")

    def _add(res: tuple[int, int], guard: str) -> None:
        out.setdefault(res, [])
        if guard not in out[res]:
            out[res].append(guard)

    for body in _function_bodies(cs.read_text(errors="ignore"), "FormCZTVInit"):
        cur = ""
        for raw in body.splitlines():
            s = raw.strip()
            if (g := guard_re.match(s)):
                cur = _norm_guard(g.group(1))
            elif (d := direct_re.search(s)):
                _add((int(d.group(1)), int(d.group(2))), d.group(3))
            elif (f := flag_re.search(s)) and cur:
                _add((int(f.group(1)), int(f.group(2))), cur)
    return out


def _handshake_convention(cs: Path) -> str:
    """The (pm, sub) byte positions from ``AddhidDeviceList`` — for the report.

    Surfaces it per-release so a future byte-layout change is visible, not
    silently assumed.
    """
    m = re.search(r"ADDUserButton\(ID,\s*receive\[(\d+)\],\s*receive\[(\d+)\]\)",
                  cs.read_text(errors="ignore"))
    return (f"pm=receive[{m.group(1)}], sub=receive[{m.group(2)}]"
            if m else "(AddhidDeviceList pattern not found)")


def _our_catalog_resolutions() -> set[tuple[int, int]]:
    """Resolutions a real device in our catalog actually resolves to.

    The accurate check (bare FBL_PROFILES misses pm-override-derived sizes):
    every (pm, sub) in the variant registry run through the real resolver, plus
    any fixed native_resolution from registry devices with no variant table.
    """
    from trcc.core.protocol import get_profile, pm_to_fbl
    from trcc.core.registry import ALL_DEVICES
    from trcc.core.variants import _VARIANT_REGISTRY
    out: set[tuple[int, int]] = set()
    for table in _VARIANT_REGISTRY.values():
        for pm, subs in table.items():
            for sub in subs:
                s = sub if sub is not None else 0
                out.add(get_profile(pm_to_fbl(pm, s), pm).resolution)
    for product in ALL_DEVICES.values():
        if product.native_resolution != (0, 0):
            out.add(product.native_resolution)
    return out


def _led_panel_composition(cs: Path) -> list[dict]:
    """Per-device LED panel composition parsed from the C# ``FormLEDInit``.

    FormLEDInit(NO, …) is keyed on the handshake device family ``NO``; each block
    sets the segment style (``nowLedStyle``), the segment preview image
    (``Resources.D<model>``), and the section visibility — sensor gauges
    (``ucInfoImage1-6``, shown by default), the LC1 memory panel
    (``ucledMemoryInfo1``), the LF11 disk panel (``ucledHarddiskInfo1``), and the
    LC2 week/clock buttons (``buttonWeek*``).  This is the C#'s authoritative
    "what does this LED device's panel show", to drive the in-code panel model.
    """
    rows: list[dict] = []
    cur: dict | None = None
    for body in _function_bodies(cs.read_text(errors="ignore"), "FormLEDInit"):
        for raw in body.splitlines():
            s = raw.strip()
            nos = [int(n) for n in re.findall(r"NO ==\s*(\d+)", s)]
            rng = re.findall(r"NO\s*(>=|<=|>|<)\s*(\d+)", s)
            case = re.match(r"case\s+(\d+)\s*:", s)
            if nos or case or (rng and s.startswith(("if", "else"))):
                if cur is not None:               # a new NO / range / case block
                    rows.append(cur)
                if nos:
                    label = ",".join(str(n) for n in nos)
                elif case:
                    label = case.group(1)
                else:
                    label = " ".join(f"NO{op}{n}" for op, n in rng)
                cur = {"no": label, "style": None,
                       "preview": None, "sensors": True, "memory": False,
                       "disk": False, "week": False}
                continue
            if cur is None:
                continue
            if (m := re.search(r"nowLedStyle = (\d+)", s)):
                cur["style"] = int(m.group(1))
            if (m := re.search(r"Resources\.(D[A-Za-z0-9_]+)", s)) and not cur["preview"]:
                cur["preview"] = m.group(1)
            if re.search(r"ucInfoImage\d\)\.Hide\(\)", s):
                cur["sensors"] = False
            if "ucledMemoryInfo1).Show()" in s:
                cur["memory"] = True
            if "ucledHarddiskInfo1).Show()" in s:
                cur["disk"] = True
            if re.search(r"buttonWeek\d\)\.Show\(\)", s):
                cur["week"] = True
    if cur is not None:
        rows.append(cur)
    # Keep only blocks that actually configured a panel (have a style/preview).
    return [r for r in rows if r["style"] is not None or r["preview"]]


def _led_zone_styles(cs: Path) -> set[int]:
    """LED styles the C# treats as RGB-ZONE (per-zone colour) vs metric-PAGE.

    ``ucColor1Delegate`` writes per-zone colour only under the gate
    ``if (nowLedStyle == 2 || nowLedStyle == 7)`` — every other style uses a
    single global colour and its ``button1-4`` row selects which metric *page*
    the single numeric display shows instead.  This returns that gate's style
    set: the authoritative ZONE classification driving the in-code display
    model (``ui.presentation.led_display``).
    """
    for body in _function_bodies(cs.read_text(errors="ignore"), "ucColor1Delegate"):
        for raw in body.splitlines():
            s = raw.strip()
            if s.startswith(("if", "else if")) and "nowLedStyle ==" in s:
                nums = {int(n) for n in re.findall(r"nowLedStyle ==\s*(\d+)", s)}
                if nums:
                    return nums
    return set()


def _lcd_panel_composition(cs: Path) -> dict[tuple[int, int], dict]:
    """Per-resolution LCD panel attributes from C# ``FormCZTVInit``.

    The LCD form is one form for every LCD device; the per-device panel
    variation is **widescreen** — a ``isBiliPingmu`` "bilibili screen" panel that
    spins up the projection/screen-image form + a ``P0预览弹窗{res}`` preview
    popup (854x480, 1280x480, 1920x462/440, 960x540, …) — vs a **standard**
    square/portrait preview (320x320, 480x480, …).  Keyed on the resolution the
    handshake resolves to, so the gui picks the right LCD preview/panel.
    """
    out: dict[tuple[int, int], dict] = {}
    for body in _function_bodies(cs.read_text(errors="ignore"), "FormCZTVInit"):
        res: tuple[int, int] | None = None
        wide = False
        popup: str | None = None
        for raw in body.splitlines():
            s = raw.strip()
            if s.startswith(("if ", "else if ", "else if(", "if(")):
                if res is not None:               # close the previous branch
                    out[res] = {"widescreen": wide, "popup": popup}
                res, wide, popup = None, False, None
            if (m := re.search(r"is(\d+)x(\d+) = true", s)):
                res = (int(m.group(1)), int(m.group(2)))
            if "isBiliPingmu = true" in s:
                wide = True
            if (m := re.search(r"P0预览弹窗([0-9A-Za-z]+)", s)):
                popup = m.group(1)
        if res is not None:
            out[res] = {"widescreen": wide, "popup": popup}
    return out


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
    ap.add_argument("--cs", default="/tmp/trcc216_src/TRCC.decompiled.cs",
                    help="single-file .cs decompile (ilspycmd <exe>) for the "
                         "resolution-fingerprint parser")
    a = ap.parse_args()
    resx_dir, setup, cs = Path(a.resx), Path(a.installer), Path(a.cs)
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
    else:
        print(f"\n(installer not found at {setup} — skipping data archive diff)")

    res_gap: list[tuple[int, int]] = []
    res_fps: dict[tuple[int, int], list[str]] = {}
    if cs.is_file():
        _h("RESOLUTIONS (C# is{W}x{H} universe vs our resolved device catalog)")
        cs_res = _csharp_resolutions(cs)
        ours = _our_catalog_resolutions()
        res_fps = _resolution_fingerprints(cs)
        res_gap = sorted(cs_res - ours)
        print(f"  handshake fingerprint: {_handshake_convention(cs)}")
        print(f"  C# supports {len(cs_res)} panel resolutions; "
              f"{len(res_gap)} not produced by any device in our catalog:")
        for w, h in res_gap:
            guards = res_fps.get((w, h)) or [f"(direct fbl assign — grep is{w}x{h})"]
            print(f"    + {w}x{h}   ⟵ {'  |  '.join(guards)}")
        only_ours = sorted(ours - cs_res)
        if only_ours:
            print(f"  ({len(only_ours)} ours-only — derived rotations / legacy: "
                  + ", ".join(f"{w}x{h}" for w, h in only_ours) + ")")
    else:
        print(f"\n(.cs decompile not found at {cs} — skipping resolution diff; "
              f"run `ilspycmd <exe>` and pass --cs)")

    if cs.is_file():
        _h("PANELS — LED composition (C# FormLEDInit, by handshake NO → style)")
        comp = _led_panel_composition(cs)
        print(f"  {'NO':>12}  {'style':>5}  {'preview':<22} sections")
        for r in comp:
            secs = ["gauges" if r["sensors"] else "-gauges"]
            if r["memory"]:
                secs.append("memory")
            if r["disk"]:
                secs.append("disk")
            if r["week"]:
                secs.append("week/clock")
            style = r["style"] if r["style"] is not None else 1   # C# field default
            preview = r["preview"] or "?"
            print(f"  {r['no']:>12}  {style:>5}  {preview:<22} {' '.join(secs)}")
        print(f"  ({len(comp)} device blocks; style defaults to 1 when unset; "
              f"sensor gauges default, LC1→memory, LF11→disk, LC2→week/clock — "
              f"to drive LedPanelModel)")

        _h("PANELS — LCD composition (C# FormCZTVInit, by handshake fingerprint)")
        lcd = _lcd_panel_composition(cs)
        print(f"  {'handshake fingerprint':<46} {'res':>9}  panel")
        for res in sorted(lcd):
            w, h = res
            attrs = lcd[res]
            kind = ("widescreen/projection" if attrs["widescreen"]
                    else "standard preview")
            popup = f"  popup={attrs['popup']}" if attrs["popup"] else ""
            guards = res_fps.get(res) or ["(direct fbl assign)"]
            print(f"  {'  |  '.join(guards):<46} {f'{w}x{h}':>9}  {kind}{popup}")
        print(f"  ({len(lcd)} LCD resolutions, keyed on the handshake "
              f"(mode,pm,sub,fbl) → resolution → panel kind, to drive the LCD "
              f"panel model)")

    _h("PANELS (Form*.resx → our analogue)")
    forms = sorted(f.stem.replace("TRCC.", "") for f in resx_dir.glob("*.resx")
                   if "Form" in f.stem or "UC" in f.stem)
    for f in forms:
        have = _PANEL_MAP.get(f, "?")
        mark = "MISSING" if have is None else ("?" if have == "?" else "have")
        print(f"  [{mark:7}] {f:32} {have or ''}")

    _h("WHAT TO PULL (actionable — validate each on the dev console before landing)")
    todo = False
    resx_a1_raw = _resx_a1_raw(resx_dir)
    pullable = [m for m in missing if m in resx_a1_raw]    # base art exists → extractable
    orphans = [m for m in missing if m not in resx_a1_raw]  # hover-only, no base
    if pullable:
        todo = True
        names = ",".join(f"{m},{m}a" for m in pullable)
        print("  • button images for new device models:")
        print("      python dev/tools/extract_resx_images.py \\")
        print(f"          --resx {resx_dir}/TRCC.Properties.Resources.resx --names {names}")
    if orphans:
        print(f"  • skipped {len(orphans)} hover-only orphan(s) with no base art in the "
              f"resx (not used by any variant): {', '.join(orphans)}")
    if res_gap:
        todo = True
        print("  • new panel resolution(s) — add a profile row + pull data:")
        for w, h in res_gap:
            fp = (res_fps.get((w, h)) or ["(grep is%dx%d)" % (w, h)])[0]
            print(f"      {w}x{h}: variant fingerprint  ⟵ {fp}")
            print(f"             data:  python dev/tools/pack_theme_archives.py {w}{h}"
                  f"   (from installer Data/USBLCD/Theme{w}{h}, Web/{w}{h}, Web/zt{w}{h})")
    if not todo:
        print("  nothing — devices, assets, and resolutions are all covered. ✓")


if __name__ == "__main__":
    main()
