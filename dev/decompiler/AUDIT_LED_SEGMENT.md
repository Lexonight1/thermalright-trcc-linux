# AUDIT — LED Segment-Display Compose

<!-- audit-state: origin=2.0.3.0 addresses=2.1.6.0 known-bad=none -->
> **Audited against TRCC 2.0.3; citations re-anchored to TRCC 2.1.6.**
> Every method it documents is byte-identical in TRCC 2.1.6.
> [`AUDIT_INDEX.md`](AUDIT_INDEX.md#provenance)
<!-- /audit-state -->

Source of truth (verbatim, line-cited):
- `~/Downloads/TRCCCAPEN/TRCC_decompiled/TRCC.DCUserControl/UCScreenLED.cs` (10,143 lines) — the 7-/13-segment **preview UserControl**.
- `~/Downloads/TRCCCAPEN/TRCC_decompiled/TRCC.KVMALED6/FormKVMALED6.cs` (2,084 lines) — a **separate ARGB 6/10-channel lighting controller**, NOT a segment display.

Every claim below quotes the code or cites `file:line`. Where a table is long, the exact line ranges are given so the full block can be re-read.

---

## 0. Scope correction — the two files are different device classes

- **`UCScreenLED`** (`UCScreenLED.cs:8` `public class UCScreenLED : UserControl`) is the segment/matrix METRIC display. It computes which segments are lit (`isOn[]`), their colors (`ledColor[]`), and their rectangles (`ledPosition[]`), then paints them in `OnPaint`. **It contains no USB send** (grep for `usb|SendData|WriteFile|byte[]` in method bodies returns nothing) — it is preview-only. The wire send for these panels lives in `FormLED` (out of scope for this audit).
- **`FormKVMALED6`** is an ARGB channel controller (10 RGB channels, modes, brightness), persisted to `proMode.dc`. It is **not** a 7-segment metric display; it is the "6/10-channel lighting" device. Included here only for its wire format (§6).

---

## 1. UCScreenLED — purpose & lifecycle

### 1.1 Data model (the "segment buffer")
Three parallel active arrays, swapped per product by the `ReSet*` methods:

- `ledPosition` (UCScreenLED.cs:2144) — public 2-D `int` array; one row per segment index, four columns holding that segment's rectangle as x, y, w, h.
- `isOn` (UCScreenLED.cs:2146) — public `bool` array; one entry per segment index, true when that segment is lit.
- `ledColor` (UCScreenLED.cs:2148) — public 2-D `byte` array; one row per segment index, three columns holding R, G, B.

Per-product concrete arrays are declared as fields, e.g. `ledPosition1`/`isOn1`/`ledColor1` (`:464`, `:498`, `:505`), `ledPosition2` (84 rows, `:539`), … up to `ledColorLF13`/`ledPositionLF13` (`:19`/`:23` region). `ledPosition1` has 30 rows (`new int[30,4]` `:464`); `ledPosition2` has 84 rows (`new int[84,4]` `:539`).

### 1.2 Logical segment→index constants (remapped per product)
Segment identity is a set of named `int` fields that index into the active arrays. Standard 7-seg names are `LEDA1..LEDG1` (digit 1) through `LEDG13` (digit 13):

- `LEDA1` (UCScreenLED.cs:140) — public `int` field, default 9; the first of digit 1's seven segment indices.
- `LEDG1` (UCScreenLED.cs:154) — default 15; A..G are the 7 segments of digit 1, so digit 1 spans indices 9-15 by default.
- `LEDG2` (UCScreenLED.cs:168) — default 22; the same A..G run repeats once per digit, on through `LEDG13` (UCScreenLED.cs:320).

An **alternate 13-segment naming** exists — `LEDH1..LEDM1` … `LEDH6..LEDM6`:

- `LEDH1` (UCScreenLED.cs:392) — public `int` field, default 13; the first of the six extra segment indices for digit 1.
- `LEDM1` (UCScreenLED.cs:402) — default 18; H, I, J, K, L, M are 6 extra segments which together with A..G make 13 per digit.

Zone / label / decoration indices — each a named `int` field carrying a default index into the active arrays:

- `Logo1` = 0, `Logo2` = 1 (UCScreenLED.cs:108) — the two logo segments.
- `MTNo` = 1, `GNo` = 2 (UCScreenLED.cs:112) — the "MT" and "G" unit markers.
- `WATT` = 1, `MHz` = 5 (UCScreenLED.cs:116) — the "W" and "MHz" unit markers.
- `Cpu1` = 2, `Cpu2` = 3, `Gpu1` = 4, `Gpu2` = 5 (UCScreenLED.cs:120) — the CPU/GPU zone labels.
- `SSD` = 6, `HSD` = 7, `BFB` = 8 (UCScreenLED.cs:128) — the SSD / HDD / "%" (百分比) markers.
- `ZhuangShi1` = 93 through `ZhuangShi32` = 124 (UCScreenLED.cs:322-384) — 32 consecutive decorative/frame segments.
- `Shijian1` = 0, `Shijian2` = 1, `Riqi` = 2 (UCScreenLED.cs:386) — the clock separators and the date marker.

**These are defaults; every `ReSet*` OVERWRITES them for that product** (see §2). A port must remap per style, never hardcode the default indices.

### 1.3 Construction
- `UCScreenLED` constructor (UCScreenLED.cs:2152) — runs the designer's `InitializeComponent`, then points the three active arrays at style 1's concrete arrays (`ledPosition` → `ledPosition1`, `isOn` → `isOn1`, `ledColor` → `ledColor1`), loads `Resources.DFROZEN_HORIZON_PRO` into the background image `imageBk`, and calls `SetMyNumeral(36)` so the control comes up already showing the seed value 36.

`nowLedStyle` default `1` (`:10`); `myLedMode` default `4` (`:12`).

---

## 2. Per-product layout — the `ReSet*` methods (style selection)

Each `ReSetUCScreenLEDn()` (a) points `ledPosition/isOn/ledColor` at that product's arrays, (b) sets `nowLedStyle`, (c) **remaps every segment/zone index** to that product's array offsets, and (d) loads any artwork resources. `ReSetUCScreenLED1()` is empty (`:2164-2166`) — style 1 is the constructor default.

| Method | `nowLedStyle` | Array | Artwork resource(s) | Zone remap highlights | Cite |
|---|---|---|---|---|---|
| ReSet1 | 1 (default) | ledPosition1 (30) | `DFROZEN_HORIZON_PRO` (Frozen Horizon Pro) | defaults | 2162 / 2158 |
| ReSet2 | 2 | ledPosition2 (84) | — | Cpu1=0..BFB1=9, LEDA1=10.. | 2166-2203 |
| ReSet3 | 3 | ledPosition3 | — | Cpu1=0 WATT=1 SSD=2 HSD=3 BFB=4 Gpu1=5 | 2205-2275 |
| ReSet4 | 4 | ledPosition4 | — | SSD=0 MTNo=1 GNo=2 LEDA1=3.. | 2277-2314 |
| ReSet5 | 5 | ledPosition5 | — | Cpu1=0 Gpu1=1 SSD=2 HSD=3 WATT=4 MHz=5 BFB=6 | 2316-2415 |
| ReSet6 | 6 | ledPosition6 | `Dch2`,`Dch3`,`Dch4` (imageBk61/62/63) | Cpu1=0 Gpu1=1 SSD=2 WATT=4 | 2417 / 2423-2425 |
| ReSet7 | 7 | — | `Dch1` (imageBk71) | uses `ZhuangShi21` gate in paint | 2521-2527 |
| ReSet8 | 8 | — | `Dchcz1` (imageBk81) — **CZ1** | Cpu1=0 Gpu1=1 | 2646-2652 |
| ReSet9 | 9 | — | — | — | 2673-2678 |
| ReSet10 | `:10` | — | — | SSD=0 MTNo=BFB=1 GNo=MHz=2 | 2739-2744 |
| ReSetLF15 | `:11` | ledPositionLF15 | — | Cpu1=0 Gpu1=1 SSD=2 WATT=4 MHz=5 | 2785-2790 |
| ReSetLF13 | `:12` | ledPositionLF13 (1 cell 460×460) | `D0rgblf13` (imageLF13); bg `DLF13` | whole panel = one RGB cell | 2886-2892 / 10133 |

**Product NAMES (AX120/PA120/AK120/LC1/LF8/LF10/LF12/CZ1 …) are not in this file.** The caller (`FormLED`, out of scope) selects the style by calling the matching `ReSet*`. In-file evidence links style 8 → **CZ1** (`Resources.Dchcz1`, `:2654`), style 12 → **LF13** (`Resources.D0rgblf13`/`DLF13`), style 11 → **LF15**. Others are addressed by number only.

---

## 3. Digit → segment truth tables (the masks)

### 3.1 Standard 7-segment table (A=top, B=upper-right, C=lower-right, D=bottom, E=lower-left, F=upper-left, G=middle)
Canonical block: `SetMyNumeral(int val)` ones-digit switch, `UCScreenLED.cs:3259-3351` (`case n: isOn[LEDA3..LEDG3]=…`). Decoded (segment ON = `true`):

| Digit | A | B | C | D | E | F | G | cite |
|---|---|---|---|---|---|---|---|---|
| 0 | 1 | 1 | 1 | 1 | 1 | 1 | 0 | 3259 |
| 1 | 0 | 1 | 1 | 0 | 0 | 0 | 0 | 3268 |
| 2 | 1 | 1 | 0 | 1 | 1 | 0 | 1 | 3277 |
| 3 | 1 | 1 | 1 | 1 | 0 | 0 | 1 | 3286 |
| 4 | 0 | 1 | 1 | 0 | 0 | 1 | 1 | 3295 |
| 5 | 1 | 0 | 1 | 1 | 0 | 1 | 1 | 3304 |
| 6 | 1 | 0 | 1 | 1 | 1 | 1 | 1 | 3313 |
| 7 | 1 | 1 | 1 | 0 | 0 | 0 | 0 | 3322 |
| 8 | 1 | 1 | 1 | 1 | 1 | 1 | 1 | 3331 |
| 9 | 1 | 1 | 1 | 1 | 0 | 1 | 1 | 3340 |

This exact table is repeated verbatim in every value overload (e.g. `:3051-3152`, `:5259-5559`, `:9401-9493`). No product deviates from it for `LEDA..LEDG`.

### 3.2 Leading-zero suppression (value displays)
- **Hundreds digit** (`num = val/100`): `case 0` = all segments OFF (blank), `:3053-3061`.
- **Tens digit** (`num2`): `case 0` = all OFF **iff `num==0`**, else full "0" glyph, `:3153-3176`. The `0` case branches on the hundreds digit — when `num` is 0 it clears every one of `isOn[LEDA2]` through `isOn[LEDG2]` so the tens place goes blank (UCScreenLED.cs:3154-3163); otherwise it lights the full "0" glyph, `LEDA2` through `LEDF2` on with `LEDG2` (the middle bar) off (UCScreenLED.cs:3165-3172).
- **Ones digit** (`num3`): `case 0` = always the "0" glyph (`:3261`). Ones place never blanks.

### 3.3 13-segment table (A–M) — SetMyNumeralNew ONLY
`SetMyNumeralNew(int cpuTemp,int gpuTemp)` (`:8126`) uses a **completely different 13-segment glyph set** writing `isOn[LEDA1..LEDG1]` AND `isOn[LEDH1..LEDM1]`. Decoded from `:8131-8245`:

| Digit | A B C D E F G | H I J K L M | cite |
|---|---|---|---|
| 0 | 0 0 0 0 0 0 0 | 0 0 0 0 0 0 | 8131 |
| 1 | 0 0 1 1 1 1 1 | 0 0 0 0 0 0 | 8146 |
| 2 | 1 1 1 1 1 0 1 | 1 1 1 1 0 1 | 8161 |
| 3 | 1 1 1 1 1 1 1 | 1 1 0 1 0 1 | 8176 |
| 4 | 1 0 1 1 1 1 1 | 0 0 0 1 1 1 | 8191 |
| 5 | 1 1 1 0 1 1 1 | 1 1 0 1 1 1 | 8206 |
| 6 | 1 1 1 0 1 1 1 | 1 1 1 1 1 1 | 8221 |
| 7 | 1 1 1 1 1 1 1 | … (block continues 8236+) | 8236 |

**This table is NOT the 7-seg table** — reusing §3.1 for a "New"/13-segment product renders wrong glyphs. Digits 7-9 continue in the same 13-line-per-case block through `:8302`ish.

### 3.4 Unit glyphs drawn ON a digit (letters, not just numbers)
`SetMyNumeral(int mode,int val)` with `mode<=0` draws a **temperature-unit letter on digit 4** (`LEDA4..LEDG4`), `:5236-5255`:

- `mode == 0` → the Celsius glyph "C": `LEDA4`, `LEDD4`, `LEDE4`, `LEDF4` lit; `LEDB4`, `LEDC4`, `LEDG4` dark.
- any other `mode` → the Fahrenheit glyph "F": `LEDA4`, `LEDE4`, `LEDF4`, `LEDG4` lit; `LEDB4`, `LEDC4`, `LEDD4` dark.

---

## 4. Metric → display mapping (the overloads)

Digit decomposition idiom: `num = v/100; num2 = v%100/10; num3 = v%10` (`:3048-3050`). 4-digit uses `/1000, /100%10, %100/10, %10` (`:5574-5577`); 5-digit uses `/10000 …` (`:6348-6352`).

| Overload | Purpose | Metric layout | Cite |
|---|---|---|---|
| `SetMyNumeral(int val)` | generic 3-digit value → LED1/2/3 | leading-zero suppressed | 3044 |
| `SetMyNumeral(int cpuTemp,int cpuUse,int gpuTemp,int gpuUse)` | 4 metrics, each a 3-digit block | cpuTemp→3354, cpuUse→3658, gpuTemp→3875, gpuUse→4179 | 3352 |
| `SetMyNumeral(int watt,int temp,int use)` | 3 metrics | watt→4400, temp→4704, use→5008 | 4398 |
| `SetMyNumeral(int mode,int val)` | unit-switched value | `mode<=0`→temp(3-digit)+C/F glyph+`isOn[SSD]` (5231); `mode==1`→`isOn[MTNo]` 4-digit (5562-5575); `else`→`isOn[GNo]` 4-digit (5568) | 5227 |
| `SetMyNumeralHardDisk(int mode,int val)` | disk metric | `mode<=0`→`isOn[SSD]` (5998); else `isOn[BFB]`/`isOn[MHz]` (6336-6344); **5-digit** value `/10000` (6346) | 5994 |
| `SetMyNumeral(int temp,int watt,int mhz,byte use)` | 4 metrics incl. MHz | 4 blocks | 6875 |
| `SetMyNumeralNew(int cpuTemp,int gpuTemp)` | **13-segment** product, 2 metrics | cpuTemp (A–M digit1) + gpuTemp | 8124 |
| `SetMyNumeralNew(int val)` | **2-digit** 7-seg (tens LED1, ones LED2) | num2→LED1 (9209), num3→LED2 (9300) | 9180 |
| `SetMyTimer(int M,int d,int h,int m,int w)` | clock/date | h→LED1/2 (9397), m→LED3/4 (9585), M→month, d→day, w→weekday markers | 9395 |

**Zone/unit markers are mutually exclusive toggles** set inside these overloads: `isOn[SSD]` / `isOn[MTNo]` / `isOn[GNo]` (`:5233-5235`, `:5564-5572`), `isOn[BFB]` / `isOn[MHz]` (`:6338-6346`). They light which unit LABEL segment shows next to the number.

---

## 5. Compose / color / zone rendering (`OnPaint`, `:2897-3044`)

The buffer is rendered to the control's `Graphics` (this is the PREVIEW; the device wire buffer is `isOn[]`+`ledColor[]`):

- **`myLedMode == 4` = artwork-image mode**; any other value = **solid-color mode** using `ledColor`.
- **Generic path** (all styles except 8 & 12), `:3031-3042` — walks every row `k` of `ledPosition` and, for each index whose `isOn[k]` is true, builds a solid brush from that segment's own colour triple (`ledColor[k]` columns 0/1/2 → R/G/B) and fills that segment's rectangle (`ledPosition[k]` columns 0-3 → x, y, w, h). One rect and one RGB per segment; nothing is drawn for an OFF segment. The artwork/mask `imageBk` is then drawn LAST, at origin (0, 0), on top of the filled segments (UCScreenLED.cs:3041).
- **Style 6** (`:2913-2942`): mode 4 draws `imageBk61/62/63` at fixed offsets `(26,17)/(23,221)/(293,274)`; else fills those rects with `ledColor[0]`.
- **Style 7** (`:2943-2974`): gated on `isOn[ZhuangShi21]`; draws `imageBk71` or fills rects `(30,217,…,70)` and `(195,268,70,170)` with `ledColor[ZhuangShi21]`.
- **Style 8 (CZ1)** (`:2975-3008`): mode 4 draws `imageBk81` then **blacks out** every OFF segment (`Color.Black` fill, `:2986-2988`); else fills only ON segments — i.e. CZ1 is an "artwork minus dark segments" panel.
- **Style 12 (LF13)** (`:3009-3030`): single cell `isOn[0]`; draws `imageLF13` or fills the whole `ledPosition[0]` rect with `ledColor[0]`.

Per-segment color: `ledColor[i]` is an independent `[R,G,B]` per segment index — every segment can be a different color (default arrays seed digit segments red `{255,0,0}` and logos white `{255,255,255}`, `:505-537`).

---

## 6. Wire format — `FormKVMALED6` (separate ARGB device, NOT segment)

Packet constants (`:22-42`) — the two header bytes, the command codes, and the channel geometry:

| Constant | Value | Constant | Value |
|---|---|---|---|
| `USB_PACKED_Head` | `:220` | `USB_PACKED_Head1` | 221 |
| `USB_PACKED_ONOFF` | 0 | `USB_PACKED_STATE` | 1 |
| `USB_PACKED_LEDMS` | `:104` | `USB_PACKED_FAN` | 6 |
| `USB_PACKED_LED` | `:16` | `USB_PACKED_GET_STATE` | 2 |
| `USB_PACKED_AUDIO` | 4 | `USB_LED_COUNT` | 10 |
| `USB_LED_RGB` | 3 | | |

State model: 10 channels — `myOnOff[10]`, `myModeS[10]`, `myBrightnessS[10]`, `myRGB[10,3]` (`:62-82`). Persisted to `Data\KVMALED6\proMode.dc` with a **`220` magic first byte** (`:583-595` read, `:624-634` write).

Send seam — all packets go through `SendDeviceData(cmd,ch,mode,data)` → `delegateForm(16, {cmd,ch,mode}, data, this)` (`:708-712`):
- `SendLEDDataOnOff` → `SendDeviceData(0,0,myMode, byte[10] myOnOff)` (`:714-737`).
- `SendLEDData` → `SendDeviceData(16,0,myMode, byte[18] { brightness, speed, R,G,B, 0,0,0, myChannel[0..9] })` (`:739-767`).
- `SendLEDMSData(m)` → `SendDeviceData(104,(byte)m,myModeS[0], byte[60] { onOff[10], modeS[10], brightnessS[10], RGB[10×3] })` (`:783-853`).
- `SendStateData` → `SendDeviceData(1,0,0,null)` (`:857-864`).

Mode codes (`ButtonMode_Click` switch, `:884-931`): UI button → internal `myMode` values 0-10, 100 (static color, `:936-941`), 201/202/203 (light-show, `:922-929`). Brightness scroll maps `0-255 → 0-100` (`:524`). This device has **no digit/segment computation** — it is channel RGB only.

---

## 7. Caveats a naive port must not miss

1. **Two glyph encodings coexist.** `LEDA..LEDG` = standard 7-seg (§3.1); `SetMyNumeralNew(cpuTemp,gpuTemp)` uses a **13-segment A–M table** (§3.3) that is entirely different. Applying the 7-seg table to a 13-seg product renders garbage.
2. **Segment indices are remapped per product.** The `LEDA1..`/`SSD`/`MTNo`/`GNo` fields are overwritten by each `ReSet*` (§2). Port must resolve indices from the active style, never from the field defaults.
3. **Leading-zero suppression is display-type-dependent.** Value displays blank leading zeros (hundreds always, tens iff hundreds==0; §3.2). `SetMyTimer` does **not** — hour tens `case 0` is a full "0" glyph (`:9403`), so clocks show `08:05`.
4. **Unit letters are drawn as segment glyphs on a digit**, not fixed labels: `mode==0`→"C" (A,D,E,F), else "F" (A,E,F,G) on `LED4` (`:5238-5254`).
5. **Digit count varies by overload** (2/3/4/5 digits) → the decomposition divisor changes (`/100`, `/1000`, `/10000`; §4). `SetMyNumeralHardDisk` is 5-digit (`:6348`).
6. **`myLedMode==4` is artwork mode, not a color.** In mode 4 `ledColor` is ignored and bitmaps are drawn (`:2915`, `:2945`, `:2977`, `:3011`); every other value fills solid rects from `ledColor`.
7. **Style 8 (CZ1) inverts the compose**: it draws the full artwork then **blacks out OFF segments** (`Color.Black`, `:2986`), rather than drawing ON segments. Style 12 (LF13) is a single full-panel RGB cell (`ledPositionLF13 = {0,0,460,460}`).
8. **This file emits no bytes.** `isOn[]`+`ledColor[]`+`ledPosition[]` ARE the segment buffer; `OnPaint` is preview only. The USB pack/send for segment panels is in `FormLED` (not in this audit). Do **not** assume `FormKVMALED6`'s packet format (§6) applies to segment panels — it is a different ARGB-channel device.
9. **Zone/unit markers are exclusive toggles** (`SSD`/`MTNo`/`GNo`, `BFB`/`MHz`) set inside the metric overloads; forgetting to clear the others leaves two unit labels lit.
10. **Decorative segments** `ZhuangShi1..32` (`:322-384`) and clock separators `Shijian1/2`, `Riqi` (`:386-390`) are real entries in the arrays and gate some paint branches (e.g. style 7 uses `isOn[ZhuangShi21]`, `:2949`).
