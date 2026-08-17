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

Source: `/home/ignorant/Downloads/TRCCCAPEN/TRCC_decompiled/`
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
6. **RGB565 byte order** (`FormCZTV.cs:3069-3089`): `is320x320` OR `myDeviceSPIMode==2` → **big-endian word**; else little-endian. SPIMode=2 is set for **fbl 51 (mode1)** and **fbl 53 (mode3)** (`:799-806`).
7. **Round-panel edge fill (480 only)**: `RotateImgHei` blacks the 1px ring (`UCScreenImage.cs:702-711`); `RotateImgBu` edge-replicates the middle band 160-320 (`:554-563`). On 320/240/360 all three primitives are pixel-identical to plain `RotateImg`. Used only in the JPEG square branch (pm≠3→Hei, pm==3→Bu, pm==6→Hei+180).
8. **Preview is HALF SIZE for 640/1600/1920** (`UCScreenImage.cs:1758-1763` `DrawImage(myImage,0,0,W/2,H/2)`); mouse coords ×2 (`:1218-1221`).
9. **Dynamic Island (灵动岛) is 1600×720 ONLY** (`UCScreenImage.cs:1208-1288`) — `myLddVal`∈{1,2,3} × directionB → per-angle resource; default myLddVal=2, cycles 1→2→3 (never 0), persisted in theme file. `buttonLDD.Show()` only for 1600×720.
10. **Oversize guard**: JPEG ≥ 450000 bytes → drop frame, `myTempDeviceJpgYSL -= 5` (`FormCZTV.cs:2715-2719`).
11. **ThemeML portrait/landscape sub-swap** (`pmSub<5` vs `≥5`) exists for **854/960/800 only** (`FormCZTV.cs:917-949`); 640, 1280, 1600, 1920 have a single fixed dir.
12. **Header format split**: 64-byte `12345678` (320/480/240/640/800/854/960/1600/1920, len@[60..63]) vs 20-byte `DADBDCDD` (360/1280/320×240-default, len@[16..19]).

## Gap list — where OUR code diverges from the C# (for the consolidation)

| # | Divergence | Status | Evidence |
|---|---|---|---|
| G1 | **360×360 → base 90** (C# default branch) vs our base 0 | xfail test in place; no reporter | `FormCZTV.cs:2766`; `test_csharp_oracle_parity.py::test_360_fan_hub…` |
| G2 | **Mjolnir letterbox at 90°** — we letterbox landscape bg on portrait canvas; C# draws native-or-black + upright text | root-caused, unfixed | user screenshot; `UCScreenImage.cs:824-834` |
| G3 | **fbl 51/53 RGB565 byte order** — C# SPIMode=2 → big-endian; our profile `big_endian=False` + comment says "SPIMode=1" | **VERIFY** (don't flip blind; Frozen Warframe fbl 51 is a working shipping device) | `FormCZTV.cs:799-806,3009-3029` vs `protocol.py:90-92` |
| G4 | **Round-480 edge fill** (Hei/Bu) not implemented | cosmetic, unfixed | `UCScreenImage.cs:702-758` |
| G5 | **Widescreen animated-theme squeeze** (no widescreen branch in .zt prep) — does our video/animation path replicate or fix this? | investigate | `FormCZTV.cs:2311-2516` |

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
