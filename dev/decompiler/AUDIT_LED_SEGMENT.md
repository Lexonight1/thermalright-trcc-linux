# AUDIT — LED Segment-Display Compose (TRCC 2.1.6 decompile)

Source of truth (verbatim, line-cited):
- `~/Downloads/TRCCCAPEN/TRCC_decompiled/TRCC.DCUserControl/UCScreenLED.cs` (10,141 lines) — the 7-/13-segment **preview UserControl**.
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
```
UCScreenLED.cs:2144  public int[,]  ledPosition;   // [segIdx][x,y,w,h]
UCScreenLED.cs:2146  public bool[]  isOn;          // [segIdx] lit?
UCScreenLED.cs:2148  public byte[,] ledColor;      // [segIdx][R,G,B]
```
Per-product concrete arrays are declared as fields, e.g. `ledPosition1`/`isOn1`/`ledColor1` (`:464`, `:498`, `:505`), `ledPosition2` (84 rows, `:539`), … up to `ledColorLF13`/`ledPositionLF13` (`:19`/`:23` region). `ledPosition1` has 30 rows (`new int[30,4]` `:464`); `ledPosition2` has 84 rows (`new int[84,4]` `:539`).

### 1.2 Logical segment→index constants (remapped per product)
Segment identity is a set of named `int` fields that index into the active arrays. Standard 7-seg names are `LEDA1..LEDG1` (digit 1) through `LEDG13` (digit 13):
```
UCScreenLED.cs:140  public int LEDA1 = 9;
...:154            public int LEDG1 = 15;   // A..G = the 7 segments of digit 1
...:168            public int LEDG2 = 22;   // digit 2, and so on to LEDG13 (:320)
```
An **alternate 13-segment naming** exists — `LEDH1..LEDM1` … `LEDH6..LEDM6`:
```
UCScreenLED.cs:392  public int LEDH1 = 13;
...:402            public int LEDM1 = 18;   // H,I,J,K,L,M = 6 extra segments -> 13 total with A..G
```
Zone / label / decoration indices:
```
UCScreenLED.cs:108  Logo1=0  Logo2=1               // logos
...:112  MTNo=1  GNo=2                              // "MT" / "G" unit markers
...:116  WATT=1  MHz=5                              // "W" / "MHz" unit markers
...:120  Cpu1=2 Cpu2=3 Gpu1=4 Gpu2=5               // CPU/GPU zone labels
...:128  SSD=6 HSD=7 BFB=8                          // SSD / HDD / "%" (百分比) markers
...:322  ZhuangShi1=93 .. ZhuangShi32=124           // decorative/frame segments (:322-:384)
...:386  Shijian1=0 Shijian2=1 Riqi=2               // clock separators / date marker
```
**These are defaults; every `ReSet*` OVERWRITES them for that product** (see §2). A port must remap per style, never hardcode the default indices.

### 1.3 Construction
```
UCScreenLED.cs:2152  public UCScreenLED() {
  InitializeComponent();
  ledPosition = ledPosition1; isOn = isOn1; ledColor = ledColor1;   // default = style 1
  imageBk = (Image)(object)Resources.DFROZEN_HORIZON_PRO;
  SetMyNumeral(36);                                                  // seed value 36
}
```
`nowLedStyle` default `1` (`:10`); `myLedMode` default `4` (`:12`).

---

## 2. Per-product layout — the `ReSet*` methods (style selection)

Each `ReSetUCScreenLEDn()` (a) points `ledPosition/isOn/ledColor` at that product's arrays, (b) sets `nowLedStyle`, (c) **remaps every segment/zone index** to that product's array offsets, and (d) loads any artwork resources. `ReSetUCScreenLED1()` is empty (`:2162-2164`) — style 1 is the constructor default.

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
| ReSet10 | 10 | — | — | SSD=0 MTNo=BFB=1 GNo=MHz=2 | 2739-2744 |
| ReSetLF15 | 11 | ledPositionLF15 | — | Cpu1=0 Gpu1=1 SSD=2 WATT=4 MHz=5 | 2785-2790 |
| ReSetLF13 | 12 | ledPositionLF13 (1 cell 460×460) | `D0rgblf13` (imageLF13); bg `DLF13` | whole panel = one RGB cell | 2886-2892 / 10133 |

**Product NAMES (AX120/PA120/AK120/LC1/LF8/LF10/LF12/CZ1 …) are not in this file.** The caller (`FormLED`, out of scope) selects the style by calling the matching `ReSet*`. In-file evidence links style 8 → **CZ1** (`Resources.Dchcz1`, `:2652`), style 12 → **LF13** (`Resources.D0rgblf13`/`DLF13`), style 11 → **LF15**. Others are addressed by number only.

---

## 3. Digit → segment truth tables (the masks)

### 3.1 Standard 7-segment table (A=top, B=upper-right, C=lower-right, D=bottom, E=lower-left, F=upper-left, G=middle)
Canonical block: `SetMyNumeral(int val)` ones-digit switch, `UCScreenLED.cs:3257-3349` (`case n: isOn[LEDA3..LEDG3]=…`). Decoded (segment ON = `true`):

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

This exact table is repeated verbatim in every value overload (e.g. `:3049-3150`, `:5257-5557`, `:9399-9491`). No product deviates from it for `LEDA..LEDG`.

### 3.2 Leading-zero suppression (value displays)
- **Hundreds digit** (`num = val/100`): `case 0` = all segments OFF (blank), `:3051-3059`.
- **Tens digit** (`num2`): `case 0` = all OFF **iff `num==0`**, else full "0" glyph, `:3151-3174`:
```
case 0:
  if (num == 0) { isOn[LEDA2]=false; … all false; }   // 3154-3163  blank
  else          { isOn[LEDA2]=true; … LEDF2=true; LEDG2=false; } // 3165-3172  "0"
```
- **Ones digit** (`num3`): `case 0` = always the "0" glyph (`:3259`). Ones place never blanks.

### 3.3 13-segment table (A–M) — SetMyNumeralNew ONLY
`SetMyNumeralNew(int cpuTemp,int gpuTemp)` (`:8124`) uses a **completely different 13-segment glyph set** writing `isOn[LEDA1..LEDG1]` AND `isOn[LEDH1..LEDM1]`. Decoded from `:8129-8243`:

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

**This table is NOT the 7-seg table** — reusing §3.1 for a "New"/13-segment product renders wrong glyphs. Digits 7-9 continue in the same 13-line-per-case block through `:8300`ish.

### 3.4 Unit glyphs drawn ON a digit (letters, not just numbers)
`SetMyNumeral(int mode,int val)` with `mode<=0` draws a **temperature-unit letter on digit 4** (`LEDA4..LEDG4`), `:5234-5253`:
```
if (mode == 0)  // Celsius glyph "C"
  isOn[LEDA4]=true; LEDB4=false; LEDC4=false; LEDD4=true; LEDE4=true; LEDF4=true; LEDG4=false;
else            // Fahrenheit glyph "F"
  isOn[LEDA4]=true; LEDB4=false; LEDC4=false; LEDD4=false; LEDE4=true; LEDF4=true; LEDG4=true;
```

---

## 4. Metric → display mapping (the overloads)

Digit decomposition idiom: `num = v/100; num2 = v%100/10; num3 = v%10` (`:3046-3048`). 4-digit uses `/1000, /100%10, %100/10, %10` (`:5572-5575`); 5-digit uses `/10000 …` (`:6346-6350`).

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

**Zone/unit markers are mutually exclusive toggles** set inside these overloads: `isOn[SSD]` / `isOn[MTNo]` / `isOn[GNo]` (`:5231-5233`, `:5562-5570`), `isOn[BFB]` / `isOn[MHz]` (`:6336-6344`). They light which unit LABEL segment shows next to the number.

---

## 5. Compose / color / zone rendering (`OnPaint`, `:2895-3042`)

The buffer is rendered to the control's `Graphics` (this is the PREVIEW; the device wire buffer is `isOn[]`+`ledColor[]`):

- **`myLedMode == 4` = artwork-image mode**; any other value = **solid-color mode** using `ledColor`.
- **Generic path** (all styles except 8 & 12), `:3029-3040`:
```
for (k in ledPosition) if (isOn[k]) {
  Brush = SolidBrush(FromArgb(ledColor[k,0], ledColor[k,1], ledColor[k,2]));
  graphics.FillRectangle(Brush, ledPosition[k,0..3]);   // per-segment rect, per-segment RGB
}
graphics.DrawImage(imageBk, 0, 0);                       // artwork/mask overlaid LAST (:3041)
```
- **Style 6** (`:2911-2940`): mode 4 draws `imageBk61/62/63` at fixed offsets `(26,17)/(23,221)/(293,274)`; else fills those rects with `ledColor[0]`.
- **Style 7** (`:2941-2972`): gated on `isOn[ZhuangShi21]`; draws `imageBk71` or fills rects `(30,217,…,70)` and `(195,268,70,170)` with `ledColor[ZhuangShi21]`.
- **Style 8 (CZ1)** (`:2973-3006`): mode 4 draws `imageBk81` then **blacks out** every OFF segment (`Color.Black` fill, `:2984-2986`); else fills only ON segments — i.e. CZ1 is an "artwork minus dark segments" panel.
- **Style 12 (LF13)** (`:3007-3028`): single cell `isOn[0]`; draws `imageLF13` or fills the whole `ledPosition[0]` rect with `ledColor[0]`.

Per-segment color: `ledColor[i]` is an independent `[R,G,B]` per segment index — every segment can be a different color (default arrays seed digit segments red `{255,0,0}` and logos white `{255,255,255}`, `:505-537`).

---

## 6. Wire format — `FormKVMALED6` (separate ARGB device, NOT segment)

Packet constants (`:22-42`):
```
USB_PACKED_Head=220  USB_PACKED_Head1=221  USB_PACKED_ONOFF=0  USB_PACKED_STATE=1
USB_PACKED_LEDMS=104  USB_PACKED_FAN=6  USB_PACKED_LED=16  USB_PACKED_GET_STATE=2
USB_PACKED_AUDIO=4  USB_LED_COUNT=10  USB_LED_RGB=3
```
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
3. **Leading-zero suppression is display-type-dependent.** Value displays blank leading zeros (hundreds always, tens iff hundreds==0; §3.2). `SetMyTimer` does **not** — hour tens `case 0` is a full "0" glyph (`:9401`), so clocks show `08:05`.
4. **Unit letters are drawn as segment glyphs on a digit**, not fixed labels: `mode==0`→"C" (A,D,E,F), else "F" (A,E,F,G) on `LED4` (`:5236-5252`).
5. **Digit count varies by overload** (2/3/4/5 digits) → the decomposition divisor changes (`/100`, `/1000`, `/10000`; §4). `SetMyNumeralHardDisk` is 5-digit (`:6346`).
6. **`myLedMode==4` is artwork mode, not a color.** In mode 4 `ledColor` is ignored and bitmaps are drawn (`:2913`, `:2943`, `:2975`, `:3009`); every other value fills solid rects from `ledColor`.
7. **Style 8 (CZ1) inverts the compose**: it draws the full artwork then **blacks out OFF segments** (`Color.Black`, `:2984`), rather than drawing ON segments. Style 12 (LF13) is a single full-panel RGB cell (`ledPositionLF13 = {0,0,460,460}`).
8. **This file emits no bytes.** `isOn[]`+`ledColor[]`+`ledPosition[]` ARE the segment buffer; `OnPaint` is preview only. The USB pack/send for segment panels is in `FormLED` (not in this audit). Do **not** assume `FormKVMALED6`'s packet format (§6) applies to segment panels — it is a different ARGB-channel device.
9. **Zone/unit markers are exclusive toggles** (`SSD`/`MTNo`/`GNo`, `BFB`/`MHz`) set inside the metric overloads; forgetting to clear the others leaves two unit labels lit.
10. **Decorative segments** `ZhuangShi1..32` (`:322-384`) and clock separators `Shijian1/2`, `Riqi` (`:386-390`) are real entries in the arrays and gate some paint branches (e.g. style 7 uses `isOn[ZhuangShi21]`, `:2947`).
