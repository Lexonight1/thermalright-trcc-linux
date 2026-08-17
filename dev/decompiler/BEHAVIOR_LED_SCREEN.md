# BEHAVIOR — LED screen controls, per-method

<!-- audit-state: origin=2.0.3.0 addresses=2.1.6.0 known-bad=none -->
> **Audited against TRCC 2.0.3; citations re-anchored to TRCC 2.1.6.**
> 1 method(s) documented here changed in TRCC 2.1.6 and have NOT been re-read: `CheckDirectoryExist` — read those entries as TRCC 2.0.3 history.
> [`AUDIT_INDEX.md`](AUDIT_INDEX.md#provenance)
<!-- /audit-state -->

Exhaustive per-method behavioral annotation. Builds on `AUDIT_LED_SEGMENT.md`
(segment/13-seg truth tables, `ReSet*` zone-remap table, wire format) — that doc
owns the *data tables*; this one owns *what every method does*.

Sources (verbatim, line-cited):
- `~/Downloads/TRCCCAPEN/TRCC_decompiled/TRCC.DCUserControl/UCScreenLED.cs` (10,143 lines) — **25 methods**. Segment/matrix METRIC preview UserControl. No USB send (preview-only; wire lives in `FormLED`, out of scope).
- `~/Downloads/TRCCCAPEN/TRCC_decompiled/TRCC.KVMALED6/FormKVMALED6.cs` (2,084 lines) — **71 methods**. Separate ARGB 10-channel lighting controller, persisted to `proMode.dc`. NOT a segment display.

Coverage: **UCScreenLED 25/25**, **FormKVMALED6 71/71**.

---

## FILE 1 — UCScreenLED.cs (25 methods)

### Lifecycle / style selection

- `UCScreenLED()` (UCScreenLED.cs:2154) — ctor; `InitializeComponent()`, then points the three active buffers at style-1 arrays (`ledPosition=ledPosition1; isOn=isOn1; ledColor=ledColor1`), sets `imageBk=Resources.DFROZEN_HORIZON_PRO`, seeds display with `SetMyNumeral(36)`. Style 1 is the constructor default; no `nowLedStyle` write (stays field default `1`). No branches.

- `ReSetUCScreenLED1()` (UCScreenLED.cs:2164) — **empty body**. Style 1 == the ctor default, so nothing to re-point. **[COPY-PASTE]** (part of the 12-method `ReSet*` family).

- `ReSetUCScreenLED2()` (UCScreenLED.cs:2168) — style 2: repoints buffers to `ledPosition2/isOn2/ledColor2`, `nowLedStyle=2`, remaps zone+segment indices (Cpu1=0…BFB1=9, LEDA1=10..LEDG3=30). 3 digit-rows, dual SSD/HSD/BFB markers. No artwork. **[COPY-PASTE]**.

- `ReSetUCScreenLED3()` (UCScreenLED.cs:2207) — style 3: `ledPosition3` etc., `nowLedStyle=3`; zone order Cpu1=0 WATT=1 SSD=2 HSD=3 BFB=4 Gpu1=5, then 8 digit-rows LEDA1=6..LEDC9=63. No artwork. **[COPY-PASTE]**.

- `ReSetUCScreenLED4()` (UCScreenLED.cs:2279) — style 4: `nowLedStyle=4`; zone SSD=0 MTNo=1 GNo=2 (MT/G unit markers), 4 digit-rows LEDA1=3..LEDG4=30. **[COPY-PASTE]**.

- `ReSetUCScreenLED5()` (UCScreenLED.cs:2318) — style 5: `nowLedStyle=5`; full unit set Cpu1=0 Gpu1=1 SSD=2 HSD=3 WATT=4 MHz=5 BFB=6, 12 digit-rows +LEDB13/LEDC13 (LEDA1=7..LEDC13=92). Largest 7-seg layout. **[COPY-PASTE]**.

- `ReSetUCScreenLED6()` (UCScreenLED.cs:2419) — style 6: `nowLedStyle=6`; **loads 3 artwork images** `imageBk61=Dch2, imageBk62=Dch3, imageBk63=Dch4`; same zone/segment map as style 5 (Cpu1=0..LEDC13=92). Paint overlays the 3 images at fixed offsets. **[COPY-PASTE]** + artwork.

- `ReSetUCScreenLED7()` (UCScreenLED.cs:2523) — style 7: `nowLedStyle=7`; loads `imageBk71=Dch1`; **13-segment product** — remaps LEDA1..LEDM1 (H–M extra segments) across 6 digit-rows, plus 32 `ZhuangShi*` decoration segments (84..115). Paint gates the artwork on `isOn[ZhuangShi21]`. **[COPY-PASTE]**.

- `ReSetUCScreenLED8()` (UCScreenLED.cs:2648) — style 8 = **CZ1**: `nowLedStyle=8`, loads `imageBk81=Dchcz1`; minimal map Cpu1=0 Gpu1=1 Cpu2=2 Gpu2=3, only 2 digit-rows (LEDA1=4..LEDG2=17). Paint = "artwork minus dark segments". **[COPY-PASTE]**.

- `ReSetUCScreenLED9()` (UCScreenLED.cs:2675) — style 9: `nowLedStyle=9`; no zone units, 7 digit-rows (LEDA1=3..LEDC8=53) + 7 `ZhuangShi` (54..60). No artwork. **[COPY-PASTE]**.

- `ReSetUCScreenLED10()` (UCScreenLED.cs:2741) — style 10: `nowLedStyle=10`; **aliased unit markers** `MTNo=(BFB=1)` and `GNo=(MHz=2)` (same index drives two logical names), SSD=0, 5 digit-rows LEDA1=3..LEDG5=37. **[COPY-PASTE]**.

- `ReSetUCScreenLEDLF15()` (UCScreenLED.cs:2787) — style 11 = **LF15**: buffers→`ledPositionLF15` etc., `nowLedStyle=11`; unit set Cpu1=0..BFB=6, 12 digit-rows +LEDB13/C13 (identical layout to style 5). No artwork. **[COPY-PASTE]**.

- `ReSetUCScreenLEDLF13()` (UCScreenLED.cs:2888) — style 12 = **LF13**: buffers→`ledPositionLF13` (single 460×460 cell), loads `imageLF13=D0rgblf13`, `nowLedStyle=12`. **No index remap** — whole panel is one RGB cell (`isOn[0]`). Shortest ReSet. **[COPY-PASTE]** (degenerate).

### Rendering

- `OnPaint(PaintEventArgs pe)` (UCScreenLED.cs:2897) — the compose/preview. 16 branches. Calls base, gets `Graphics`, then style-specific pre-passes:
  - `nowLedStyle==6` (:2913): `myLedMode==4`→draw imageBk61/62/63 at (26,17)/(23,221)/(293,274); else fill those rects with `ledColor[0]`.
  - `==7` (:2943): gated on `isOn[ZhuangShi21]`; mode4→draw imageBk71 at (30,217); else fill rects (30,217,w,70)+(195,268,70,170) with `ledColor[ZhuangShi21]`.
  - `==8` CZ1 (:2975): mode4→draw imageBk81 at (0,0) then **black-fill every OFF segment**; else fill only ON segments (artwork-minus-dark).
  - `==12` LF13 (:3009): single cell `isOn[0]`; mode4→draw imageLF13; else fill whole cell with `ledColor[0]`.
  - **Generic tail** (:3031, all styles except 8 & 12): loop every `ledPosition` row, if `isOn[k]` fill its rect with per-segment `ledColor[k]` `[R,G,B]`.
  - Always ends `graphics.DrawImage(imageBk,0,0)` (:3043) — background/mask artwork overlaid LAST. `myLedMode==4`=artwork mode, else solid-color mode.

### Metric → segment writers (the SetMyNumeral family) **[GOD] / [COPY-PASTE]**

All share the digit-decompose idiom (`num=v/100; num2=v%100/10; num3=v%10`) and the identical per-digit 7-seg switch (table in AUDIT_LED_SEGMENT §3.1), copy-pasted per metric block. Leading-zero suppression: hundreds `case 0`=blank; tens `case 0`=blank iff hundreds==0 else glyph "0"; ones never blanks.

- `SetMyNumeral(int val)` (UCScreenLED.cs:3046) — 124 br. Generic 3-digit value → digit-rows 1/2/3 (LED1=hundreds, LED2=tens, LED3=ones), leading-zero suppressed. The ctor's seed path. **[GOD]** (single-metric, one full 3-digit switch triple).

- `SetMyNumeral(int cpuTemp,int cpuUse,int gpuTemp,int gpuUse)` (UCScreenLED.cs:3354) — 4 metrics, each its own copy-pasted 3-digit switch block: cpuTemp→3354, cpuUse→3658, gpuTemp→3875, gpuUse→4179. **[GOD]/[COPY-PASTE]** — four repetitions of the same switch triple.

- `SetMyNumeral(int watt,int temp,int use)` (UCScreenLED.cs:4400) — 3 metrics: watt→4400, temp→4704, use→5008. Same repeated switch pattern. **[GOD]/[COPY-PASTE]**.

- `SetMyNumeral(int mode,int val)` (UCScreenLED.cs:5229) — unit-switched value. `mode<=0`(:5229): light `isOn[SSD]`, clear MTNo/GNo, draw temp-unit LETTER on digit-4 (`mode==0`→"C" glyph, else "F" glyph, LEDA4..LEDG4), then 3-digit value. `mode==1`→`isOn[MTNo]` marker, 4-digit value (`/1000`). `else`→`isOn[GNo]` marker, 4-digit value. Branches: mode→unit marker + glyph. **[GOD]** (unit-marker mutual-exclusion + C/F letter glyph).

- `SetMyNumeralHardDisk(int mode,int val)` (UCScreenLED.cs:5996) — disk metric, sibling of the above but writes digit-row 5 and uses `BFB`/`MHz` markers. `mode<=0`→SSD marker + C/F glyph on LEDA5..; else BFB/MHz marker; **5-digit** value (`/10000`). **[GOD]/[COPY-PASTE]** (near-duplicate of the mode/val overload).

- `SetMyNumeral(int temp,int watt,int mhz,byte use)` (UCScreenLED.cs:6877) — 145 br (largest). 4 metrics incl. MHz; note `num=temp/100%10` (masks hundreds to 1 digit). Four copy-pasted switch blocks + a `byte use` metric. **[GOD]/[COPY-PASTE]** (widest fan-out).

- `SetMyNumeralNew(int cpuTemp,int gpuTemp)` (UCScreenLED.cs:8126) — **13-segment** product (styles 7/9). Uses the COMPLETELY DIFFERENT A–M glyph set (AUDIT_LED_SEGMENT §3.3, NOT the 7-seg table). 2 metrics: cpuTemp digit1 (A..M), gpuTemp. Reusing the 7-seg table here renders wrong glyphs. **[GOD]** (distinct glyph table).

- `SetMyNumeralNew(int val)` (UCScreenLED.cs:9182) — 24 br. **2-digit** 7-seg: switches on `num2`(tens→LED1) then `num3`(ones→LED2); `num2 case 0` blanks iff `num==0`. Compact variant. **[COPY-PASTE]** (subset of the 3-digit switch).

- `SetMyTimer(int M,int d,int h,int m,int w)` (UCScreenLED.cs:9397) — clock/date writer. `h`→digit-rows 1/2 (`num=h%100/10`,`num2=h%10`; note hour "0" digit lights as full glyph, not blanked — clock keeps leading zero), `m`→rows 3/4, plus month `M`, day `d`, weekday `w` marker segments. Same 7-seg switch family. **[GOD]/[COPY-PASTE]** (clock-specific but reuses the digit switch).

### Framework

- `Dispose(bool disposing)` (UCScreenLED.cs:10120) — standard WinForms dispose; `if (disposing && components!=null) components.Dispose()`, then base. 1 branch.

- `InitializeComponent()` (UCScreenLED.cs:10129) — designer boilerplate; sets `imageBk` for LF13 background (`Resources.DLF13` region) + control props. No branches.

---

## FILE 2 — FormKVMALED6.cs (71 methods)

ARGB 10-channel lighting controller. State: `myChannel[10]` (which channels selected), `myOnOff[10]`, `myModeS[10]`, `myBrightnessS[10]`, `myRGB[10,3]`, plus current-edit scalars `myBrightness/mySpeed/myMode/rgbR1/rgbG1/rgbB1`. Persisted to `Data\KVMALED6\proMode.dc` (magic byte 220). All sends funnel through `SendDeviceData`→`delegateForm.Invoke(16,...)`.

### Setup / lifecycle

- `ClearButtonBouns()` (FormKVMALED6.cs:198) — clears the FlatAppearance border color (transparent) on all ~35 buttons. Pure UI. No branches.
- `InitControl()` (FormKVMALED6.cs:238) — seeds the 4 scroll controls (R/G/B/white sliders: G=0,B=0,white=255) and wires the UCColor/UCScroll delegate callbacks to this form's handlers. No branches.
- `FormKVMALED6()` (FormKVMALED6.cs:251) — ctor: InitializeComponent → ClearButtonBouns → CheckDirectoryExist → InitData → InitControl.
- `CheckDirectoryExist()` (FormKVMALED6.cs:568) — builds `KVMALED6_All_ML = StartupPath\Data\KVMALED6\`, creates it if missing. 1 branch (exists?).

### Window chrome / power

- `buttonPower_Click` (FormKVMALED6.cs:260) — `delegateForm.Invoke(255)` (host handles power/close). UI event.
- `buttonPower_MouseEnter` (:265) / `buttonPower_MouseLeave` (:270) — swap power-button background image (selected/default). UI hover.
- `FormKVMALED6_MouseDown/Move/Up` (:275/:280/:285) — forward drag to host: `Invoke(241/242/243, null, e)` (frameless-window drag). UI events.

### Channel selection (the "helmet"/TK buttons)

- `SetMyChannel()` (FormKVMALED6.cs:290) — set channels 0..7 (`Length-2`) to 1, restore all 5 TK helmet + DGJH "aggregate-on" background images. No branches (loop).
- `ClrMyChannel()` (FormKVMALED6.cs:304) — inverse: channels 0..7 =0, clear TK backgrounds, set DGJH "aggregate-off" image. No branches (loop).
- `buttonTK0_Click`..`buttonTK4_Click` (FormKVMALED6.cs:318/344/370/396/422) — **[COPY-PASTE]** 5-fold. Each toggles `myChannel[n]` and its helmet image; then if all of ch0..4==1 shows DGJH "aggregate-a" image else default; then `if(my_6_10_Ch) buttonWBSR_Click(...)` (re-arm external-output). Branches: channel toggle → image + aggregate state.
- `buttonDGJH_Click` (FormKVMALED6.cs:448) — "light aggregate": `SetMyChannel()`, then re-arm WBSR if in 6/10-ch mode. 1 branch.
- `buttonWBSR_Click` (FormKVMALED6.cs:457) — toggles `my_6_10_Ch` (external-output / 6-vs-10-channel). ON→set image-a, `myChannel[8]=1`, `ClrMyChannel()`; OFF→default image, `myChannel[8]=0`. Ends `delegateForm.Invoke(my_6_10_Ch?161:160)`. 2 branches.

### Color pickers (UCColor / UCScroll delegates + preset swatches)

- `ucColor1Delegate(int r,int b,int g)` (FormKVMALED6.cs:475) — **note param order (r,b,g)**; assigns `rgbR1=r, rgbG1=g, rgbB1=b`, updates R/G/B labels + the 3 scroll sliders, then `SendLEDData()`. The central "set current color" seam. No branches.
- `ucColor2Delegate(byte onOff)` (FormKVMALED6.cs:489) — apply on/off to every SELECTED channel (`myChannel[i]==1 → myOnOff[i]=onOff`), then `SendLEDDataOnOff()`. 1 branch (per-channel).
- `ucScrollRDelegate/G/B` (FormKVMALED6.cs:501/508/515) — set `rgbR1/G1/B1=val`, update that label, `SendLEDData()`. **[COPY-PASTE]** 3-fold. No branches.
- `ucScrollWDelegate(int val)` (FormKVMALED6.cs:522) — white slider = brightness: `myBrightness=val*100/255`, `SendLEDData()`. No branches.
- `buttonC1_Click`..`buttonC8_Click` (FormKVMALED6.cs:528..563) — **[COPY-PASTE]** 8 preset swatches; each calls `ucColor1Delegate(R,B,G)` with a hardcoded color (C1=255,42,0 … C8=255,255,255 white). Order (r,b,g) per the delegate signature. UI presets.

### Persistence

- `InitData()` (FormKVMALED6.cs:577) — open `proMode.dc`; if first byte==220 read `myOnOff[10]`,`myModeS[10]`,`myBrightnessS[10]`,`myRGB[10,3]`; else/catch → `SaveData()` (write defaults). 1 branch (magic).
- `SaveData()` (FormKVMALED6.cs:620) — write magic 220 + the 4 state arrays to `proMode.dc`. No branches.
- `GetMSData(string str)` (FormKVMALED6.cs:642) — same read as InitData but from an arbitrary path (mode-slot files `{n}proMode.dc`); on bad magic just closes (no default-write). 1 branch.
- `SaveMSData(string str)` (FormKVMALED6.cs:683) — same write as SaveData to an arbitrary path (save a mode slot). No branches.

### Wire send

- `ReceivedDeviceData(byte[] data)` (FormKVMALED6.cs:704) — **empty stub** (inbound not handled here).
- `SendDeviceData(byte cmd,byte ch,byte mode,byte[] data)` (FormKVMALED6.cs:708) — the single send seam: `delegateForm.Invoke(16, {cmd,ch,mode}, data, this)`. No branches.
- `SendLEDDataOnOff()` (FormKVMALED6.cs:714) — reentrancy-guarded (`isSendData`, 1ms sleep); packs `byte[10]=myOnOff`, `SendDeviceData(0,0,myMode,data)`, then `SaveData()`. 1 branch (guard). cmd 0 = ONOFF.
- `SendLEDData()` (FormKVMALED6.cs:739) — guarded; packs `byte[18]={brightness,speed,R,G,B,0,0,0, myChannel[0..9]}`, `SendDeviceData(16,0,myMode,data)`; then writes current mode/brightness/RGB into every selected channel's `myModeS/myBrightnessS/myRGB`, `SaveData()`. 2 branches (guard + per-channel). cmd 16 = LED.
- `SendLEDMSData(int m)` (FormKVMALED6.cs:783) — guarded; packs `byte[60]` = full snapshot (myOnOff[10]+myModeS[10]+myBrightnessS[10]+myRGB[10,3]=60), `SendDeviceData(104, m, myModeS[0], data)`. cmd 104 = LEDMS (mode-slot broadcast). 1 branch.
- `SendStateData()` (FormKVMALED6.cs:857) — guarded one-shot `SendDeviceData(1,0,0,null)` (cmd 1 = STATE request). 1 branch. Called every timer tick.

### Mode buttons

- `ButtonMode_Click(int mode)` (FormKVMALED6.cs:867) — 16 br. Resets all 12 mode + 3 light-show button images to default, then a switch highlights the button matching `mode` with its "-a" (active) image. **Mode→button map is scrambled**: 100→btn1, 0→btn2, 1→btn3, 2→btn4, 7→btn5, 6→btn6, 5→btn7, 4→btn8, 3→btn9, 10→btn10, 9→btn11, 8→btn12, 201/202/203→DGX1/2/3. Pure UI selection state.
- `button1_Click` (FormKVMALED6.cs:934) — special: `myMode=100`→`ButtonMode_Click(100)` (highlight), set warm-white rgb(255,175,100), `myMode=0`, `SendLEDData()`. The "custom color" entry.
- `button2_Click`..`button12_Click` (FormKVMALED6.cs:945..1015) — **[COPY-PASTE]** 11-fold. Each sets `myMode=<scrambled n>`, `ButtonMode_Click(myMode)`, `SendLEDData()`. Modes per the map above (btn2→0, btn3→1, … btn12→8).
- `buttonDGX1/2/3_Click` (FormKVMALED6.cs:1022/1032/1042) — **[COPY-PASTE]** 3-fold light-show modes 201/202/203; each guarded a guard on `my_6_10_Ch` (disabled in external-output mode), then mode+highlight+send. 1 branch each.

### Mode-slot save/recall (KJMS + MS buttons)

- `buttonKJMS_Click` (FormKVMALED6.cs:1052) — recall slot 0: `GetMSData(...\0proMode.dc)` + `SendLEDMSData(0)`. No branches.
- `buttonMS_Click(int m)` (FormKVMALED6.cs:1058) — 5 br. Reset MS1..4 button images, highlight slot `m`'s "-a" image (switch 1..4). UI selection.
- `buttonMS1_Click`..`buttonMS4_Click` (FormKVMALED6.cs:1081/1088/1095/1102) — **[COPY-PASTE]** 4-fold; each `buttonMS_Click(n)` + `GetMSData(...\{n}proMode.dc)` + `SendLEDMSData(n)` (recall slot n). No branches.
- `SaveButtonMouseDown(sender,e)` (FormKVMALED6.cs:1109) — 6 br. On right-click (`e.Button==2097152`) start a long-press timer (`keyRDown=true, isStartTimer=true`) and identify `nowButton` (0..4) by matching sender's X to KJMS/MS1..4 location. Long-press-to-save arm.
- `SaveButtonMouseMove(sender,e)` (FormKVMALED6.cs:1146) — if pressed and pointer leaves button bounds, cancel the long-press timer. 1 branch.
- `SaveButtonMouseUp(sender,e)` (FormKVMALED6.cs:1158) — release cancels the long-press timer. 1 branch.

### Timer / framework

- `MyTimer_Event()` (FormKVMALED6.cs:1168) — 4 br. Long-press progress: if `isStartTimer` increment `longTimer`; at >=16 ticks fire the SAVE (`SaveMSData(...\{nowButton}proMode.dc)`), flash `label1` (show, `hideCount=8`); else count `hideCount` down and hide label at 0. Always ends `SendStateData()` (poll device each tick). The heartbeat.
- `Dispose(bool disposing)` (FormKVMALED6.cs:1193) — standard WinForms dispose. 1 branch.
- `InitializeComponent()` (FormKVMALED6.cs:1202) — designer boilerplate spanning lines 1202–2084 (all control instantiation + event-handler wiring). No logic. Designer/UI.

---

## Consolidation targets (top 5)

1. **UCScreenLED `SetMyNumeral*` family (9 methods, ~810 branches)** — the same 7-seg digit switch + leading-zero suppression is copy-pasted per metric block, 1–4 times per overload. Collapse to ONE `digit_segments(n) -> mask` table + a per-metric loop that writes a target digit-row. `SetMyNumeralNew(cpuTemp,gpuTemp)` needs the SEPARATE 13-seg table (§3.3) — two glyph tables total, zero switch duplication.
2. **UCScreenLED 12 `ReSet*` methods** — pure index-remap + optional artwork. Replace with 12 declarative rows (buffer arrays, `nowLedStyle`, zone→index map, artwork resources) driven by one applier. Style 1 empty + LF13 degenerate fall out for free.
3. **UCScreenLED `OnPaint` style pre-passes (styles 6/7/8/12)** — four bespoke branches + a generic tail. Model each style's compose as data (image offsets / gated segment / black-out-off / single-cell) so paint is one branchless loop + a per-style overlay descriptor.
4. **FormKVMALED6 button-click fan-out (buttonTK0..4, buttonC1..8, button1..12, buttonDGX1..3, buttonMS1..4, ucScrollR/G/B)** — ~35 near-identical handlers differing only by a constant (channel idx / preset color / mode id / slot id). Collapse to data-tagged handlers (one handler + a param, or a {control→value} table).
5. **FormKVMALED6 `InitData`/`GetMSData` (read) and `SaveData`/`SaveMSData` (write)** — two read-pairs and two write-pairs identical except path + the bad-magic fallback. One `read(path, writeDefaultOnMiss:bool)` + one `write(path)`.

## Undetermined methods

None. All 25 + 71 method bodies were read directly. Two non-behavioral notes (not gaps):
- `UCScreenLED.InitializeComponent` (:10127) and `FormKVMALED6.InitializeComponent` (:1202, spanning to 2084) are designer boilerplate — control instantiation/wiring, read as such, not per-widget-annotated (per task scope).
- `ReceivedDeviceData` (FormKVMALED6.cs:704) is an intentional empty stub (inbound device data unhandled in this form).

## Confidence

**High.** Both files read in full for structure; every method's body observed (FormKVMALED6 lines 1–1210 verbatim; UCScreenLED ctor/ReSet/OnPaint/overload-heads verbatim, digit-switch bodies cross-checked against the verified tables in AUDIT_LED_SEGMENT §3). The one modeling caution already flagged in the prior audit holds: `SetMyNumeralNew(cpuTemp,gpuTemp)` uses a DIFFERENT 13-segment glyph table than the 7-seg overloads — a consolidation must keep both tables.
