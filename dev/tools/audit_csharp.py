#!/usr/bin/env python3
"""Audit a decompiled Thermalright TRCC version against our Python port.

Thermalright ships updates regularly; this turns "audit the new version" into one
re-runnable command that tells you exactly what's new and what to pull. Inputs:

    --resx       extracted .resx (Forms + Resources, from ``ilspycmd -p <exe>``)
    --installer  the installer .exe (its ``Data/USBLCD`` data tree, read via 7z)
    --cs         the decompiled C# for the resolution-fingerprint parser —
                 a project tree (``ilspycmd -p``) or a single-file dump
                 (``ilspycmd``); ``decompile_text`` reads both the same

Both decompile inputs default to :data:`core.csharp.DECOMPILE_ROOT` — the ONE
definition of "where the oracle lives", overridable with ``TRCC_DECOMPILE``.
They used to default to a pair of ``/tmp`` scratch paths, which is how three
C#-parity tests in ``tests/`` sat permanently skipped: a wiped scratch dir is
indistinguishable from "no decompile installed", so the gate reported itself
absent instead of broken.

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
    TRCC_DECOMPILE=~/Downloads/TRCC_2.0.3_decompiled \
        PYTHONPATH=src python3 dev/tools/audit_csharp.py
"""
from __future__ import annotations

import argparse
import tempfile
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "decompiler"))
from core.csharp import (  # pyright: ignore[reportMissingImports]
    DECOMPILE_ROOT,
    INSTALLER,
    ORACLE_RELEASE,
    decompile_text,
)
from rename_assets import RENAME_MAP  # C# (Chinese) name → our English name

REPO = Path(__file__).resolve().parent.parent.parent
# Both asset roots, because the two checks below want different halves of
# them and a miss in either reads as "2.1.6 has art we never ported".
#
# This constant used to name ONLY the first one -- the packaging root: two
# fonts, eight icons, a .desktop and a polkit policy, 17 files, and not one
# piece of device art.  Every one of the C#'s device buttons therefore came
# back missing, and the tool reported 68 absent coolers while the repo held
# 176 A1* PNGs the scan could never see.  The GUI's own resolver says where
# they really live (ui/gui/assets.py: ``_PKG_ASSETS_DIR = Path(__file__).parent
# / 'assets'``); read that rather than guessing a second time.
ASSET_ROOTS = (
    REPO / "src" / "trcc" / "assets",           # packaging: fonts, icons, .desktop
    REPO / "src" / "trcc" / "ui" / "gui" / "assets",   # the GUI art itself
)
DATA = REPO / "src" / "trcc" / "data"

#: A C# form that is in the program but never shown by the shipped app.
DEAD_IN_CSHARP = "dead in C#"

# C# Form (.resx) -> our analogue as a REPO PATH (gate() checks each exists),
# None when we have no panel for it, or DEAD_IN_CSHARP.  Values were free-text
# names until 2026-10-06 and drifted both ways: four forms we had read MISSING
# (the LCD page, the eyedropper, the region picker, the preview popup), and the
# two FormLCD forms read "have" though the C# never runs them.
_PANEL_MAP: dict[str, str | None] = {
    "LED.FormLED": "src/trcc/ui/gui/uc_led_control.py",
    # FormLCD launches Data/LCD/TRCCLCDAPP.exe, which no installer ships;
    # FormLCDImageCut is its screen-region marker.  dev/decompiler/
    # AUDIT_FORMLCD_PROJECTION.md.
    "LCD.FormLCD": DEAD_IN_CSHARP,
    "LCD.FormLCDImageCut": DEAD_IN_CSHARP,
    "FormSystemInfo": "src/trcc/ui/gui/uc_system_info.py",
    "FormStart": "src/trcc/ui/gui/splash.py",
    "Form1": "src/trcc/ui/gui/trcc_app.py",
    "DCUserControl.UCThemeSetting": "src/trcc/ui/gui/uc_theme_setting.py",
    "DCUserControl.UCShortcut": None,        # the icon element (mode 5)
    "KVMALED6.FormKVMALED6": "src/trcc/ui/gui/uc_led_control.py",  # via KVMALEDC6 -> PA120
    "CZTV.FormCZTV": "src/trcc/ui/gui/lcd_handler.py",
    "CZTV.FormScreenshot": "src/trcc/ui/viewfinder.py",
    "CZTV.FormScreenImage": "src/trcc/ui/gui/preview_popup.py",
    "CZTV.FormGetColor": "src/trcc/ui/eyedropper.py",
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


def _selected_device_models(cs_text: str) -> set[str]:
    """The ``A1<model>`` names 2.1.6 actually SELECTS, not merely ships.

    ``Resources.cs`` declares an accessor for every image in the resx, whether
    or not any code path assigns it to a button.  Comparing our variant table
    against the resx alone therefore counts dead artwork as missing coolers:
    ``A1LD10`` has both a resx entry and an accessor, and ``ADDUserButton``
    never names it in this release -- our table was already corrected off it
    (see variants.py "was A1LD10 in our prior table").  ``A1CZTV`` is the
    opposite error: it IS selected, as the ``default:`` arm, so it lives in our
    ``ProductInfo.button_image`` default rather than in a variant row.

    So the honest denominator is "referenced by a chooser", which means every
    ``.cs`` EXCEPT the generated resource accessors.

    Names come back with spaces folded to underscores, because that is what
    the generated accessor does: the resx entry ``A1FROZEN WARFRAME`` is
    reached in code as ``Resources.A1FROZEN_WARFRAME``, since a C# identifier
    cannot contain a space.  Comparing the two spellings directly reports 21
    shipping coolers as dead art -- which is what this function did on its
    first run.  (It is also why our asset dir carries both spellings.)
    """
    return set(re.findall(r"Resources\.(A1[A-Za-z0-9_]+)", cs_text))


def _as_identifier(name: str) -> str:
    """A resx entry name as the generated C# accessor spells it."""
    return name.replace(" ", "_")


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
    """Every button image we can produce — variant rows AND the default.

    The default matters: the C#'s ``default:`` arm is ``A1CZTV``, and ours is
    ``ProductInfo.button_image``, whose dataclass default is the same string.
    Reading only the variant table therefore reported the one image we are
    guaranteed to have as the one image we were missing.
    """
    from trcc.core.models import ProductInfo
    from trcc.core.variants import _VARIANT_REGISTRY

    models = {ov.button_image
              for t in _VARIANT_REGISTRY.values()
              for subs in t.values() for ov in subs.values()}
    default = ProductInfo.__dataclass_fields__["button_image"].default
    if isinstance(default, str) and default:
        models.add(default)
    return models


def _our_asset_stems() -> set[str]:
    """Every image stem we ship, across both roots.

    Guarded rather than trusted: an empty or tiny result makes both checks
    below vacuous -- they would report the ENTIRE C# asset list as missing and
    look like a catastrophic porting gap, which is exactly what happened while
    this scanned the wrong directory.  A wrong path is indistinguishable from
    "we ported nothing" unless someone asserts the difference.
    """
    stems = {p.stem for root in ASSET_ROOTS for p in root.rglob("*")
             if p.is_file() and p.suffix.lower() in (".png", ".jpg", ".jpeg", ".ico")}
    if len(stems) < 100:
        raise SystemExit(
            f"audit_csharp: only {len(stems)} image(s) found under "
            f"{[str(r) for r in ASSET_ROOTS]} — that is not a repo with GUI "
            "art in it.  Fix the roots before trusting any asset verdict."
        )
    return stems


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


# A resolution key as it appears in a directory name: digits, optionally
# followed by the per-SKU variant letter.  1600x720 ships SIX theme
# directories -- 1600720/u/l and 7201600/u/l -- chosen by pmSub (2 and 4 -> u,
# 3 -> l) crossed with orientation; see FormCZTV.cs:1290-1353.  A third
# letter ``y`` is PM-keyed and masks-only: is480x480 && myDevicePingMu
# == 3 -> zt480480y (FormCZTV.cs:5746).
#
# The pattern used to be ``(\d+)\b``, which cannot match a suffixed name AT
# ALL: ``\b`` between a digit and a letter is never a boundary, so
# ``Theme1600720u`` returned no match rather than a partial one.  The variants
# could therefore never enter the installer set, ``installer - ours`` was empty
# by construction, and the check reported "0 new, 0 only-ours" while twelve
# archives were missing across the three axes.  It was not measuring us against
# the installer; it was measuring the digits-only names against themselves.
# The suffix is ANY one letter, not a list of the letters seen so far: ``[uly]``
# could not match 2.1.8's ``Theme360360m``, so the audit reported a library we
# ship as ours-only and the installer as disagreeing with itself (2026-10-06).
_RES_KEY = r"(\d+[a-z]?)"

#: Directory prefix -> where our matching archives live, per data axis.
_DATA_AXES = (
    ("themes",          rf"Data/USBLCD/Theme{_RES_KEY}\b",  "theme{}.7z"),
    ("web backgrounds", rf"Data/USBLCD/Web/{_RES_KEY}\b",   "web/{}.7z"),
    ("zt masks",        rf"Data/USBLCD/Web/zt{_RES_KEY}\b", "web/zt{}.7z"),
)


def _installer_listing(setup: Path) -> str:
    """The installer's zip index, read once — 7z over ~880 MB is the slow part."""
    return subprocess.run(["7z", "l", "-tzip", str(setup)],
                          capture_output=True, text=True).stdout


def _installer_axis(listing: str, pattern: str, label: str) -> set[str]:
    """Resolution keys the installer carries on one axis, guarded.

    Guarded for the same reason the asset scan is: a pattern that matches
    nothing is indistinguishable from "the installer and we agree", and reads
    as parity.  That is precisely how the suffix bug stayed invisible.
    """
    found = set(re.findall(pattern, listing))
    if len(found) < 20:
        raise SystemExit(
            f"audit_csharp: only {len(found)} {label} director(ies) matched in "
            f"the installer ({pattern!r}) — the installer layout changed or the "
            "pattern is wrong.  Fix it before trusting any data verdict."
        )
    return found


def _our_data_keys(template: str) -> set[str]:
    """Resolution keys we ship on one axis, recovered from the archive names."""
    prefix, suffix = template.split("{}")
    keys: set[str] = set()
    for path in DATA.glob(f"{prefix}*{suffix}"):
        key = path.relative_to(DATA).as_posix()[len(prefix):-len(suffix)]
        if prefix == "web/" and key.startswith("zt"):
            continue                    # web/zt*.7z belongs to the mask axis
        keys.add(key)
    return keys


def _csharp_data_keys(text: str) -> dict[str, set[str]]:
    """The directories the C# itself names — the second, independent source.

    The installer says what shipped; these constants say what the program asks
    for.  Checking one against the other is what surfaced the gap by hand, and
    a disagreement is worth printing rather than silently resolving.
    """
    return {
        "themes": set(re.findall(r'ThemeML\w*\s*=\s*"([^"\\]+)\\\\"', text)),
        "web backgrounds": set(re.findall(
            r'GifDirectoryWeb(?!MB)\w*\s*=\s*"USBLCD\\\\Web\\\\([^"\\]+)\\\\"', text)),
        "zt masks": set(re.findall(
            r'GifDirectoryWebMB\w*\s*=\s*"USBLCD\\\\Web\\\\zt([^"\\]+)\\\\"', text)),
    }


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
    text = decompile_text(cs)
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

    for body in _function_bodies(decompile_text(cs), "FormCZTVInit"):
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
                  decompile_text(cs))
    return (f"pm=receive[{m.group(1)}], sub=receive[{m.group(2)}]"
            if m else "(AddhidDeviceList pattern not found)")


def _our_catalog_resolutions() -> set[tuple[int, int]]:
    """Resolutions a real device in our catalog actually resolves to.

    Each variant table is resolved the way ITS WIRE resolves a handshake, by
    the shipped functions -- never by one resolver for all:

    * Bulk + LY: ``bulk_profile(pm, sub)``, the one implementation both
      ``connect()`` calls use (its docstring: an oracle that re-implements the
      thing it audits proves nothing).
    * HID: ``pm_to_fbl`` then ``get_profile``, as ``HidLcd`` does.
    * SCSI: the PM table selects button ART; the panel's size comes from the
      FBL byte its poll reports, so every ``FBL_PROFILES`` size is reachable.
    * LED: segment displays, no LCD size at all.

    It ran every table through the HID path until 2026-10-06, so the SCSI,
    Bulk and LY tables' PMs (1, 3, 4, 6, 19, 21, 22 ...) and the LED family's
    each fell back to 320x320 with "UNKNOWN FBL ... please report this line":
    140 false warnings a run, and invented entries in this set.
    """
    from trcc.adapters.device.bulk_lcd import bulk_profile
    from trcc.core.models import Wire
    from trcc.core.protocol import FBL_PROFILES, get_profile, pm_to_fbl
    from trcc.core.registry import ALL_DEVICES
    from trcc.core.variants import _VARIANT_REGISTRY
    out: set[tuple[int, int]] = {get_profile(fbl).resolution for fbl in FBL_PROFILES}
    for key, table in _VARIANT_REGISTRY.items():
        product = ALL_DEVICES.get(key)
        wire = product.wire if product is not None else None
        if wire not in (Wire.BULK, Wire.LY, Wire.HID):
            continue        # SCSI: covered by FBL_PROFILES above; LED: no LCD
        for pm, subs in table.items():
            for sub in subs:
                s = sub if sub is not None else 0
                if wire is Wire.HID:
                    out.add(get_profile(pm_to_fbl(pm, s), pm, s).resolution)
                else:
                    out.add(bulk_profile(pm, s)[1].resolution)
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
    for body in _function_bodies(decompile_text(cs), "FormLEDInit"):
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
    for body in _function_bodies(decompile_text(cs), "ucColor1Delegate"):
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
    for body in _function_bodies(decompile_text(cs), "FormCZTVInit"):
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

        # Panels assigned by EXPRESSION rather than by literal, e.g.
        # ``is480x480 = fbl == 72;`` — 480x480, 640x480 and 360x360 (the round
        # fan LCD) are all set this way and were invisible to the branch walk
        # above, so the parity gate had never checked them.
        #
        # A SEPARATE pass, not a widened pattern in the loop: these three sit
        # in a run of consecutive assignments with no ``if`` between them, and
        # the walk only closes a row at a branch boundary — so widening it
        # there would make each assignment overwrite the last and LOSE rows
        # that are currently correct.  Anchored at the start of the statement
        # so the fan-out block (``ucImageCut1.is320x320 = is320x320;``) cannot
        # match.  ``setdefault``, so anything the branch walk established wins.
        #
        # Standard by construction: none of the expression-assigned panels is
        # a ``isBiliPingmu`` screen — confirmed against
        # ``UCScreenImage.SetMyUCScreenImage``, where 480x480 and 360x360 both
        # draw the plain ``P320320`` frame.
        for m in re.finditer(r"^\s*is(\d+)x(\d+) = (?!true\b)", body, re.M):
            out.setdefault((int(m.group(1)), int(m.group(2))),
                           {"widescreen": False, "popup": None})
    return out


def gate() -> int:
    """Re-prove the C# parsers against a fixture.  Offline and instant.

    ``test_model_matches_csharp_audit`` asserts ``rows`` is non-empty, which is
    not the same as COMPLETE: measured 2026-09-20 the parser returned 12 of the
    15 ``is{W}x{H}`` flags in ``FormCZTV.cs``, because three are assigned by
    EXPRESSION (``is480x480 = fbl == 72;``) and the pattern matched only
    ``= true``.  One of the three is ``is360x360`` — the round fan LCD — so the
    parity gate had never checked it.  Non-empty is not complete.
    """
    checks: list[tuple[str, bool]] = []
    fixture = '''
public void FormCZTVInit(int fbl, int m, int pm, int pmSub)
{
    if (myDeviceMode == 2 && pm == 9)
    {
        isBiliPingmu = true;
        is854x480 = true;
        ((Control)formScreenImage).BackgroundImage = Resources.P0预览弹窗854X480;
    }
    else if (myDeviceMode == 2 && pm == 15)
    {
        is640x172 = true;
    }
    is480x480 = fbl == 72;
    is360x360 = fbl == 54;
    ucImageCut1.is320x320 = is320x320;
    ucThemeSetting1.ucTouPingXianShi1.is1234x567 = is1234x567;
}
'''
    with tempfile.TemporaryDirectory() as tmp:
        cs = Path(tmp) / "Form.cs"
        cs.write_text(fixture, encoding="utf-8")
        rows = _lcd_panel_composition(cs)

    checks.append(("a widescreen arm is found with its popup",
                   rows.get((854, 480), {}).get("widescreen") is True
                   and rows.get((854, 480), {}).get("popup") == "854X480"))
    checks.append(("an arm with no isBiliPingmu reads as standard",
                   rows.get((640, 172), {}).get("widescreen") is False))
    checks.append(("a popup is not attributed to the next arm",
                   rows.get((640, 172), {}).get("popup") is None))
    # The three the `= true` pattern could not see.
    checks.append(("an EXPRESSION-assigned flag is found (is480x480)",
                   (480, 480) in rows))
    checks.append(("an EXPRESSION-assigned flag is found (is360x360)",
                   (360, 360) in rows))
    checks.append(("every is{W}x{H} flag in the body is reported",
                   len(rows) == 4))
    # The fan-out block assigns each flag onto five child controls.  An
    # unanchored pattern matches those too and invents panels that do not
    # exist — 1234x567 is in the fixture solely to be NOT found.
    checks.append(("the fan-out block does not invent panels",
                   (1234, 567) not in rows and (320, 320) not in rows))
    # The map drifted for months as free text; a path that moved fails here.
    gone = [f"{form} -> {path}" for form, path in _PANEL_MAP.items()
            if path not in (None, DEAD_IN_CSHARP) and not (REPO / path).is_file()]
    checks.append((f"every panel analogue exists ({gone or 'all present'})",
                   not gone))
    # Each table resolved by its own wire's function.  Through one resolver
    # for all, SCSI/Bulk/LY/LED PMs warned "UNKNOWN FBL" 140 times a run.  The
    # last one was REAL -- HID PM 49 had no FBL row -- and got its row on
    # 2026-10-06, so any unknown FBL now is a new finding.
    import logging as _logging
    unknown: list[str] = []

    class _Catch(_logging.Handler):
        def emit(self, record: _logging.LogRecord) -> None:
            if "UNKNOWN FBL" in record.getMessage():
                unknown.append(record.getMessage().split(" (")[0])

    catch = _Catch()
    proto = _logging.getLogger("trcc.core.protocol")
    proto.addHandler(catch)
    try:
        _our_catalog_resolutions()
    finally:
        proto.removeHandler(catch)
    checks.append((f"tables resolve by their own wire ({len(unknown)} unknown "
                   f"FBL: {sorted(set(unknown))})",
                   not unknown))
    key = re.compile(rf"Data/USBLCD/Theme{_RES_KEY}\b")
    checks.append(("a resolution key takes any one-letter variant (m, u, l, y)",
                   all((m := key.search(f"Data/USBLCD/Theme{n}")) and m.group(1) == n
                       for n in ("360360m", "1600720u", "1600720l", "480480y"))))

    for label, ok in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    failed = [label for label, ok in checks if not ok]
    if failed:
        print(f"\n{len(failed)} gate check(s) FAILED — do not trust this "
              f"tool's output until they pass.")
        return 1
    print(f"\nAll {len(checks)} gate checks passed.")
    return 0


def _show(label: str, only_new: set, only_ours: set) -> None:
    print(f"  {label}: {len(only_new)} new in {ORACLE_RELEASE}, "
          f"{len(only_ours)} only-ours")
    for n in sorted(only_new):
        print(f"    + {n}")
    for n in sorted(only_ours):
        print(f"    - {n}  (ours, not in {ORACLE_RELEASE})")


def main() -> None:
    if "--gate" in sys.argv:
        raise SystemExit(gate())
    ap = argparse.ArgumentParser()
    ap.add_argument("--resx", default=str(DECOMPILE_ROOT),
                    help="decompile carrying the .resx files (ilspycmd -p <exe>)")
    ap.add_argument("--installer",
                    default=str(INSTALLER))
    ap.add_argument("--cs", default=str(DECOMPILE_ROOT),
                    help="the C# decompile for the resolution-fingerprint "
                         "parser — a project tree (ilspycmd -p) or a "
                         "single-file dump (ilspycmd); both read the same")
    a = ap.parse_args()
    resx_dir, setup, cs = Path(a.resx), Path(a.installer), Path(a.cs)
    if not resx_dir.is_dir():
        sys.exit(f"resx dir not found: {resx_dir} (run ilspycmd -p first)")

    _h("DEVICES (by button asset)")
    new_dev = _resx_device_models(resx_dir)
    # A name that exists only as a hover frame is not a device.  2.1.6's
    # ``case 12`` pairs button ``A1LF167`` with hover ``A1LF17a`` -- a typo in
    # the vendor's own code, since ``A1LF167a`` is what exists.  Folding that
    # hover back to a base invents a cooler called A1LF17 that no release has
    # ever had.
    raw_names = _resx_a1_raw(resx_dir)
    new_dev = {d for d in new_dev if d in raw_names}
    # Keep only models some code path actually chooses.  Without this the
    # audit reports dead resources as unported coolers -- three of them in
    # 2.1.6, every one a false alarm that cost a real investigation.
    if cs.exists():
        selected = _selected_device_models(decompile_text(cs))
        if len(selected) < 20:
            raise SystemExit(
                f"audit_csharp: only {len(selected)} A1* selections found in "
                "the decompile — the chooser moved or the pattern is wrong.  "
                "Fix it before trusting any device verdict."
            )
        unreferenced = {
            d for d in new_dev
            if _as_identifier(d) not in selected
            and f"{_as_identifier(d)}a" not in selected
        }
        if unreferenced:
            print(f"  shipped but never selected in {ORACLE_RELEASE} "
                  f"({len(unreferenced)}): {', '.join(sorted(unreferenced))}")
        new_dev -= unreferenced
    our_dev = _our_device_models()
    # Fold deliberate renames: a 2.1.6 device we renamed isn't "new".
    genuinely_new = {d for d in new_dev - our_dev
                     if _KNOWN_RENAMES.get(d, d) not in our_dev}
    _show("device models", genuinely_new, our_dev - new_dev)

    _h("ASSETS — device buttons (reliable; ours keep A1<model> names)")
    our_assets = _our_asset_stems()
    missing = sorted(m for m in new_dev if not _covered(m, our_assets))
    # Split the count the same way WHAT TO PULL does.  A model whose base art
    # 2.1.6 does not itself ship (A1LF17 exists only as the hover frame
    # A1LF17a) is not art we failed to port -- there is nothing to port, and
    # counting it says we are one cooler behind when we match the C# exactly.
    raw_a1 = _resx_a1_raw(resx_dir)
    absent = [m for m in missing if m in raw_a1]
    orphan = [m for m in missing if m not in raw_a1]
    print(f"  device-button images missing: {len(absent)}")
    for m in absent:
        print(f"    + {m} (+ {m}a hover)")
    if orphan:
        print(f"  hover-only in {ORACLE_RELEASE}, no base art to port ({len(orphan)}): "
              f"{', '.join(orphan)}")

    _h("ASSETS — chrome (converted via rename_assets.RENAME_MAP, then checked)")
    chrome = {n for n in _resx_all_assets(resx_dir) if n not in new_dev}
    chrome_missing = sorted(n for n in chrome if not _covered(n, our_assets))
    print(f"  chrome assets present in {ORACLE_RELEASE} but not covered: {len(chrome_missing)} "
          f"(of {len(chrome)})")
    print("  (caveat: a miss here may just be an unmapped rename, not absent art)")
    for n in chrome_missing[:40]:
        print(f"    ? {n}")
    if len(chrome_missing) > 40:
        print(f"    … +{len(chrome_missing) - 40} more")

    installer_data: set[str] | None = None     # None: no installer to ask
    if setup.is_file():
        installer_data = set()
        _h("DATA (per-resolution archives, all three axes)")
        listing = _installer_listing(setup)
        csharp_keys = _csharp_data_keys(decompile_text(cs)) if cs.exists() else {}
        for label, pattern, template in _DATA_AXES:
            theirs = _installer_axis(listing, pattern, label)
            installer_data |= theirs
            ours = _our_data_keys(template)
            _show(label, theirs - ours, ours - theirs)
            # The installer says what shipped; the C# constants say what the
            # program asks for.  Print any disagreement instead of trusting
            # whichever source happens to be read first.
            named = csharp_keys.get(label)
            if named and named != theirs:
                print(f"    ! installer and C# disagree on {label}: "
                      f"C#-only={sorted(named - theirs) or 'none'} "
                      f"installer-only={sorted(theirs - named) or 'none'}")
    else:
        print(f"\n(installer not found at {setup} — skipping data archive diff)")

    res_gap: list[tuple[int, int]] = []
    res_fps: dict[tuple[int, int], list[str]] = {}
    if cs.exists():
        _h("RESOLUTIONS (C# is{W}x{H} universe vs our resolved device catalog)")
        cs_res = _csharp_resolutions(cs)
        ours = _our_catalog_resolutions()
        res_fps = _resolution_fingerprints(cs)
        # One panel, two orientations: the C# names 176x320 where our profile
        # says 320x176 (rotate=True).  Unfolded, that read as a missing panel
        # and WHAT TO PULL told us to pack data the installer does not have.
        turned = {(h, w) for w, h in ours}
        folded = sorted(r for r in cs_res - ours if r in turned)
        res_gap = sorted(r for r in cs_res - ours if r not in turned)
        print(f"  handshake fingerprint: {_handshake_convention(cs)}")
        print(f"  C# supports {len(cs_res)} panel resolutions; "
              f"{len(res_gap)} not produced by any device in our catalog:")
        for w, h in res_gap:
            guards = res_fps.get((w, h)) or [f"(direct fbl assign — grep is{w}x{h})"]
            print(f"    + {w}x{h}   ⟵ {'  |  '.join(guards)}")
        for w, h in folded:
            print(f"    = {w}x{h}   is ours as {h}x{w} -- the same panel, turned")
        only_ours = sorted(r for r in ours - cs_res
                           if (r[1], r[0]) not in cs_res)
        if only_ours:
            print(f"  ({len(only_ours)} ours-only — derived rotations / legacy: "
                  + ", ".join(f"{w}x{h}" for w, h in only_ours) + ")")
    else:
        print(f"\n(C# decompile not found at {cs} — skipping resolution diff; "
              f"run `ilspycmd -p <exe>` and pass --cs)")

    if cs.exists():
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
        mark = ("MISSING" if have is None else "?" if have == "?"
                else "dead" if have == DEAD_IN_CSHARP else "have")
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
            if installer_data is not None and f"{w}{h}" not in installer_data:
                print("             data:  none to pull -- the C# names it, the "
                      "installer ships no data for it")
                continue
            print(f"             data:  python dev/tools/pack_theme_archives.py {w}{h}"
                  f"   (from installer Data/USBLCD/Theme{w}{h}, Web/{w}{h}, Web/zt{w}{h})")
    if not todo:
        print("  nothing — devices, assets, and resolutions are all covered. ✓")


if __name__ == "__main__":
    main()
