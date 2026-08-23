# C# FormLCD Audit — the projection subsystem, and why it is dead in 2.1.6

<!-- audit-state: origin=2.1.6.0 addresses=2.1.6.0 -->
> **Audited against TRCC 2.1.6 — the release we port.**
> First doc in this corpus whose origin IS its target: every citation below was
> read from `core.csharp.DECOMPILE_ROOT`, not re-anchored onto it.
> [`AUDIT_INDEX.md`](AUDIT_INDEX.md#provenance)
<!-- /audit-state -->

Covers the five files the corpus had never opened, listed as `unaudited` in
`worklist.json`:

| file | lines | methods |
|---|---|---|
| `TRCC.LCD/FormLCD.cs` | 5082 | 87 |
| `TRCC.DCUserControl/UCVideoCutF.cs` | 1651 | 29 |
| `TRCC.DCUserControl/UCImageCutF.cs` | 726 | 17 |
| `TRCC.DCUserControl/UCShortcut.cs` | 275 | 12 |
| `TRCC.LCD/FormLCDImageCut.cs` | 74 | 5 |

## They are one subsystem, not five gaps

`UCImageCutF` and `UCVideoCutF` are constructed **only** by `FormLCD`
(`FormLCD.cs:4757`, `FormLCD.cs:4766`) and by nothing else in the decompile.
`FormLCDImageCut` is `FormLCD`'s screen-region marker. So four of the five are
`FormLCD`'s own parts, and the corpus missed them together because it was built
around `FormCZTV` — the ordinary LCD path — and this is the *other* form.

`FormLCD` is a near-complete parallel of `FormCZTV`: its own theme load / save /
import / export (`FormLCD.cs:1081` `ReadFileTheme`, `FormLCD.cs:3358` `buttonBCZT_Click`,
`FormLCD.cs:3585` `buttonDaoChu_Click`, `FormLCD.cs:3716` `buttonDaoRu_Click`), mask select
(`FormLCD.cs:2219` `MengBanSelect_Open`), web themes (`FormLCD.cs:2144` `ThemeWeb`), background select
(`FormLCD.cs:1768` `buttonSelectBackgroundImage`), image + video cutters
(`FormLCD.cs:1405` `ImageCut_Open`, `FormLCD.cs:1460` `VideoCut_Open`), system info (`FormLCD.cs:1627` `GetSystemInfo`),
brightness (`FormLCD.cs:4651` `buttonLDD_Click`), and its **own** shared-memory block
(`FormLCD.cs:314` `InitMemorySize`) distinct from `FormCZTV`'s.

Two capabilities it has that `FormCZTV` does not:

* **DPI awareness** — `GetDeviceCaps` P/Invoke (`FormLCD.cs:661`) feeding
  `GetScreenScalingFactor` (`FormLCD.cs:663`). The ordinary path has no equivalent, which
  is context for #220 (HiDPI blowing up the wire frame).
* **Screen-region capture** — `FormLCDImageCut` is a borderless, always-on-top,
  black `2560x720` form whose `FormClosing` is unconditionally cancelled
  (`FormLCDImageCut.cs:39-41`); `FormLCD` fills its `myX/myY/myW/myH` from the
  monitor's own `Bounds` and shows/hides it to mark the captured region
  (`FormLCD.cs:931-948`).

## The finding: in 2.1.6 this subsystem cannot run

Three independent facts, each verified against the shipped installer rather
than inferred from the source:

1. **Its helper binary is not shipped.** `FupingJiance` (`FormLCD.cs:1593`)
   scans for a process named `TRCCLCDAPP` and, if absent, launches
   `Data/LCD/TRCCLCDAPP.exe` (`FormLCD.cs:254`, `FormLCD.cs:319`). That file does not exist in the
   2.1.6 installer payload — 13,726 files, 2,073 folders, no match, and no
   `Data/LCD` directory at all. This is a **fourth** binary, beyond the three
   named in `project_the_audit_covers_one_binary_of_three`, and it is the one
   that never shipped.
2. **Its theme data is not shipped.** `FormLCD`'s family reads `2560720` /
   `7202560` on all three data axes. The C# names those directories; the
   installer carries none of them. `audit_csharp.py` prints this as an
   installer-vs-C# disagreement on every axis.
3. **Its resolution has no encoder arm.** `ImageToJpg` (`FormCZTV.cs:3641-4101`)
   branches for thirteen panels and `ImageTo565` (`FormCZTV.cs:4102-4261`) for four;
   `2560x720` appears in neither, so there are no header bytes, no dimensions
   and no rotation for it anywhere in the encode path.

A fourth, weaker signal points the same way: `is2560x720` is assigned `true` in
**both** arms of `if (fbl == 257) … else …` (`FormLCD.cs:931`, `FormLCD.cs:949`), so
inside this form it is not a discriminator at all — it is a marker meaning "this
is FormLCD". Reading it as a resolution selector is what made an earlier audit
pass report `2560x720` as a panel we had failed to support.

**Consequence for the port: there is nothing here to port.** The subsystem is
vestigial in the release we build against, and any attempt to implement
`2560x720` would be inventing a wire format, not reproducing one.

## Overlay element mode 5: authored, persisted, and never drawn

`UCShortcut` is not a panel — it is the editor for an **overlay element kind we
do not implement**. `MyShortcut` (`UCThemeSetting.cs:67`) routes its three
modes into the system-display element table, and the element is dispatched
under different delegate commands from every other kind — `19`/`275` instead of
`3`/`259` (`UCXiTongXianShi.cs:207`, `UCXiTongXianShi.cs:221`), which
`UCThemeSetting.cs:133` answers by showing the shortcut editor. It is offered
in the picker (`UCThemeSetting.cs:208` → `UCXiTongXianShiAdd(5, 64)`, where 64
is the initial icon size from `UCScrollC`) and it is written to the DC
(`FormCZTV.cs:7163`, `FormCZTV.cs:7306`).

`LoadIconForPathOrName` (`UCXiTongXianShiSub.cs:~330-450`) really does resolve
`myText` as a path:

| `myText` is | loaded as |
|---|---|
| `.png` / `.jpg` / `.jpeg` / `.bmp` / `.gif` | the image itself, read from disk |
| `.ico` | `new Icon(path).ToBitmap()` |
| `.exe` / `.dll` | `Icon.ExtractAssociatedIcon` → bitmap |
| a bare name | `ResolveExecutablePath` → then its icon |

**But the picture never reaches the panel.** An earlier revision of this doc
called mode 5 "the one genuinely portable thing" and recommended porting the
image half. That was wrong, and the correction is the finding:

* The loaded image is assigned to the editor control's own `BackgroundImage`
  (`UCXiTongXianShiSub.cs:291-300`) — a WinForms control property, not the frame.
* The only compositor for the wire frame is `UCScreenImage.cs`, and both of its
  element loops — `GenerateImage:1122-1142` and `GenerateImage2:1529-1549` —
  draw **`DrawString` only**, gated on `text.Length > 0`.
* Mode 5 hides all three labels (`InitUCXiTongXianShiSub`, case 5) and no code
  path ever assigns `label2.Text` for it; `InitializeComponent` gives the label
  no designer default. So that guard is permanently false for a mode-5 element.
* `GenerateImage1` (`:1236-1519`) never touches the element array at all.
* `FormLCD.cs` contains no `DrawString`, no `SetUCState`, no `GenerateImage`.
* `DrawToBitmap` appears **nowhere in the decompile**, so "it renders because it
  is a control" is ruled out too — the frame is never captured from controls.
* The mouse hit-test carries the same text-only guard
  (`UCScreenImage.cs:2027-2045`), so a mode-5 element is not even draggable.

So mode 5 belongs with the rest of this subsystem: implementing it would
**invent** behaviour rather than reproduce it, exactly as `2560x720` would.

Our `OverlayElement.type` is `text` / `metric` / `clock`
(`core/commands/device.py`, `AddOverlayElement.execute` guard) and
`core.models.OverlayMode` stops at `CUSTOM = 4`. `services/_dc.py`
`_build_dd_element` ends in `case _: return None`, so a mode-5 element in a
Windows-authored DC is skipped — which **already matches what the C# puts on
the glass**. The only residue is that re-saving such a theme drops the element,
costing the user nothing on the panel.

**There is therefore no capability in these 7,808 lines that is both missing
from our port and implementable without hardware.**

## What this does not cover, measured

These five files were listed `unaudited` in `worklist.json`, which tracks
whether a file has been DIFFED between releases — not whether it is covered.
They were largely covered already: of the 143 methods the structural map holds
for them, **21 remain dark** after this doc.

| file | methods in map | dark |
|---|---|---|
| `FormLCD.cs` | 83 | 7 |
| `UCVideoCutF.cs` | 28 | 9 |
| `UCImageCutF.cs` | 16 | 3 |
| `UCShortcut.cs` | 11 | 2 |
| `FormLCDImageCut.cs` | 5 | 0 |

    python3.12 dev/decompiler/audit_coverage.py --dark UCVideoCutF.cs

Documenting those 21 line-by-line is deliberately NOT done here. Naming, with
evidence, the fact that the subsystem cannot run in the target release is worth
more than prose about methods that never execute — and the one candidate that
looked portable (mode 5) is settled above: it is not. If `TRCCLCDAPP.exe` ever
appears in a future installer, this doc is the place that says what to read
first.
