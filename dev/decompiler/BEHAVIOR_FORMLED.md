# BEHAVIOR — `TRCC.LED/FormLED.cs` (per-method annotation)

<!-- audit-state: origin=2.0.3.0 addresses=2.1.6.0 known-bad=none -->
> **Audited against TRCC 2.0.3; citations re-anchored to TRCC 2.1.6.**
> 12 method(s) documented here changed in TRCC 2.1.6 and have NOT been re-read: `CHMS_Timer5`, `DSCL_Timer5`, `DSHX_Timer5`, `FZLD_Timer5`, `FormLEDInit`, `FormLEDLanguageSet`, `GetSystemInfo`, `InitializeComponent`, `QCJB_Timer5`, `SendHidVal`, `SetMyNameFile`, `WDLD_Timer5` — read those entries as TRCC 2.0.3 history.
> [`AUDIT_INDEX.md`](AUDIT_INDEX.md#provenance)
<!-- /audit-state -->

Source: `/home/ignorant/Downloads/TRCCCAPEN/TRCC_decompiled/TRCC.LED/FormLED.cs` (17,434 lines, 131 methods).
Every entry is line-cited. This is the **per-method map**; the effect *algorithms* (the 6 timer
families) are documented in full in `AUDIT_LED_CORE.md §4` — those methods get a one-line entry +
line here and cross-reference that doc. `[GOD]` / `[COPY-PASTE]` flags mark consolidation targets.

State glossary (fields drive nearly everything):
`nowNo` = wire product id (PM byte). `nowLedStyle` (1–12) = internal product family, derived from
`nowNo` in `FormLEDInit`. `nowLedStyleSub` = 1 only for LF11 (hard-disk). `myLedMode` (1–6) =
effect. `myLedMode1..4` = per-zone effect for ring products (styles 2/7). `myTempMode` (1=℃,2=℉).
`myOnOff`/`myBrightness` = master on + 0–100 brightness; `_1.._4` = per-zone copies.
`rgbR1/G1/B1` = user color; `_1.._4` = per-zone copies. `isLunBo`/`LunBo1..4` = rotation master +
per-source enables. `nowLunbo` = current rotation index. `ValCount`/`InfoCount` = tick counters.

---

## 1. Construction & control wiring

- `ClearButtonBouns` (FormLED.cs:1075) — resets `FlatAppearance.BorderColor` to transparent ARGB(0,0,0,0) on all 27 buttons (button1-6, N1-4, C1-8, C, F, DSCL/DSHX/QCJB/CHMS/WDLD/FZLD, LB); pure cosmetic init, no branches.
- `InitControl` (FormLED.cs:1106) — wires the 6 UC scroll/color widgets' update callbacks to the local delegates (`ucColor1Delegate`, `ucColor2Delegate`, `ucScrollR/G/B/WDelegate`) and seeds default scroll positions (R/G/B sliders 0/0, brightness 165); no branches.
- `FormLED` (FormLED.cs:1119) — ctor: `InitializeComponent()` → `ClearButtonBouns()` → `InitControl()` → `M1_M6_Init()`. No device state yet (that arrives via `FormLEDInit`).

## 2. Onboarding, product table, settings I/O

- `FormLEDLanguageSet` (FormLED.cs:1125) — **[GOD] [COPY-PASTE]** 158-branch nested `switch(nowNo)`×`switch(Form1.Language 0..8)` that ONLY sets `this.BackgroundImage` to the per-product, per-language skin resource. 14 product groups × 9 languages, each a verbatim 9-case block; `nowNo 1/2/3/32` all reuse the `D0数码屏*` skin (control-flow `goto case 48` fallthrough for LF8). Key branches: `nowNo`→skin family (数码屏/4区域/LF12/LF10/LC2/LC1/LF11/LF15/LF13/CZ1/LF8), `Form1.Language`→suffix (en/(zh)/tc/d/e/f/p/r/x). Prime candidate to collapse to a `(product,lang)→resource` table.
- `FormLEDInit(NO, mode, count, name)` (FormLED.cs:1598) — **[GOD]** the onboarding god-method. Sets `myDeviceCount=count`, `nowNo=NO`, calls `FormLEDLanguageSet()`, then a 14-way `if/else-if/switch(NO)` (1603–1802) that per product sets `ucScreenLED1.BackgroundImage`+`imageBk`, calls the product's `ReSetUCScreenLEDn()`, sets `nowLedStyle` (and `nowLedStyleSub=1` for NO==129), and shows/hides/repositions the relevant buttons + info panels. key branches: `NO`→(`1`/`2`/`3`→style1 default; `16..31`→style2 PA120 + hide textBoxTimer; `32`→style3 AK120; `48||49`→style5 LF8; `80`→style6 LF12; `96`→style7 LF10; `112`→style9 LC2 clock; `128`→style4 LC1 memory + show ucledMemoryInfo1; `129`→style10 LF11 harddisk + sub=1; `144`→style11 LF15; `160`→style12 LF13; `208`→style8 CZ1). Then (1804–1915) opens `Data\Digital\Setting<name>`, and **if leading byte==220 (0xDC)** deserializes the full settings blob (rgb, onOff, brightness, ledMode, tempMode, 5 LunBo bools, 4 zone modes/colors/onOff/brightness, timer text, memoryRatio, hardDiskCount, +style9 isTimer24/isWeekSun), replaying each through its setter (`ButtonDengGuang_Click`, `buttonCF_Click`, `ButtonLB_Set`, `buttonLB_Click`, etc.); on any read exception → for styles 2/7 calls `buttonLB_Click(null,null)`. Finally `isSaveTimer=true`. No handshake here — `NO` arrives pre-parsed from the host.
- `ucComboBoxB(mode)` (FormLED.cs:1978) — memory-ratio picker (style4/LC1). `switch(mode)`: 1→`memoryRatio=1`, 2→`=2`, 3→`=4`; then `SetMyNameFile()`.
- `ucComboBoxC(mode)` (FormLED.cs:1995) — hard-disk picker (style10/LF11): `hardDiskCount=mode`; `SetMyNameFile()`.
- `SetMyNameFile` (FormLED.cs:1941) — **the settings serializer** (mirror of the FormLEDInit reader). Writes `Data\Digital\Setting<myName>` as a `BinaryWriter` blob led by byte 220 (0xDC), then the fixed field order (rgb1, onOff, brightness, ledMode, tempMode, isLunBo, LunBo1..4, ledMode1..4, rgb/onOff/brightness ×4 zones, textBoxTimer.Text, memoryRatio, hardDiskCount, +isTimer24/isWeekSun only if `nowLedStyle==9`). Called by ~30 sites on every user change.

## 3. Color / brightness / temp-unit input handlers

- `ucColor1Delegate(r, b, g)` (FormLED.cs:2057) — **[COPY-PASTE]** sets `rgbR1/G1/B1` (note arg order r,b,g→R,G,B), then for styles 2/7 fans the value into the 4 zone copies (all 4 if `isLunBo`, else only enabled `LunBoN`); updates R/G/B textboxes+labels (guarded by `isSaveTimer=false/true`), sets the 3 scroll widgets, `SetMyNameFile()`.
- `ucColor2Delegate(onOff)` (FormLED.cs:2124) — sets master `myOnOff`; styles 2/7 fan into `myOnOff1..4` (all if isLunBo else per-LunBoN); `SetMyNameFile()`. Same fan-out shape as ucColor1Delegate.
- `ucScrollRDelegate(val)` (FormLED.cs:2159) — sets `rgbR1`(+zone `rgbR1_1..4` fan-out for 2/7), updates textBoxR/labelR, `SetMyNameFile()`. **[COPY-PASTE]** with G/B/W variants.
- `ucScrollGDelegate(val)` (FormLED.cs:2199) — same as R for `rgbG1`/textBoxG. **[COPY-PASTE]**
- `ucScrollBDelegate(val)` (FormLED.cs:2239) — same as R for `rgbB1`/textBoxB. **[COPY-PASTE]**
- `ucScrollWDelegate(val)` (FormLED.cs:2279) — brightness: `myBrightness=(byte)(val*100/255)` (0–255 slider→0–100), zone fan-out `myBrightness1..4` for 2/7, `SetMyNameFile()`. No textbox.
- `buttonC1_Click` (FormLED.cs:2314) — preset color `ucColor1Delegate(255,42,0)` (orange-red). **[COPY-PASTE] ×8**
- `buttonC2_Click` (FormLED.cs:2319) — `(255,0,110)` → R255 B110 (arg is r,b,g so B=0,G=110... note: r=255,b=0,g=110).
- `buttonC3_Click` (FormLED.cs:2324) — `(255,0,255)`.
- `buttonC4_Click` (FormLED.cs:2329) — `(0,0,255)`.
- `buttonC5_Click` (FormLED.cs:2334) — `(0,255,255)`.
- `buttonC6_Click` (FormLED.cs:2339) — `(0,255,91)`.
- `buttonC7_Click` (FormLED.cs:2344) — `(214,255,0)`.
- `buttonC8_Click` (FormLED.cs:2349) — `(255,255,255)` white. (8 preset swatches, each a one-liner into ucColor1Delegate.)
- `M1_M6_Init` (FormLED.cs:2354) — seeds the 6 `ucInfoImageN` metric widgets: bitmaps `P环H2..6`, `Mval` = CPU (img1-3) / GPU (img4-6), and `SetUCState(100)` each wrapped in try/catch that re-calls the identical `SetUCState(100)` on failure (dead-defensive).
- `buttonCF_Click(mode)` (FormLED.cs:2417) — temp-unit selector: `myTempMode=mode`; resets both C/F button skins to `P点选框`, then `switch(mode)`: 1→highlight buttonC, 2→highlight buttonF (`P点选框A`).
- `buttonC_Click` (FormLED.cs:2433) — `myTempMode=1`→`buttonCF_Click(1)`→`SetMyNameFile()`.
- `buttonF_Click` (FormLED.cs:2440) — `myTempMode=2`→`buttonCF_Click(2)`→`SetMyNameFile()`.

## 4. Effect-mode selection

- `ButtonDengGuang_Click(mode)` (FormLED.cs:2447) — **the effect-mode setter.** `myLedMode=mode`; styles 2/7 fan into `myLedMode1..4` (all if isLunBo else per-LunBoN); styles 6/8/12 push `ucScreenLED1.myLedMode=mode`; style7 pushes `ucScreenLED1.myLedMode=myLedMode3`. Resets all 6 mode-button skins then `switch(mode)` highlights the active one (`D2灯光{1..6}a`). Called both by user clicks and by FormLEDInit replay.
- `buttonDSCL_Click` (FormLED.cs:2516) — mode 1 (static): `myLedMode=1`→`ButtonDengGuang_Click(1)`→save. **[COPY-PASTE] ×6**
- `buttonDSHX_Click` (FormLED.cs:2523) — mode 2 (breathe).
- `buttonQCJB_Click` (FormLED.cs:2530) — mode 3 (gradient); **also resets `nowJianbian=0`** (phase) before the shared call.
- `buttonCHMS_Click` (FormLED.cs:2538) — mode 4 (rainbow).
- `buttonWDLD_Click` (FormLED.cs:2545) — mode 5 (temp-linked).
- `buttonFZLD_Click` (FormLED.cs:2552) — mode 6 (load-linked).

## 5. Rotation (LunBo) selection — [GOD]/[COPY-PASTE] cluster

- `ButtonLB_Set(bl)` (FormLED.cs:2559) — `isLunBo=bl`; sets buttonLB skin `P点选框A`/`P点选框`.
- `buttonLB_Click(sender, e)` (FormLED.cs:2572) — **[GOD]** the rotation-toggle god-handler (32 branches). Flips `isLunBo`, `ButtonLB_Set`, then a per-`nowLedStyle` cascade: styles 2/7 (if turning on, seed `myLedMode1..4=myLedMode`) then repaint via `buttonLB_Click`/`buttonLBN_Click`; styles 3/5/6/11 enforce "≤1 of LunBo1/2 when rotation off" (2-source products); styles 4/8/10 + default enforce "≤1 of LunBo1..4" (multi-source). Only saves if `sender!=null` (distinguishes user click from programmatic replay).
- `buttonLB_Click(bl1,bl2,bl3,bl4)` (FormLED.cs:2670) — **[COPY-PASTE]** overload for the 4-source products (styles 2/4/8/10 mode buttons button1-4): sets `LunBo1..4`, and if `nowLedStyle==2 && isLunBo` lights ALL 4 mode skins, else lights each per its bool (`D4模式{1..4}a`/`{1..4}`).
- `buttonLBN_Click(bl1,bl2,bl3,bl4)` (FormLED.cs:2718) — **[COPY-PASTE]** the ring-product twin (styles 7/8/10 buttonN1-4): identical logic to the above but paints `D4按钮{1..4}` skins and gates the all-on on `nowLedStyle==7 && isLunBo`.
- `buttonLB_Click(bl1,bl2)` (FormLED.cs:2766) — 2-source overload (button5/6 for styles 3/5/6/11): sets `LunBo1/2`, paints `D4模式5/6` skins.
- `button1_Click` (FormLED.cs:2788) — **[COPY-PASTE] ×4** source-1 toggle: for style2 or isLunBo, toggle LunBo1 on but only allow off if another source is on; else exclusive-select LunBo1 (others off). Then `buttonLB_Click(1,2,3,4)`+save.
- `button2_Click` (FormLED.cs:2823) — source-2 twin.
- `button3_Click` (FormLED.cs:2858) — source-3 twin.
- `button4_Click` (FormLED.cs:2893) — source-4 twin.
- `button5_Click` (FormLED.cs:2928) — source-1 for 2-source products (isLunBo toggle LunBo1 vs exclusive), `buttonLB_Click(1,2)`+save.
- `button6_Click` (FormLED.cs:2950) — source-2 twin of button5.
- `buttonN1_Click` (FormLED.cs:2972) — **[COPY-PASTE] ×4** ring-product source-1 (style7 caps at LunBo1..3, else LunBo1..4); routes through `buttonLBN_Click`.
- `buttonN2_Click` (FormLED.cs:3007) — ring source-2 twin.
- `buttonN3_Click` (FormLED.cs:3042) — ring source-3 twin.
- `buttonN4_Click` (FormLED.cs:3077) — ring source-4 (no style7 special-case, always 4-source).

## 6. Sensor read + per-frame value routing

- `GetSystemInfo` (FormLED.cs:3041) — **1-in-6 throttle:** `InfoCount++`; returns early if `<6`, else resets and `GetSystemInfoVal()`. Then style-specific panel-label refresh: `nowLedStyle==4 && sub==0` populates the 10 memory labels from `Form1.formSystemInfo.ucSystemInfo1.Mem*` (temp with ℃/℉ per `ucInfoImage1.myTextMode`, `MemClock*memoryRatio` MT/S, `MemUsed/1000` GB, timings); `nowLedStyle==10 && sub==1` lazy-fills the harddisk combo names then populates 4 labels from `HardDiskInfo[hardDiskCount-1]` (temp ℃/℉, load %, read/write MB/S); all else returns. The `6` fixes the sensor cadence at ~1 Hz (master tick ≈6 Hz).
- `GetSystemInfoVal` (FormLED.cs:3197) — **[COPY-PASTE] ×6** reads 6 metrics from `Form1.ucSystemInfoOptions1.GetSystemInfoVal(group,index,ref val)` (CPU: (0,1)/(0,3)/(0,2); GPU: (1,1)/(1,3)/(1,2)) into `ucInfoImage1..6`. Each of 6 identical blocks **string-sniffs the unit** (`℃`→TextMode1+myTempMode1 / `RPM`→2 / `℉`→17+myTempMode2 / `MHz`→2 / `%`→1), strips it, `Convert.ToInt32`, `SetUCState`. All 6 blocks are byte-identical bar the widget + group/index args — a table would collapse this.
- `GetVal` (FormLED.cs:3432) — **[GOD]** 99-branch per-frame numeral-routing god-method. First backfills any empty `ucInfoImageN.val1` with "10000" (sentinel). Then a giant `if(nowLedStyle==…)` selecting which sensor values feed the segment display + which `ucScreenLED1.isOn[...]` flags/icons light, and (when `isLunBo`) advancing `nowLunbo` via the `ValCount >= 6*textBoxTimer` gate over a duplicated LunBo list. Per-style: **2** (3394) CPU+GPU both, SSD/HSD icon per myTempMode, `SetMyNumeral(4 args)`; **3** (3418) WATT+BFB, rotates CPU/GPU, feeds `CpuPower`/`GpuPower`; **4** (3509) memory rotate Temp/Clock/Used with inline C→F (`*9/5+32`), clock`*memoryRatio`, used`/1000`; **10** (3573) harddisk `HardDiskInfo[...]` fields 1-4 via `SetMyNumeralHardDisk`; **5/6/11** (3649) CPU/GPU rotate w/ power via `SetMyNumeral(4 args)`; **7** (3741) both CPU/GPU `SetMyNumeralNew(2 args)`; **8** (3761) CZ1 4-way rotate `SetMyNumeralNew`; **9** (3868) clock: `DateTime.Now`, 12/24h fold (`%12`, 0→12), week Sun/Mon fold, `SetMyTimer(month,day,hr,min,dow)`; **12** (3896) early `return` (pure strip, no numerals); **default** (3900) 4-way CPU/GPU rotate driving raw `isOn[0..8]`.

## 7. Per-tick engine + wire send

- `MyTimer_Event` (FormLED.cs:4133) — the host-driven per-tick pipeline: `GetSystemInfo()` → `GetVal()` → effect compute → `LedValToScreenLed()` → `SendHidVal()` → `Invalidate()`. Effect dispatch: styles 2/7 run **all** `_New` writers every tick (DSHX/QCJB/CHMS[_7]/WDLD/FZLD — the ring blends zones); styles 4/5/6/8/9/10/11/12 each `switch(myLedMode 1..6)`→their `_Timer<style>()`; default (style1) `switch(myLedMode)`→base `_Timer()`. **[COPY-PASTE]** — 9 near-identical 6-case switches.
- `SendHidVal` (FormLED.cs:4309) — **[GOD] [COPY-PASTE] the biggest method (~3,300 lines, 13 near-identical branches).** Sets global scale `num=0.4f`, floor `b=0`. First a `checkBox1.Checked` **test branch** (4325): 4-phase RGB test pattern into a 252-byte payload. Then one one branch per `nowLedStyle` branch per product, each: allocate `byte[N*3]`, build the identical 20-byte header (`218,219,220,221,…,[12]=2,[16]=payloadLen`), fill from `ucScreenLED1.ledColor[j]*0.4` (gated on `isOn[j]`, or all-zero if `myOnOff==0`), copy into a **unique per-product physical-reorder array** (hand-wired index permutation over `Cpu1/LEDA1/WATT/BFB/SSD/HSD/…` symbols), `Concat(header,payload)`, `delegateForm.Invoke(16, myDeviceCount, frame, len)`. Branch heads + fill loops: style2 @4364 (84-loop→4644), style3 @4647 (64), style4 @4876 (31), style5 @5006 (93), style6 @5320 (124), style7 @5781 (116), style8 @6251 (**two payloads** — second `byte[48]` icon plane @~6674), style9 @6728 (61), style10 @6955 (38), style11 @7106 (93), style12 @7556 (62), default/style1 @7586 (30). See `AUDIT_LED_CORE.md §3` for the framing/reorder detail. **#1 consolidation target** — extract header builder + a per-product `(count, reorder_table)` data row.

## 8. Effect timer families — algorithms in `AUDIT_LED_CORE.md §4`

Each family applies ONE algorithm to a per-style buffer; the suffix selects buffer+bound:
`(none)`→`ledVal[10]`, `4`→`ledVal4[14]`, `5`→`ledVal5[23]`, `6`→`ledVal6[72]`, `8`→`ledVal8[13]`,
`9`→`ledVal9[31]`, `10`→`ledVal10[17]`, `LF15`→`ledValLF15[72]`, `LF13`→`ledValLF13[62]`.
`_New`/`_New_7` variants write the ring scalars (`ledHuxi`/`ledQicai`/`ledValCaihong[18|12]`/
`ledWendu`/`ledFuzai`) for styles 2/7. **Every family is [COPY-PASTE] — 9–11 methods differing only
in loop bound; collapse each to `effect(buffer, count)`.**

### 8.1 DSCL (static) — fills buffer with raw `rgbR1/G1/B1`, no time term (verified 7619–7707)
- `DSCL_Timer` (FormLED.cs:8266) — bound 10.
- `DSCL_Timer4` (FormLED.cs:8276) — bound 14.
- `DSCL_Timer5` (FormLED.cs:7639) — bound 23.
- `DSCL_Timer6` (FormLED.cs:8302) — bound 72.
- `DSCL_Timer8` (FormLED.cs:8312) — bound 13.
- `DSCL_Timer9` (FormLED.cs:8322) — bound 31.
- `DSCL_Timer10` (FormLED.cs:8332) — bound 17.
- `DSCL_TimerLF15` (FormLED.cs:8342) — bound 72.
- `DSCL_TimerLF13` (FormLED.cs:8352) — bound 62.

### 8.2 DSHX (breathe) — 0→33→66 sawtooth on `rgbTimer`, mixed 80/20 with static (AUDIT_LED_CORE §4.2)
- `DSHX_Timer` (FormLED.cs:8362) — bound 10.
- `DSHX_Timer4` (FormLED.cs:8393) — bound 14.
- `DSHX_Timer5` (FormLED.cs:7771) — bound 23.
- `DSHX_Timer6` (FormLED.cs:8461) — bound 72.
- `DSHX_Timer8` (FormLED.cs:8492) — bound 13.
- `DSHX_Timer9` (FormLED.cs:8523) — bound 31.
- `DSHX_Timer10` (FormLED.cs:8554) — bound 17.
- `DSHX_TimerLF15` (FormLED.cs:8585) — bound 72.
- `DSHX_TimerLF13` (FormLED.cs:8616) — bound 62.
- `DSHX_Timer_New` (FormLED.cs:8647) — ring scalar `ledHuxi`, floor 51, 0.8 gain, separate counter `rgbTimer1`.

### 8.3 QCJB (gradient) — 6-phase RGB wheel, `nowJianbian`+`nowJianbianTimer`, RGB_COLORFUL_TIMER=28 (AUDIT_LED_CORE §4.3)
- `QCJB_Timer` (FormLED.cs:8664) — bound 10.
- `QCJB_Timer4` (FormLED.cs:8785) — bound 14.
- `QCJB_Timer5` (FormLED.cs:8247) — bound 23.
- `QCJB_Timer6` (FormLED.cs:9033) — bound 72.
- `QCJB_Timer8` (FormLED.cs:9154) — bound 13.
- `QCJB_Timer9` (FormLED.cs:9275) — bound 31.
- `QCJB_Timer10` (FormLED.cs:9396) — bound 17.
- `QCJB_TimerLF15` (FormLED.cs:9517) — bound 72.
- `QCJB_TimerLF13` (FormLED.cs:9638) — bound 62.
- `QCJB_Timer_New` (FormLED.cs:9759) — ring triplet `ledQicai[0..2]`, same phase machine.

### 8.4 CHMS (rainbow) — spatial from 768-entry `RGBTable`, `rgbTimer+=4`, reversed LEDs, per-LED phase offset (AUDIT_LED_CORE §4.4; spatial divisor varies per style)
- `CHMS_Timer` (FormLED.cs:9877) — bound 10, `i*768/10/6`.
- `CHMS_Timer4` (FormLED.cs:9892) — bound 14, `/14/6`.
- `CHMS_Timer5` (FormLED.cs:9242) — bound 23, `/23/6`.
- `CHMS_Timer6` (FormLED.cs:9928) — bound 72, `/72` (no /6).
- `CHMS_Timer8` (FormLED.cs:9943) — bound 13, `/13/2`.
- `CHMS_Timer9` (FormLED.cs:9958) — bound 31, `/31`.
- `CHMS_Timer10` (FormLED.cs:9973) — bound 17, `/17/6`.
- `CHMS_TimerLF15` (FormLED.cs:9988) — bound 72, `/72`.
- `CHMS_TimerLF13` (FormLED.cs:10003) — bound 62, `/62`.
- `CHMS_Timer_New` (FormLED.cs:10018) — ring `ledValCaihong[18]`, `/18/6`, third counter `rgbTimer2`.
- `CHMS_Timer_New_7` (FormLED.cs:10033) — ring `ledValCaihong7[12]`, `/12/6`.

### 8.5 WDLD (temp-linked) — 5 fixed bands keyed on CPU-temp `ucInfoImage1.myVal`: <30 cyan / <50 green / <70 yellow / <90 orange(255,110,0) / else red (AUDIT_LED_CORE §4.5)
- `WDLD_Timer` (FormLED.cs:10048) — bound 10, source `ucInfoImage1.myVal`.
- `WDLD_Timer4` (FormLED.cs:10091) — bound 14, source `MemTemperature` (sub0) / `HardDiskInfo[..][1]` (sub1).
- `WDLD_Timer5` (FormLED.cs:9473) — bound 23.
- `WDLD_Timer6` (FormLED.cs:10193) — bound 72.
- `WDLD_Timer8` (FormLED.cs:10236) — bound 13.
- `WDLD_Timer9` (FormLED.cs:10279) — bound 31.
- `WDLD_Timer10` (FormLED.cs:10322) — bound 17, harddisk source.
- `WDLD_TimerLF15` (FormLED.cs:10375) — bound 72.
- `WDLD_TimerLF13` (FormLED.cs:10418) — bound 62.
- `WDLD_Timer_New` (FormLED.cs:10461) — ring `ledWendu[0..2]`.

### 8.6 FZLD (load-linked) — IDENTICAL band table to WDLD but keyed on CPU-usage `ucInfoImage3.myVal` / `MemLoad` / `HardDiskInfo[..][2]` (AUDIT_LED_CORE §4.6)
- `FZLD_Timer` (FormLED.cs:10501) — bound 10.
- `FZLD_Timer4` (FormLED.cs:10544) — bound 14, `MemLoad`/harddisk[2].
- `FZLD_Timer5` (FormLED.cs:9920) — bound 23.
- `FZLD_Timer6` (FormLED.cs:10646) — bound 72.
- `FZLD_Timer8` (FormLED.cs:10689) — bound 13.
- `FZLD_Timer9` (FormLED.cs:10732) — bound 31.
- `FZLD_Timer10` (FormLED.cs:10775) — bound 17.
- `FZLD_TimerLF15` (FormLED.cs:10828) — bound 72.
- `FZLD_TimerLF13` (FormLED.cs:10871) — bound 62.
- `FZLD_Timer_New` (FormLED.cs:10914) — ring `ledFuzai[0..2]`.

## 9. Buffer→preview mapping

- `LedValToScreenLed` (FormLED.cs:10954) — **[GOD] the 2nd-biggest method (~5,380 lines, 60 branches).** Copies the computed effect buffers (`ledVal*`) into `ucScreenLED1.ledColor[symbol]` for preview + as the source SendHidVal reads, applying per-zone brightness `(myOnOffN*myBrightnessN)*0.01`. Structure: the `nowLedStyle==2` branch (10273) has **four** sub-`switch(myLedMode1..4)` (10276/10743/11066/11533) writing the 4 ring zones independently; `==3` (11856); `==4` (12052); `==5` (12149); `==6` (12432); `==7` (12808) again 3 zone switches (myLedMode1..3 @12811/13764/14717); `==8` (14950) `for i<18`; `==9` (14960); `==10` (15147); `==11` (15265); `==12` (15548). Each branch is a long hand-unrolled per-symbol assignment list keyed off the effect's output. **#2 consolidation target** — the per-symbol unroll should be a loop over the product's LED layout.

## 10. Window chrome, numeric inputs, clock/week toggles, teardown

- `FormLED_MouseDown` (FormLED.cs:16335) — `delegateForm.Invoke(241, null, e)` — host does frameless-window drag.
- `FormLED_MouseMove` (FormLED.cs:16340) — `Invoke(242,…)` — drag move.
- `FormLED_MouseUp` (FormLED.cs:16345) — `Invoke(243,…)` — drag end.
- `buttonPower_Click` (FormLED.cs:16350) — `Invoke(255)` — host power/close command.
- `textBoxTimer_KeyPress` (FormLED.cs:16355) — digits+backspace only (`e.Handled=true` otherwise); numeric-input guard.
- `textBoxTimer_TextChanged` (FormLED.cs:16363) — if `isSaveTimer && len>0` → `SetMyNameFile()` (persist rotation seconds).
- `textBoxR_TextChanged` (FormLED.cs:16371) — **[COPY-PASTE] ×3** if `isSaveTimer && len>0`: clamp >255→"255" (early return), else `ucScrollRDelegate(val)` + sync scroll widget.
- `textBoxG_TextChanged` (FormLED.cs:16385) — G twin.
- `textBoxB_TextChanged` (FormLED.cs:16399) — B twin.
- `ButtonH24orH12(bl)` (FormLED.cs:16413) — `isTimer24=bl`; toggles buttonH24/H12 skins (`P点选框A`/`P点选框`). Style9 clock.
- `buttonH24_Click` (FormLED.cs:16428) — `ButtonH24orH12(true)`+save.
- `buttonH12_Click` (FormLED.cs:16434) — `ButtonH24orH12(false)`+save.
- `ButtonWeek(bl)` (FormLED.cs:16440) — `isWeekSun=bl`; toggles buttonWeek7/Week1 skins (Sun-start vs Mon-start).
- `buttonWeek1_Click` (FormLED.cs:16455) — `ButtonWeek(false)` (Mon start)+save.
- `buttonWeek7_Click` (FormLED.cs:16461) — `ButtonWeek(true)` (Sun start)+save.
- `Dispose(disposing)` (FormLED.cs:16467) — standard WinForms dispose: `components.Dispose()` if disposing, then base.
- `InitializeComponent` (FormLED.cs:15793) — designer-generated control tree (~800 lines of widget construction/layout; no domain logic).

---

## Consolidation targets (drives the rewrite)

1. **`SendHidVal` (4309)** — 13 branches × {alloc, identical header, brightness-scaled fill, unique reorder, invoke}. Extract one header builder + a per-product data row `(led_count, reorder_table)`; style8's second 48-byte plane is the only structural exception.
2. **`LedValToScreenLed` (10271)** — 60 branches of hand-unrolled per-symbol `ledColor[...]` assignment. Replace the unrolls with a loop over each product's LED-symbol layout + a single brightness apply.
3. **The 6 effect families (7619–10269)** — 60 timer methods that are one algorithm each applied to `(buffer, count)`. Collapse to 6 functions taking `(buffer, count)`; the `_New` ring variants are 6 more parameterized by the ring scalar target.
4. **`FormLEDLanguageSet` (1125)** — 158-branch skin/language nest → `(product, lang) → resource` lookup table.
5. **`GetSystemInfoVal` (3133)** — 6 byte-identical unit-sniff blocks → a loop over `[(widget, group, index)]` with one `parse_unit(val)` helper. (Runner-up: the LunBo click cluster `button1-4`/`buttonN1-4`/`button5-6`, ~14 near-identical exclusive-select handlers.)
