# C# LCD Pipeline Audit — the device oracle, all 5 families

<!-- audit-state: origin=2.0.3.0 addresses=2.1.6.0 known-bad=UCScreenImage.cs::RotateImgBu -->
> **Audited against TRCC 2.0.3; citations re-anchored to TRCC 2.1.6.**
> Every method it documents is byte-identical in TRCC 2.1.6.
> [`AUDIT_INDEX.md`](AUDIT_INDEX.md#provenance)
<!-- /audit-state -->

Line-cited audit of the **LCD compose → rotate → wire** pipeline,
extracted from the decompile and **cross-validated** against `dev/decompiler/
rotation_trace.py` + independent per-family reads. This is the ground truth the
hexagonal consolidation (one `DeviceProfile`, resolve-once-at-connect, branch-free
render) is built from.

Source: the decompile at `core.csharp.DECOMPILE_ROOT` (`TRCC_DECOMPILE` to
override) — never a path spelled here. The literal that used to sit on this line
pointed at the ORIGIN tree named in the state block above, which is not the tree
these citations address, and is no longer on disk.
- `TRCC.CZTV/FormCZTV.cs` — init, theme-frame prep, ImageToJpg/ImageTo565 (wire).
- `TRCC.DCUserControl/UCScreenImage.cs` — GenerateImage (compose), SetMyUCScreenImage (preview), RotateImg/Hei/Bu.

Encoder is selected at `FormCZTV.cs:2180`: **`myDeviceMode == 2 → ImageToJpg` (JPEG), else `ImageTo565` (RGB565)**. Every rotation is `RotateImg((BASE − directionB) mod 360)`; BASE = the panel's physical-mount offset.

## Master per-family table

| Family | Entry (mode/pm/sub) | fbl | Res | Encoder | ThemeML dir | Canvas 0/180 → 90/270 | Wire-rot (0/90/180/270) | Header |
|---|---|---|---|---|---|---|---|---|
| **320×240 default** (Frozen Warframe, BA120, LF20, LM26) | mode1, fbl 50/51/52/53/58 | 50-58 | 320×240 | RGB565 (565 default branch) | `240320\` | 320×240 → 240×320 | 90/0/270/180 (**base 90**) | 20-byte `DADBDCDD`, 240×320 |
| **320×240 Mjolnir** | mode2, pm=5 | 50 | 320×240 | JPEG (pm==5 branch) | `240320\` | 320×240 → 240×320 | 0/270/180/90 (**base 0**) | 64-byte `12345678`, 320×240 |
| **Squares 240/320/480** | mode1/2, fbl 36/37,100/101/102,72 | — | NxN | 565 sq / JPEG sq | `NNNN\` | NxN (fixed, angle-independent) | 0/270/180/90 (**base 0**); pm6→180; pm3→Bu | 64-byte `12345678` |
| **360×360 fan-hub** | mode3→2, fbl 54 | 54 | 360×360 | JPEG (**default** branch!) | `360360\` | 360×360 (fixed) | 90/0/270/180 (**base 90** ⚠) | 20-byte `DADBDCDD`, 360×360 |
| **640×480** (Mjolnir PRO, Stream) | mode2, pm=7/14 | 64 | 640×480 | JPEG (is640 branch) | `640480\` | 640×480 → 480×640 | 0/270/180/90 (**base 0**) | 64-byte `12345678`, 640×480 |
| **WS-landscape** 854/960/800/1280 | mode2, pm=9/11/10/12; 1280 mode3→2 pm100 | 224/128 | WxH | JPEG (shared branch) | `WWWHHH\` (sub<5) / transposed (sub≥5); 1280 fixed | WxH → HxW | 0/270/180/90 (**base 0**) | 64-byte (800/854/960); **1280 = 20-byte** `DADBDCDD` |
| **WS-tall** 1600×720 / 1920×462 | mode2, pm=64/(1,48); pm=65/(1,49) | 114/192 | WxH | JPEG | `1600720\`/`1920462\` (fixed, no sub-swap) | WxH → HxW | 180/90/0/270 (**base 180**) | 64-byte `12345678` |

## Cross-cutting caveats (apply across families)

1. **`fbl` is NOT the resolution key for JPEG panels** — fbl 224 = 854/960/800 (pm disambiguates), fbl 192 = 1920/1280 variants. Key on pm+sub. (`FormCZTV.cs:730/744/758`)
2. **The `.zt` animated-theme frame prep (`FormCZTV.cs:2311-2516`) only has branches for 320/240/480/640** — every widescreen (854…1920) falls through to the **320×240/240×320** default canvas, so animated widescreen themes are squeezed. Confirmed independently by 2 agents. (`FormCZTV.cs:2459/1826`)
3. **Theme-frame prep + GenerateImage letterbox with BLACK fill**, aspect-preserving contain-fit; at 90/270 the DrawImage w/h args are **swapped** (`FormCZTV.cs:2511` `DrawImage(val, x, y, num29, num28)`) — a naive port that reuses the 0/180 arg order transposes the fit.
4. **bg fill is a WIDTH TEST, not a flag** (`UCScreenImage.cs:824-834`): if `bitmapBGK.Width ≤ canvas.Width+2` draw the theme bg at native (0,0), **else draw `bitmapBGK1`** (a solid-black default resource `P<W><H>`, extracted & confirmed black). So a landscape 00.png on a portrait canvas → **black**, not letterbox. (This is why the official app shows black+text at 90° for shipped landscape themes.)
5. **Overlay text is drawn UPRIGHT at raw element coords** (`UCScreenImage.cs:896-912`) — never rotated separately; the whole canvas rotates later in ImageToJpg. Any "rotate the whole composite" port makes text sideways = WRONG.
6. **RGB565 byte order**: `is320x320` OR `myDeviceSPIMode==2` → **big-endian word**; else little-endian (consumer sites `FormCZTV.cs:2010`, `:4168` — the two branches are byte-identical). **Which devices get SPIMode 2 is stated once, in G3 below** — this line used to name fbl 51 and 53, and 53 is wrong. Don't restate it here.
7. **Round-panel edge fill (480 only)**: `RotateImgHei` blacks the 1px ring (`UCScreenImage.cs:702-711`); `RotateImgBu` edge-replicates the middle band 160-320 (`:554-563`). On 320/240/360 all three primitives are pixel-identical to plain `RotateImg`. Used only in the JPEG square branch (pm≠3→Hei, pm==3→Bu, pm==6→Hei+180).
8. **Preview is HALF SIZE for 640/1600/1920** (`UCScreenImage.cs:1758-1763` `DrawImage(myImage,0,0,W/2,H/2)`); mouse coords ×2 (`:1218-1221`).
9. **Dynamic Island (灵动岛) is 1600×720 ONLY** (`UCScreenImage.cs:1208-1288`) — `myLddVal`∈{1,2,3} × directionB → per-angle resource; default myLddVal=2, cycles 1→2→3 (never 0), persisted in theme file. `buttonLDD.Show()` only for 1600×720.
10. **Oversize guard**: JPEG ≥ 450000 bytes → drop frame, `myTempDeviceJpgYSL -= 5` (`FormCZTV.cs:2715-2719`).
11. **ThemeML portrait/landscape sub-swap** (`pmSub<5` vs `≥5`) exists for **854/960/800 only** (`FormCZTV.cs:917-949`); 640, 1280, 1600, 1920 have a single fixed dir.
12. **Header format split**: 64-byte `12345678` (320/480/240/640/800/854/960/1600/1920, len@[60..63]) vs 20-byte `DADBDCDD` (360/1280/320×240-default, len@[16..19]).

## Gap list — where OUR code diverges from the C# (for the consolidation)

**Every row below was re-verified against the decompile this doc addresses
(see the state block above) and current `src/` on 2026-08-19.** The list as
first written was read from the ORIGIN release, not the addressed one: of its
eight rows, **one was falsified, two were already closed, and two more were
mis-stated** — so treat any un-re-verified row in this corpus as a lead, never as
evidence. Oracle-verified throughout; **none of this is glass-verified.**

| # | Divergence | Verdict | Evidence |
|---|---|---|---|
| G1 | ~~360×360 → base 90 vs our base 0~~ | **FALSIFIED** — the addressed release names it: `is640x480 \|\| is360x360 \|\| is640x172` → base 0, which is what we ship. Read off the origin release, where 360 matched no guard and fell to the base-90 default. The `xfail` it justified had been marking correct code as broken, and is retired. | `FormCZTV.cs:3760` — `(is640x480 \|\| is360x360 \|\| is640x172) ? directionB switch`; ours `core/protocol.py:272-280` |
| G2 | ~~Mjolnir letterbox at 90°~~ | **CLOSED** — `bg_fit` implements the C# width test (`src_w <= dst_w + 2` → native at (0,0), else solid black, never letterboxed), and warns on the black branch. | `core/ports.py:1008-1032` vs `UCScreenImage.cs:824-834` |
| G3 | **RGB565 byte order — SPIMode 2 is unmodelled** | **REAL, and the original row named the wrong FBLs.** The addressed release sets `myDeviceSPIMode = 2` at exactly three sites: `mode==1 && fbl==51` (`:1048`), `mode==3 && fbl==49` (`:1052`), `mode==2 && pm==50` (`:880`, which also forces `mode=3`, `fbl=50`). **FBL 53 is not among them.** Both consumer sites are byte-identical to the `is320x320` branch, i.e. SPIMode 2 ⇒ big-endian. We ship FBL 51 `big_endian=False`, have no FBL 49 at all, and label PM 50 "(SPI mode 2)" in `_PM_TO_FBL_OVERRIDES` while dropping the consequence. | `FormCZTV.cs:880,1048,1052`; consumers `:2010`, `:4168`; ours `core/protocol.py:118-147` |
| G4 | **Round-480 edge fill (Hei/Bu) not implemented** | **OPEN** (cosmetic) — no implementation anywhere in `src/`. | `UCScreenImage.cs:702-758` |
| G5 | **Widescreen animated-theme squeeze** | **DIVERGENT BY DESIGN** — the C# `.zt` prep branches only for 320/240/480/640, so widescreen falls to the 320×240 default canvas. We compose at the device canvas instead. Better, deliberate, and **not glass-verified for a widescreen `.zt`.** | `FormCZTV.cs:2311-2516` vs `services/display.py` |
| G6 | **GPU→CPU power silent fallback** | **REAL.** The addressed release tries `"Total Graphics Power (TGP)"` → `"GPU Power"` → `"GPU ASIC Power"` and, if all three miss, assigns `GpuPower = CpuPower`. Ours returns `None`, so the field renders empty where the official app shows a number. (The original row cited `:944`; that is stale.) | `UCSystemInfo.cs:967-970` vs `adapters/sensors/_lhm.py:520-524` |
| G7 | ~~LED global ×0.4 brightness cap~~ | **CLOSED** — `_COLOR_SCALE = 0.4` is applied. | `adapters/device/led.py:48` |
| G8 | **Crop aspect thresholds are not verbatim** | **REAL (narrow).** The C# uses magic doubles that must not be recomputed: `is854x480` → **0.56206** (the true ratio is 0.5618, so the vendor's own constant is already an approximation) where ours is **0.5621**; and `1920x440` → **0.229166666**, a family absent from our `_ASPECT_RATIOS` entirely though `ENCODE_ROTATIONS` knows it. Images landing between the two values classify differently than the official app. | `UCImageCut.cs:433,476,625` vs `ui/gui/display_mode_panels.py:514-519` |

**G3 is gated by nothing.** `tests/test_csharp_conformance.py` is bulk-only by
design and states the rule — *"adding a wire means pinning its call site first"*
— and FBL 51/49 live on wires whose C# entry point is unpinned. No test in the
suite asserts endianness for them. Pin the call site, extend the harness, then
act; do not flip a shipping panel's byte order on a reading alone.

## Verified (our code MATCHES the C#)

Wire rotation for 18/19 device fingerprints (per `rotation_trace.py`): 320×240 RGB565 base 90, Mjolnir JPEG base 0, FW360 480 pm6 base 180, squares base 0, 640 base 0, all WS-landscape base 0, WS-tall base 180. Header dims, ThemeML dirs, PM→resolution disambiguation (`test_csharp_oracle_parity.py`).

## "Inside and out" — remaining subsystems NOT yet audited

56 files / 70,662 lines total. Audited: the LCD pipeline (`FormCZTV.cs` + `UCScreenImage.cs`, ~8.6K). Remaining, by size:
- **LED subsystem** — `FormLED.cs` (16,751), `UCScreenLED.cs` (10,141), `FormKVMALED6.cs` (2,084), `UCLEDMemoryInfo.cs`. Effects, segment-display masks, RGB.
- **Discovery / connection** — `Form1.cs` (1,734), `UCDevice.cs` (1,438): device enumeration, handshake dispatch per wire, hotplug.
- **Theme / mask / video** — `UCVideoCut.cs` (2,509), `UCImageCut.cs` (2,025), `UCBoFangQiKongZhi.cs` (1,880, playback), `UCThemeLocal/Web/Mask.cs`, `UCDongHuaLianDong.cs` (animation linkage).
- **Metrics / overlay / clock** — `UCSystemInfo.cs` (1,120), `UCXiTongXinXi.cs` (979), `UCXiTongXianShi*.cs`, `UCShiJianXianShi.cs` (824, clock), `FormSystemInfo.cs`.
- **Misc** — `UCAbout`, `UCComboBox{A,B,C}`, `UCColorA`, `Resources.cs` (generated).

Audit each the same way (per-file agent, line-cited caveats, verify load-bearing claims) → fold into a `device-spec.json` + this doc → then consolidate.

## `StartPipeline` — 2.1.6's frame pipeline (added 2026-08-22)

**New in 2.1.6** (`worklist.json` lists it under `FormCZTV.cs` → `new`), and it
is the shape of the whole render path in this release. `FormCZTV.cs:2730-2815`.

Three `Task.Run` stages joined by three **bounded** queues:

    _firstImages  (cap 2)  →  _secondImages (cap 2)  →  _thirdImages (cap 2)

declared at `FormCZTV.cs:506`, `FormCZTV.cs:508`, `FormCZTV.cs:510`, with one
shared `CancellationTokenSource` at `FormCZTV.cs:512`.

| stage | does | cite |
|---|---|---|
| 1 | takes a decoded frame, installs it as `bitmapBGK` and **disposes the previous background**, then `SetUCStateTask1(directionB, UCXiTongXianShiSubArray, shanPingCount)` — compose the overlay elements at the current rotation | `FormCZTV.cs:2732` |
| 2 | `SetUCStateTask2(frame)` | `FormCZTV.cs:2763` |
| 3 | copies to `MyImageSet` + `Invalidate` (**preview**), then `myDeviceMode == 2 ? ImageToJpg : ImageTo565` (**wire**), then disposes | `FormCZTV.cs:2784` |

Every stage disposes its input in a `finally`.

### The three facts worth carrying

1. **Memory is bounded by construction — at most ~6 frames live.** Capacity 2
   per queue, three queues. This is the discipline whose absence cost us ~7 GB
   on video themes (`project_video_theme_memory_and_freeze`): we pre-composited
   every frame AND held every decoded frame. The vendor's answer is not a
   smarter cache, it is a queue that cannot grow.
2. **Backpressure DROPS, it does not stall.** Stages 1 and 2 each test
   `if (_secondImages.Count < 2)` / `if (_thirdImages.Count < 2)` *before*
   producing (`FormCZTV.cs:2738`, `FormCZTV.cs:2769`), so when the device is
   slower than the source the pipeline sheds frames instead of blocking the
   decoder. A bounded queue alone would block; the explicit count test is what
   makes it lossy on purpose.
3. **Preview and wire are the SAME composite.** Stage 3 hands one bitmap to
   both `MyImageSet` (the on-screen preview) and the encoder — they are not
   composed twice and cannot drift. Our split (`build_frame` captures
   `preview_surface` before the device-rotate steps, `959b1648`) is a
   deliberate divergence for rotate panels, not an accident, and this is the
   line it diverges from.

The encoder is selected **per frame** here (`myDeviceMode == 2`), not resolved
once at connect. Our resolve-once seam (`DeviceProfile` carrying `jpeg`) is the
better form and produces the same bytes; noting it so a future reader does not
"fix" ours back toward this.
