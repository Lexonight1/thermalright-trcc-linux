# Syncing a new Thermalright TRCC release

Thermalright ships software updates that add devices, resolutions, themes, and
sometimes whole panel families. This is the repeatable protocol for bringing a
new release into our port. **The C# is the math/behaviour reference, never the
architecture reference** — we extract *what* it does and reimplement cleanly
(hexagonal/SOLID), per CLAUDE.md "Porting from C#/Windows Source".

The tools referenced live in `dev/tools/`. Run everything with `PYTHONPATH=src`.

## Prerequisites — programs you need

| Program | What it's for | Install (Fedora shown) |
|---|---|---|
| **.NET SDK** (`dotnet`) | runtime for the decompiler | `dnf install dotnet-sdk-8.0` (or newer) |
| **ilspycmd** | decompile `TRCC.exe` → `.cs` + `.resx` | `dotnet tool install -g ilspycmd` (adds `~/.dotnet/tools` to PATH) |
| **p7zip** (`7z`) | extract the self-extracting-zip installer; pack the `.7z` data archives | `dnf install p7zip p7zip-plugins` |
| **Python 3.12** + repo dev deps | run the audit / extract / pack tools + the app | already required to build TRCC |

Notes:
- `py7zr` (Python) is *optional* — `pack_theme_archives.py` falls back to the
  system `7z` when it's absent.
- **Ghidra is NOT needed.** It's only useful for the native bits
  (`ffmpeg.exe`/`libusb`); the device logic lives in the managed `TRCC.exe`,
  which `ilspycmd` decompiles fully. Don't reach for Ghidra for a normal sync.

## 0. Point the oracle at the new release, get the build, decompile it

**Bump `ORACLE_VERSION` in `dev/decompiler/core/csharp.py` first** — the full
four-part `AssemblyVersion` the new `TRCC.exe` declares. It is the single source
for the release: `ORACLE_RELEASE`, `DECOMPILE_ROOT` (the decompile directory,
override `TRCC_DECOMPILE`) and `INSTALLER` (the setup `.exe` path, override
`TRCC_INSTALLER`) all derive from it. `tests/test_oracle_version.py` gates it:
the decompile at `DECOMPILE_ROOT` must declare exactly that version (wrong
fails, absent skips). Never spell the release anywhere else.

The installer is a PE with an appended ZIP (not Inno/NSIS, not a .NET-Core
single-file bundle). Put it at the path `INSTALLER` names, extract the app
assemblies + data tree, then decompile in project mode into `DECOMPILE_ROOT`:

```bash
SETUP="<the path INSTALLER names>"
DECOMPILE="<the path DECOMPILE_ROOT names>"
7z l -tzip "$SETUP"                                   # confirm the TRCCCAP/ tree
7z x -tzip -y -o/tmp/trccXYZ_zip "$SETUP" "TRCCCAP/*" # app + Data/USBLCD tree
ilspycmd -p -o "$DECOMPILE" /tmp/trccXYZ_zip/TRCCCAP/TRCC.exe  # → Forms + .resx + Properties/
PYTHONPATH=src pytest tests/test_oracle_version.py    # must pass, not skip
```

Project mode (`-p`) is the one that is needed: it emits the `.resx` (Forms +
Resources) the audit reads and `Properties/AssemblyInfo.cs`, which
`assembly_version()` reads. The resolution-fingerprint parser reads either a
project tree or a single-file `ilspycmd` dump, so a plain run is optional.
`TRCC.exe` is the GUI; `USBLCD.exe`/`USBLCDNEW.dll` are the device layer if a
protocol diff is needed.

(The installer is a PE + appended ZIP — open it with `7z -tzip`, **not**
`sfextract` (which rejects it) and **not** Ghidra (managed code; ILSpy covers it).)

## 1. Audit — what changed

```bash
PYTHONPATH=src python3 dev/tools/audit_csharp.py
```

`--resx` and `--cs` default to `DECOMPILE_ROOT` and `--installer` to
`INSTALLER`; pass them only to audit something else.

`audit_csharp.py` diffs each dimension against our registries and prints
**new / missing / dropped**:

| dimension | source | our side | reliability |
|---|---|---|---|
| devices | resx `A1<model>` buttons | `core.variants` `_VARIANT_REGISTRY` | reliable (kept names) |
| assets — device | resx `A1<model>(+a)` | `src/trcc/assets/` + `src/trcc/ui/gui/assets/` | reliable |
| assets — chrome | resx, via `rename_assets.RENAME_MAP` | `src/trcc/assets/` + `src/trcc/ui/gui/assets/` | **advisory** (unmapped renames/localization variants over-report) |
| data | installer `Theme{res}`/`Web` | `src/trcc/data/*.7z` | reliable |
| resolutions | C# `is{W}x{H}` universe + the `(mode,pm,sub,fbl)` fingerprint (parsed from `FormCZTVInit`/`AddhidDeviceList` in the `.cs`) | our **resolved device catalog** (`get_profile∘pm_to_fbl`) | reliable |
| panels | `Form*.resx` families | `ui/gui` panels (known map) | reliable |

The audit is the source of truth for the rest of this list. Its final
**WHAT TO PULL** section is the actionable checklist — it prints the exact
`extract_resx_images` / `pack_theme_archives` commands and the C# handshake
fingerprint for every genuinely-new device, resolution, and asset.

## 2. Publish new data (reliable, do first)

For each new/changed resolution the audit reports, stage the unpacked source
dirs and pack:

```bash
B=/tmp/trccXYZ_zip/TRCCCAP/Data/USBLCD
for r in 320960 1920440 …; do            # the resolutions from the audit
  cp -r "$B/Theme$r" src/trcc/data/Theme$r
  cp -r "$B/Web/$r"  src/trcc/data/web/$r
  cp -r "$B/Web/zt$r" src/trcc/data/web/zt$r
done
PYTHONPATH=src python3 dev/tools/pack_theme_archives.py   # → theme/web/zt {res}.7z
rm -rf src/trcc/data/Theme* src/trcc/data/web/[0-9]* src/trcc/data/web/zt*  # only .7z are committed
```

Commit the `.7z` only (source dirs are build inputs). Data only takes effect
once pushed — the app fetches from `raw.githubusercontent.com/.../main/src/trcc/data/`.

## 3. Port new devices

For each new device the audit lists (its WHAT TO PULL section prints the exact
commands):
1. **Fingerprint** — the audit already extracts the `(mode,pm,sub,fbl)`
   fingerprint from the C# (`FormCZTVInit`/`ADDUserButton`); confirm it against
   the decompile in `DECOMPILE_ROOT` if it looks surprising, then add the row
   to `core/variants.py` `_VARIANT_REGISTRY` (the normalized `(vid,pid)→pm→
   {sub: model}` table — one row, never special-cased in code).
2. **Profile** — if it introduces a new resolution, add it to `core/protocol.py`
   (today: `FBL_PROFILES` + the `_FBL_*_BY_PM` pm-override tables; the audit's
   resolution diff lists these with their fingerprint).
3. **Button asset** — extract `A1<model>(+a)` from the resx:
   `python dev/tools/extract_resx_images.py --resx <proj>/TRCC.Properties.Resources.resx
   --names A1<model>,A1<model>a` → writes to `src/trcc/ui/gui/assets/`, the one
   colour tree qtgui shares and greyscales at load (device buttons keep the C# name). A guard test (`test_every_variant_button_image_has_an_asset`)
   fails CI if any variant's button is unbundled.
4. **Validate** — simulate it with zero hardware via the dev variant console:
   `PYTHONPATH=src python3 dev/mock_gui.py` → summon it; or
   `dev/tools/diagnose_metrics.py <vid:pid> <pm>` for a deterministic check that
   metrics + preview populate.

## 4. New panel families

If the audit reports a missing `Form*` family (e.g. `CZTV` = screenshot/
screen-image/color-picker), that's a new panel — a deliberate build, not a
port. Add it under `ui/gui`, and extend the shared backbone
`ui/presentation/device_presentation.py` so the device's `ProductInfo` resolves
to its panel set (family → panels). Both `ui/gui` and `ui/qtgui` read that one
model — no per-device branching.

## 5. Chrome assets (advisory)

The audit's chrome diff over-reports (localization variants, unmapped renames).
Treat it as a checklist, not gospel: extract any genuinely-new chrome from the
resx, add it to `rename_assets.py`'s map (C#→English), rename, and drop into
`src/trcc/ui/gui/assets/` (the tree `rename_assets.py` and `Assets` read;
`src/trcc/assets/` is for packaging files — fonts, icons, `.desktop`).

## 6. Verify + publish

```bash
ruff check . && pyright
PYTHONPATH=src pytest tests/ -n 8 -q          # includes test_oracle_version.py
TRCC_GUI_AUDIT=1 PYTHONPATH=src pytest tests/test_variant_presentation_audit.py  # every variant shows properly
PYTHONPATH=src python3 dev/mock_gui.py    # eyeball the new devices on a real display
```

Commit per dimension (data / devices / profiles / assets / panels) in verifiable
increments; push only on explicit instruction. Then update `core/variants.py`'s
parity notes. The release we are synced to is `ORACLE_VERSION`
(`dev/decompiler/core/csharp.py`); this doc does not repeat it.

---
**State of the sync** (the release is `ORACLE_VERSION`):

Done: variant fingerprints for LC10/LC13/LC15/LD11/LF014/RX1; their button images
extracted + bundled (guard test enforces coverage); data for 6 resolutions
published; `audit_csharp.py` upgraded to parse the C# resolution-fingerprint chain.
**176×320** (⟵ `mode==3 && pm==100 && fbl==60`, a sub-screen) now has its profile
row (`FBL_PROFILES[60]`, `core/protocol.py`) and its data (`theme176320.7z`,
`theme320176.7z`). The **CZTV** panel family is built: `FormScreenshot` →
`ui/viewfinder.py`, `FormScreenImage` → `ui/gui/preview_popup.py`, `FormGetColor`
→ `ui/eyedropper.py` (the audit's panel map, `audit_csharp.py`).

Outstanding (per the audit's WHAT TO PULL): **2560×720** (⟵ `fbl==257`, the Trofeo
family) — not in `FBL_PROFILES` or the PM tables; it needs a profile row + variant +
data pull. Re-run the audit for anything the latest release added. `A1LF17` is a
hover-only orphan in the C# (no base art, no variant) — not a device, ignore it.
