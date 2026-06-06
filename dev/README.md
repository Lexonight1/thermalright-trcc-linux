# `dev/` — contributor tooling

Everything here helps you develop and verify TRCC **without owning the
hardware**. The headline tool is `mock_gui.py`: it runs the *real* GUI — real
platform, real sensors, real composition root — and fakes only USB device
enumeration + the handshake bytes. Bugs you find here are real bugs.

> This project is GPL-3.0-or-later. Contributions are welcome under the same
> license — see `/LICENSE`.

## Develop for a device you don't own

```bash
# 1. See every cooler the app supports (model · vid:pid · PM · SUB · resolution)
PYTHONPATH=src python3 dev/mock_gui.py --list-devices

# 2. Seed your local fleet from the template, then edit it
cp dev/devices.json.example dev/devices.json
$EDITOR dev/devices.json            # keep only the device(s) you care about

# 3. Run the real GUI against that simulated fleet — zero hardware
PYTHONPATH=src python3 dev/mock_gui.py -v

# …or just simulate the ENTIRE catalog at once and scroll the sidebar:
PYTHONPATH=src python3 dev/mock_gui.py --all
```

Logs land in `dev/.trcc/trcc.log` (full history) and `dev/.trcc/trcc.latest.log`
(this run only — no offset math). `-v` = DEBUG, `-vv` = more.

Run headless (CI / no display) by appending Qt's `-platform offscreen`.

## `devices.json` schema

A JSON array of device specs. **`vid` + `pid` is enough** — the geometry
(resolution, rotation, encoding) is resolved faithfully from the registry,
through the *same* model path `connect()` uses. The rest are optional overrides:

| field | required | meaning |
|---|---|---|
| `vid`, `pid` | yes | USB IDs (hex strings, e.g. `"87ad"`) — must be a registered device |
| `pm` | no | PingMu byte — picks a specific **cooler model** under a shared vid:pid (see `--list-devices`). Defaults to the value that reproduces the registry's `native_resolution`. |
| `sub` | no | SUB byte — disambiguates models that share a PM |
| `fbl` | no | force an FBL code directly (rarely needed) |
| `name` | no | label shown in logs / sidebar |

`dev/devices.json` is **local to you** (git-ignored); commit nothing from it.
`devices.json.example` is the shared template.

## The device catalog

One USB vid:pid fronts *many* coolers — the handshake PM/SUB fingerprint tells
them apart (e.g. the bulk `87ad:70db` covers dozens of models; some coolers even
share one fingerprint under different marketing names). `--list-devices` shows
the real catalog: **~50 distinct models across 119 PM/SUB variants**.

Coverage is at **full parity with the original Windows app** (verified against
the C# v2.1.4 `UCDevice.ADDUserButton` dispatcher — every model present). When
Thermalright ships new coolers, cross-check
<https://www.thermalright.com/support/download/> and the C# decompile, then add
them (below).

## Add a device (the contributor loop)

A new cooler is **one or two registry rows** — and from those rows the mock can
simulate it faithfully *and* the shipping app can drive it:

1. Add a `ProductInfo` row to `src/trcc/core/registry.py` (`ALL_DEVICES`) — its
   vid:pid, wire, `device_type`, `fbl`, `native_resolution`.
2. If that vid:pid fronts multiple models, add the per-`(PM, SUB)`
   `VariantOverride` rows to `src/trcc/core/variants.py` (the `button_image`
   string is the model/asset name).
3. Verify with the mock: `python dev/mock_gui.py --list-devices` should show it,
   and a `devices.json` entry should render it in the GUI.

No hardware needed, and fully reproducible — a maintainer re-runs the same
`devices.json` and sees exactly what you saw. That reproducibility is what lets
the project accept fixes for devices nobody on the team owns.

## Other harnesses

`mock_cli.py` / `mock_api.py` are the CLI/API equivalents of `mock_gui.py`.
`smoke_*.py` are focused, self-contained checks (factories, portrait geometry,
metrics chain, reported-bug repros, per-OS parity, …) — run any directly with
`PYTHONPATH=src python3 dev/smoke_<x>.py`.
