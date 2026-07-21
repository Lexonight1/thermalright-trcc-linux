# AUDIT — LED Device Core (`TRCC.LED/FormLED.cs`, TRCC 2.1.6)

Source: `/home/ignorant/Downloads/TRCCCAPEN/TRCC_decompiled/TRCC.LED/FormLED.cs` (16,751 lines).
Every claim below is line-cited against that file. Quotes are verbatim. No speculation —
where behaviour lives in a *different* file (`UCScreenLED`, `Form1`, `UCSystemInfo`) it is
flagged as EXTERNAL and not asserted.

---

## 1. Purpose & Lifecycle

`FormLED : Form` is the WinForms window that drives **one** LED cooler: it owns the RGB/effect
state, the per-tick effect engine, the preview control (`UCScreenLED ucScreenLED1`), and the
USB send path. It does **not** own the USB handle or the master timer — it emits frames through
a delegate to the host (`Form1`), which owns the tick and the device I/O.

### Delegate seam (host owns the wire)
```
15:	public delegate void delegateFormLED(int cmd, object info = null, object data = null, object data1 = null);
47:	public delegateFormLED delegateForm;
```
Every frame is pushed with `cmd=16`, `info=myDeviceCount`, `data=<byte[]>`, `data1=<length>`:
```
4361:			delegateForm?.Invoke(16, myDeviceCount, array, array.Length);
```
`myDeviceCount` (`43: public int myDeviceCount = 0;`) is the device index the host uses to route
the frame to the correct USB endpoint. FormLED never calls libusb/HID directly.

### Onboarding — `FormLEDInit(int NO, int mode, int count, string name)` (1598)
Called by the host once per attached LED cooler. `NO` is the **product id** (the PM/handshake
byte); `count` is the device index (`1600: myDeviceCount = count;`); `name` is the per-device
settings filename suffix. The method:
1. Sets `nowNo = NO` (1601) and localizes labels (`1602: FormLEDLanguageSet();`).
2. Selects the **product skin + `nowLedStyle`** from `NO` (1603–1802 — the product table, §2).
3. Loads persisted user state from `Data\Digital\Setting<name>` (1804–1902).
4. Enables the save-on-change guard: `1911: isSaveTimer = true;`.

There is **no handshake performed here** — see §3; the handshake byte (`NO`) arrives already
parsed by the host.

### Per-tick engine — `MyTimer_Event()` (4069)
The host's timer calls this. One tick = read sensors → compute effect → paint preview → send:
```
4071:		GetSystemInfo();
4072:		GetVal();
...      (per-nowLedStyle: DSCL/DSHX/QCJB/CHMS/WDLD/FZLD timer)
4304:		LedValToScreenLed();
4305:		SendHidVal();
4306:		((Control)this).Invalidate();
```

---

## 2. Device / Product table — `NO → skin + nowLedStyle` (FormLEDInit 1603–1802)

`nowLedStyle` (`883: private byte nowLedStyle = 1;`) is the **internal product family** that keys
every effect timer, the preview `ReSetUCScreenLEDn()`, and the send framing. Mapping is verbatim:

| `NO` (product id / PM) | Resource skin | `ReSet…` call | `nowLedStyle` | Notes |
|---|---|---|---|---|
| `1` (1603) | `DFROZEN_HORIZON_PRO` | — | **1** (default, unchanged) | base 10-LED |
| `2` (1608) | `DFROZEN_MAGIC_PRO` | — | **1** | base 10-LED |
| `3` (1613) | `DAX120_DIGITAL` | — | **1** | base 10-LED |
| `16..31` (1618, `NO>=16 && NO<32`) | `DPA120_DIGITAL` | `ReSetUCScreenLED2()` | **2** | `textBoxTimer` hidden |
| `32` (1626) | `DAK120_DIGITAL` | `ReSetUCScreenLED3()` | **3** | |
| `48 || 49` (1641) | `DLF8` | `ReSetUCScreenLED5()` | **5** | |
| `80` (1660) | `DLF12` | `ReSetUCScreenLED6()` | **6** | `ucScreenLED1.myLedMode` driven |
| `96` (1674) | `DLF10` | `ReSetUCScreenLED7()` | **7** | rotating ring, buttonN1-3 |
| `112` (1691) | `DLC2` | `ReSetUCScreenLED9()` | **9** | clock/date panel; H24/H12 + week |
| `128` (1707) | `DLC1` | `ReSetUCScreenLED4()` | **4** | memory panel (`ucledMemoryInfo1`) |
| `129` (1731) | `DLF11` | `ReSetUCScreenLED10()` | **10**, `nowLedStyleSub=1` | harddisk panel |
| `144` (1758) | `DLF15` | `ReSetUCScreenLEDLF15()` | **11** | |
| `160` (1772) | `DLF13` | `ReSetUCScreenLEDLF13()` | **12** | |
| `208` (1784) | `DCZ1` | `ReSetUCScreenLED8()` | **8** | |

`nowLedStyleSub` is set to `1` only for `NO==129` (`1732: nowLedStyleSub = 1;`); it switches the
memory-style logic to hard-disk sources in `GetVal`/`WDLD_Timer4`/`FZLD_Timer4`.

### Effect enum (`myLedMode`, 1–6)
Set by the six mode buttons; verbatim from `ButtonDengGuang_Click` (2387) + the click handlers
(2456–2497):

| `myLedMode` | Button | Chinese name | Effect |
|---|---|---|---|
| 1 | `buttonDSCL_Click` (2456) | 单色常亮 DSCL | **Static** (constant color) |
| 2 | `buttonDSHX_Click` (2463) | 单色呼吸 DSHX | **Breathe** |
| 3 | `buttonQCJB_Click` (2470) | 七彩渐变 QCJB | **Color-cycle / gradient** (6-phase RGB wheel) |
| 4 | `buttonCHMS_Click` (2478) | 彩虹幕色 CHMS | **Rainbow** (768-entry table, spatial) |
| 5 | `buttonWDLD_Click` (2485) | 温度联动 WDLD | **Temperature-linked** color bands |
| 6 | `buttonFZLD_Click` (2492) | 负载联动 FZLD | **Load-linked** color bands |

Temp unit (`myTempMode`, `buttonCF_Click` 2357): `1`=Celsius (`buttonC_Click` 2373), `2`=Fahrenheit
(`buttonF_Click` 2380).

### Per-style LED counts (the effect buffers, 851–879)
```
851:	private byte[,] ledVal = new byte[10, 3];            // style 1 (base)
861:	private byte[,] ledValCaihong = new byte[18, 3];     // style 2 rainbow ring
863:	private byte[,] ledVal4 = new byte[14, 3];           // style 4 (LC1)
865:	private byte[,] ledVal5 = new byte[23, 3];           // style 5 (LF8)
867:	private byte[,] ledVal6 = new byte[72, 3];           // style 6 (LF12)
869:	private byte[,] ledValCaihong7 = new byte[12, 3];    // style 7 rainbow ring
871:	private byte[,] ledVal8 = new byte[13, 3];           // style 8 (CZ1)
873:	private byte[,] ledVal9 = new byte[31, 3];           // style 9 (LC2)
875:	private byte[,] ledVal10 = new byte[17, 3];          // style 10 (LF11)
877:	private byte[,] ledValLF15 = new byte[72, 3];        // style 11 (LF15)
879:	private byte[,] ledValLF13 = new byte[62, 3];        // style 12 (LF13)
```
These loop bounds are confirmed by every `DSCL_Timer*` (e.g. `7621: for (int i = 0; i < 10; i++)`,
`7631:… < 14`, `7641:… < 23`, `7651:… < 72`, `7661:… < 13`, `7671:… < 31`, `7681:… < 17`,
`7691:… < 72`, `7701:… < 62`).

---

## 3. Handshake / wire protocol

### No handshake in FormLED
FormLED receives the already-decoded product id as `NO` in `FormLEDInit`; it performs **no
handshake bytes** itself. The device-identify/handshake lives in the host (`Form1`, EXTERNAL).

### Packet framing — 20-byte header + RGB payload
Every send branch prepends the **identical 20-byte header** then a payload of `N*3` RGB bytes,
concatenates, and pushes via the delegate. Verbatim (test branch, 4326):
```
4326:			byte[] obj = new byte[20]
4327:			{
4328:				218, 219, 220, 221, 0, 0, 0, 0, 0, 0,
4329:				0, 0, 2, 0, 0, 0, 0, 0, 0, 0
4330:			};
4331:			obj[16] = (byte)array.Length;
```
- Header bytes `0..3` = **`0xDA 0xDB 0xDC 0xDD`** (218,219,220,221) — the magic preamble.
  (`49: private const byte USB_PACKED_Head = 220;` names the 0xDC byte specifically.)
- Header byte `12` = **`2`** (constant command/type).
- Header byte `16` = **payload length** = `array.Length` (the RGB byte count, NOT incl. header).
- All other header bytes = `0`.
- Frame on the wire = `header(20) ++ payload(N*3)` via `.Concat(...).ToArray()` (e.g.
  `4360: array = first.Concat(array).ToArray();`).

### Per-product payload sizes (SendHidVal branches, one `if (nowLedStyle==…)` each)
| `nowLedStyle` | Payload bytes | LEDs (bytes/3) | Branch head |
|---|---|---|---|
| test (`checkBox1.Checked`) | 252 | 84 | 4325 |
| 2 | 252 | 84 | 4364 |
| 3 | 192 | 64 | 4647 |
| 4 | 93 | 31 | (grep) |
| 5 | 279 | 93 | |
| 6 | 423 | 141 | |
| 7 | 438 | 146 | |
| 8 | 198 (+ secondary `byte[48]`) | 66 | |
| 9 | 189 | 63 | |
| 10 | 114 | 38 | |
| 11 | 411 | 137 | |
| 12 | 186 | 62 | 7586 (via array23 loop `< 62`, 7574) |
| default (style 1) | 90 | 30 | 7586 |

(Sizes taken verbatim from the `new byte[N]` allocations and the `for (int … < N/3)` loops in
SendHidVal, e.g. `4649: byte[] array4 = new byte[192];` / `4657: for (int k = 0; k < 64; k++)`;
`7586: byte[] array24 = new byte[90];` / `7594: for (int num18 = 0; num18 < 30; num18++)`.)

### Per-product byte REORDER (physical wiring map)
The buffer is filled in **logical** LED order (`ucScreenLED1.ledColor[j,*]`) then copied into a
**second** array in **physical wire order** via a long explicit index list. Example (style 2,
4389–…): `array3[num2++] = array2[ucScreenLED1.Cpu2*3];` then `Cpu1`, `LEDF1`, `LEDA1`, `LEDB1`,
`LEDG1`, `LEDE1`, `LEDD1`, `LEDC1`, `LEDF2`… (4391–4438+). Style 3 has its own order starting
`WATT, LEDC3, LEDD3, LEDE3, LEDG3, LEDB3, LEDA3, LEDF3, LEDB2, Cpu1, …` (4680–…). **Each product's
reorder table is unique and must be transcribed per style** — the symbol constants (`Cpu1`,
`LEDA1`, `WATT`, `BFB`, `SSD`, `HSD`, …) are EXTERNAL fields on `UCScreenLED`.

### Brightness / gamma in the send path
Two multipliers stack:
1. **Global fixed scale** `num = 0.4f` applied to every LED at send (`4312: float num = 0.4f;`),
   e.g. `4378: array2[j*3] = (byte)((float)(int)ucScreenLED1.ledColor[j,0] * num);`.
   `b` is an additive floor, always `0` here (`4311: byte b = 0;` — never reassigned).
2. **User brightness** `myOnOff * myBrightness * 0.01f` applied *earlier* in `LedValToScreenLed`
   when converting `ledVal→ledColor` (`10275: float num = (float)(myOnOff1 * myBrightness1) *
   0.01f;`), and again inline for the raw-buffer styles (11/12), e.g.
   `7281: float num16 = (float)(myOnOff * myBrightness) * 0.01f;` then `* num` (=0.4) —
   `7282: … ledValLF15[41,0] * num16 * num`. Style 12 folds it directly:
   `7577: array23[num17*3] = (byte)((float)(ledValLF13[num17,0] * myBrightness) * 0.01f * num + (float)(int)b);`.
- `myOnOff==0` short-circuits the payload to all-zero per LED (`4659`, `7596`).

`myBrightness` is 0–100 (`2221: myBrightness = (byte)(val * 100 / 255);`); default `65`
(`25: private byte myBrightness = 65;`).

---

## 4. Effects — exact algorithms + timing constants

Timing constants (top of file):
```
71:	private const int RGB_BREATHING_TIMER = 33;
73:	private const int RGB_COLORFUL_TIMER = 28;
77:	private const int RGBTableCount = 768;
```

### 4.1 Static — `DSCL_Timer*` (7619+)
Fills every LED of the style's buffer with the raw user color; no time term:
```
7623:			ledVal[i, 0] = (byte)rgbR1;
7624:			ledVal[i, 1] = (byte)rgbG1;
7625:			ledVal[i, 2] = (byte)rgbB1;
```
One variant per style (`DSCL_Timer4/5/6/8/9/10/TimerLF15/TimerLF13`), differing only in loop
bound (§2 counts).

### 4.2 Breathe — `DSHX_Timer*` (7709+)
A 0→33→66 sawtooth on `rgbTimer` (period 66 ticks), ramping the user color up then down, then
**mixed 80/20 with the static color** so it never goes fully dark:
```
7711:		rgbTimer++;
7715:		if (rgbTimer < 33) {   b = (byte)(rgbR1 * rgbTimer / 33); …ramp up }
7721:		else if (rgbTimer < 66) { b = (byte)(rgbR1 * (66 - rgbTimer) / 33); …ramp down }
7727:		else { rgbTimer = 0; b = b2 = b3 = 0; }
7734:			ledVal[i, 0] = (byte)((double)(int)b * 0.8 + (double)rgbR1 * 0.2);
```
**Ring variant** `DSHX_Timer_New` (7988) drives a single scalar `ledHuxi` (used by the rainbow
rings, styles 2/7) with a **floor of 51** and 0.8 gain:
```
7993:			ledHuxi = (byte)(51.0 + (double)(255 * rgbTimer1 / 33) * 0.8);   // up
7998:			ledHuxi = (byte)(51.0 + (double)(255 * (66 - rgbTimer1) / 33) * 0.8); // down
8002:		ledHuxi = 51;   // trough
```
(`853: private byte ledHuxi = byte.MaxValue;`.) Uses a **separate** counter `rgbTimer1`.

### 4.3 Color-cycle / gradient — `QCJB_Timer*` (8005+)
6-phase RGB wheel, `RGB_COLORFUL_TIMER = 28` ticks per phase, driven by `nowJianbian` (phase 0–5)
+ `nowJianbianTimer` (step). Full phase table (8010–8117):
- phase 0: R=255, G=0, B ramps 255→0 (`8016: b3 = (byte)((28 - nowJianbianTimer)*255/28)`) → red
- phase 1: R=255, G ramps 0→255, B=0 → yellow
- phase 2: R ramps 255→0, G=255, B=0 → green
- phase 3: R=0, G=255, B ramps 0→255 → cyan
- phase 4: R=0, G ramps 255→0, B=255 → blue
- phase 5: R ramps 0→255, G=0, B=255 → magenta → wraps to `nowJianbian=0`
All LEDs get the same color (`8118: for i<10 … ledVal[i,*] = b/b2/b3`) — spatially uniform.
**Ring variant** `QCJB_Timer_New` (9094) writes the single triplet `ledQicai[0..2]` (855) with the
identical phase machine.

### 4.4 Rainbow — `CHMS_Timer*` (9212+)
Spatial rainbow from the precomputed **768-entry `RGBTable`** (`79: byte[768,3]`, a full
R→Y→G→C→B→M→R wheel in steps of 2). Scrolls by `rgbTimer += 4` per tick, reversed LED order,
per-LED phase offset:
```
9214:		if (rgbTimer >= 768) rgbTimer = 0;
9220:			ledVal[10 - i - 1, 0] = RGBTable[(rgbTimer + i * 768 / 10 / 6) % 768, 0];
9224:		rgbTimer += 4;
```
The spatial spread divisor differs per product — **caveat, not uniform**:
- style1 `i*768/10/6` (9220), style4 `i*768/14/6` (9235), style5 `i*768/23/6` (9250),
  style8 `i*768/13/2` (9280), style10 `i*768/17/6` (9310)
- but style6 `i*768/72` (9265), style9 `i*768/31` (9295), LF15 `i*768/72` (9325),
  LF13 `i*768/62` (9340) — **no `/6` or `/2`** (full wheel across the strip).
**Ring variants:** `CHMS_Timer_New` (9347) → `ledValCaihong[18]` with `i*768/18/6`, uses a
**third** counter `rgbTimer2`; `CHMS_Timer_New_7` (9362) → `ledValCaihong7[12]` with `i*768/12/6`.

### 4.5 Temperature-linked — `WDLD_Timer*` (9377+)
5 fixed color bands keyed on `ucInfoImage1.myVal` (the CPU-temp reading):
```
9382:		if (myVal < 30)      { 0,255,255 }   // cyan
9388:		else if (< 50)       { 0,255,0 }     // green
9394:		else if (< 70)       { 255,255,0 }   // yellow
9400:		else if (< 90)       { 255,110,0 }   // orange (G=110!)
9406:		else                 { 255,0,0 }     // red
```
`WDLD_Timer4` (9420) reads `MemTemperature` (sub=0) or `HardDiskInfo[hardDiskCount-1][1]` (sub=1).
Ring variant `WDLD_Timer_New` (9784) writes `ledWendu[0..2]` (857).

### 4.6 Load-linked — `FZLD_Timer*` (9824+)
**Identical band table** to WDLD (thresholds 30/50/70/90, same colors incl. the `255,110,0`
orange) but keyed on `ucInfoImage3.myVal` (CPU-usage), or `MemLoad` (`9872`) / `HardDiskInfo[…][2]`
in `FZLD_Timer4` (9867). Ring variant `FZLD_Timer_New` (10231) writes `ledFuzai[0..2]` (859).

---

## 5. Per-product / per-mode behaviour & segment-digit routing

### Dispatch (MyTimer_Event, 4073–4303)
- **Styles 2 & 7 (rainbow rings, PA120/LF10):** run **all** the `_New` effect writers every tick
  (`4075: DSHX_Timer_New(); 4076: QCJB_Timer_New(); 4079: CHMS_Timer_New(); …`), because the ring
  shows breathe + gradient + rainbow scalars simultaneously and `LedValToScreenLed` picks per
  `myLedMode1..4`.
- **Styles 4/5/6/8/9/10/11/12:** `switch(myLedMode)` → the matching `_Timer<style>()` (4088–4279).
- **Default (style 1):** `switch(myLedMode)` → base `_Timer()` (4282–4302).

### Segment-digit source routing — `GetVal()` (3368)
Selects which `ucInfoImageN.val1` feeds which digit group and sets the segment on/off flags
(`ucScreenLED1.isOn[...]`), then calls the EXTERNAL `ucScreenLED1.SetMyNumeral*(...)` which does
the actual 7-seg mask. Key branches:
- **style 2** (3394): both CPU (img1/img3) + GPU (img4/img6); `myTempMode` toggles SSD vs HSD icon
  (3402); `SetMyNumeral(img1, img3, img4, img6)` (3416).
- **style 3** (3418): WATT+BFB on; rotates CPU/GPU via `nowLunbo`; feeds `CpuPower`/`GpuPower`
  (EXTERNAL `Form1.formSystemInfo.ucSystemInfo1.*`) + img values (3457, 3472).
- **style 4** memory (3509): rotates Temp/Clock/Used; **C→F conversion inline**
  `3542: MemTemperature * 9 / 5 + 32`; clock `* memoryRatio` (3546); used `/ 1000f` GB (3549).
- **style 10** harddisk (3573): `HardDiskInfo[hardDiskCount-1]` fields [1..4] (3599–3646).
- **style 8** CZ1 (3761): 4-way rotate img1/img3/img4/img6 via `SetMyNumeralNew`.
- **style 9** clock (3868): `DateTime.Now`; 12/24h fold `3876: num %= 12; if(0) num=12`; week
  start Sun/Mon fold (3884); `SetMyTimer(month,day,hour,minute,dow)`.
- **style 7** (3741): `SetMyNumeralNew(img1, img4)`.
- **style 12** (3896): `return;` — no numerals (pure light strip).

### Rotation ("轮播" LunBo) timing
`isLunBo` cycles the enabled sources. Advance gate is **`ValCount >= 6 * <seconds>`** where
seconds comes from `textBoxTimer.Text`:
```
3425:				if (ValCount >= 6 * Convert.ToInt32(((Control)textBoxTimer).Text))
```
The `6 *` implies the master tick is **6 Hz (~166 ms)** — corroborated by `GetSystemInfo`
throttling sensor refresh to every 6th tick (`3044: if (InfoCount < 6) return;` → ~1 Hz sensors).
(The QTimer interval itself is EXTERNAL, owned by `Form1`.)

### Settings persistence
`FormLEDInit` reads and `SetMyNameFile()` (1941) writes `Data\Digital\Setting<name>`, a binary
blob guarded by leading byte `220` (0xDC): `1809: if (binaryReader.ReadByte() == 220)`. Field order
is fixed (1811–1894 read / 1945–1988 write): rgb, onOff, brightness, ledMode, tempMode, 5×LunBo
bools, then per-channel modes/colors (`myLedMode1..4`, `rgb*_1..4`, `myOnOff1..4`,
`myBrightness1..4`), timer text, `memoryRatio`, `hardDiskCount`, and (style 9 only)
`isTimer24`/`isWeekSun` (1985). `memoryRatio` maps 1/2/4 (case 4 → combo index 3, 1876).

---

## 6. Caveats — what a naive port will get wrong

1. **Two separate brightness stages multiply.** User `myOnOff*myBrightness*0.01` (in
   `LedValToScreenLed` / inline for styles 11–12) **and** a hard global `*0.4f` at send
   (`4312`). Miss either and brightness is 2.5× too bright or wrong. `b` (additive floor) is
   always 0 in the shipped path.
2. **Per-product physical reorder tables are unique and long.** The logical→wire index list
   differs for every `nowLedStyle` (style 2 order at 4391+, style 3 at 4680+, …). There is no
   formula — each is an explicit hand-wired permutation over EXTERNAL `UCScreenLED` symbol
   constants. These must be transcribed per product, not derived.
3. **Header byte[16] = payload length, byte[12] = 2, preamble = 0xDA DB DC DD.** Length excludes
   the 20-byte header. Same header for all products; only the payload size/order changes.
4. **Rainbow spatial divisor is NOT uniform.** Small strips use `…/N/6` (or `/13/2` for CZ1),
   large strips (LF12/LC2/LF15/LF13, styles 6/9/11/12) use `…/N` with no extra divisor — a
   different visual density. `rgbTimer += 4` per tick; wrap at 768; **LED index reversed**
   (`count-i-1`).
5. **Three independent effect counters.** `rgbTimer` (strip breathe + rainbow), `rgbTimer1`
   (`ledHuxi` ring breathe), `rgbTimer2` (`CHMS_*_New` ring rainbow). Sharing one counter
   desyncs the ring products (2/7) which run all effects at once.
6. **WDLD and FZLD share the exact band table** (30/50/70/90 → cyan/green/yellow/orange/red) but
   read different sources (temp = `ucInfoImage1.myVal`/MemTemperature; load =
   `ucInfoImage3.myVal`/MemLoad). Note the orange band is `(255,110,0)` — **G=110, not 128**.
7. **`nowLedStyle` ≠ `NO`.** The wire product id `NO` maps through the FormLEDInit table to a
   small internal `nowLedStyle` (1–12). `NO 1/2/3` all collapse to style 1; `NO 16..31` all →
   style 2; `NO==129` additionally sets `nowLedStyleSub=1`. Port the table, don't assume identity.
8. **Breathe never reaches black** on strips (80/20 mix with static color, 7734) and floors at
   **51** on rings (`ledHuxi`, 7993/8002). QCJB/CHMS *do* reach full saturation.
9. **`GetSystemInfo` throttles to 1 in 6 ticks** (3044); rotation is `6 * seconds` (3425). The
   `6` is load-bearing timing — the master tick is ~6 Hz. If the port's tick isn't 6 Hz, breathe
   period (66 ticks), rainbow speed (+4/tick), and rotation seconds all skew.
10. **Style 8 (CZ1) sends a second `byte[48]` buffer** in the same call (secondary segment/icon
    plane) — not a single flat payload like the others.
11. **Temp unit detection is string-sniffed** from the sensor label in `GetSystemInfoVal`
    (`3142: if (val.Contains("℃"))` … `℉`/`RPM`/`MHz`/`%`), which also flips `ucInfoImage1`
    text-mode and `myTempMode`. C→F is `*9/5+32` done in integer math in several places (3542,
    3609, 3121) — truncation matters for exact digit parity.
```
