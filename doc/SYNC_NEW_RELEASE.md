# Syncing a new Thermalright TRCC release

Thermalright ships software updates that add devices, resolutions, themes, and
sometimes whole panel families. This is the repeatable protocol for bringing a
new release into our port. **The C# is the math/behaviour reference, never the
architecture reference** — we extract *what* it does and reimplement cleanly
(hexagonal/SOLID), per CLAUDE.md "Porting from C#/Windows Source".

The tools referenced live in `dev/tools/`. Run everything with `PYTHONPATH=src`.

## 0. Get the build + decompile it

The installer is a PE with an appended ZIP (not Inno/NSIS, not a .NET-Core
single-file bundle). Extract the app assemblies + data tree, then decompile:

```bash
SETUP="…/TRCC X.Y.Z-Setup.exe"
7z l -tzip "$SETUP"                                   # confirm the TRCCCAP/ tree
7z x -tzip -y -o/tmp/trccXYZ_zip "$SETUP" "TRCCCAP/*" # app + Data/USBLCD tree
ilspycmd -o /tmp/trccXYZ_src  /tmp/trccXYZ_zip/TRCCCAP/TRCC.exe   # → one .cs
ilspycmd -p -o /tmp/trccXYZ_proj /tmp/trccXYZ_zip/TRCCCAP/TRCC.exe  # → Forms + .resx
```

(`ilspycmd`, `7z`, `sfextract` are on the dev box. `TRCC.exe` is the GUI;
`USBLCD.exe`/`USBLCDNEW.dll` are the device layer if a protocol diff is needed.)

## 1. Audit — what changed

```bash
PYTHONPATH=src python3 dev/tools/audit_csharp.py \
    --resx /tmp/trccXYZ_proj --installer "$SETUP"
```

`audit_csharp.py` diffs each dimension against our registries and prints
**new / missing / dropped**:

| dimension | source | our side | reliability |
|---|---|---|---|
| devices | resx `A1<model>` buttons | `core.variants` `_VARIANT_REGISTRY` | reliable (kept names) |
| assets — device | resx `A1<model>(+a)` | `src/trcc/assets/` | reliable |
| assets — chrome | resx, via `rename_assets.RENAME_MAP` | `src/trcc/assets/` | **advisory** (unmapped renames/localization variants over-report) |
| data | installer `Theme{res}`/`Web` | `src/trcc/data/*.7z` | reliable |
| resolutions | data-tree resolutions | `core.protocol` `FBL_PROFILES` | reliable |
| panels | `Form*.resx` families | `ui/gui` panels (known map) | reliable |

The audit is the source of truth for the rest of this list. **NOT yet
automated:** the pm/sub handshake *fingerprints* (which bytes → which new
device) — read the `ADDUserButton` switch in the `.cs` for those (step 3).

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

For each new device the audit lists:
1. **Fingerprint** — read the `ADDUserButton(int ID, byte pm, byte sub)` switch
   in `/tmp/trccXYZ_src/TRCC.decompiled.cs` to find its pm/sub → model. Add the
   row to `core/variants.py` `_VARIANT_REGISTRY` (the normalized `(vid,pid)→pm→
   {sub: model}` table — one row, never special-cased in code).
2. **Profile** — if it introduces a new resolution/FBL, add it to
   `core/protocol.py` `FBL_PROFILES` (the audit's resolution diff lists these).
3. **Button asset** — extract `A1<model>(+a)` from the resx (ILSpy can save
   resources) into `src/trcc/assets/` (device buttons keep the C# name).
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
`src/trcc/assets/`.

## 6. Verify + publish

```bash
ruff check . && pyright
PYTHONPATH=src pytest tests/ -n 8 -q
TRCC_GUI_AUDIT=1 PYTHONPATH=src pytest tests/test_variant_presentation_audit.py  # every variant shows properly
PYTHONPATH=src python3 dev/mock_gui.py    # eyeball the new devices on a real display
```

Commit per dimension (data / devices / profiles / assets / panels) in verifiable
increments; push only on explicit instruction. Then update `core/variants.py`'s
parity notes and this doc's "last synced version".

---
**Last synced:** 2.1.6 (data for 6 resolutions published; new devices LC10/LC13/
LC15/LD11/LF014/LF17/RX1 + CZTV family, FBL_PROFILES resolution gaps, and the
CZTV panels still outstanding — see `audit_csharp.py` output).
