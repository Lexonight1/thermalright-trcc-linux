# C# Oracle — Per-Method Behavioral Annotation: Metrics / Overlay-Element / Clock

<!-- audit-state: origin=2.0.3.0 addresses=2.1.6.0 known-bad=FormSystemInfo.cs::UCXiTongXianShiSubTimer -->
> **Audited against TRCC 2.0.3; citations re-anchored to TRCC 2.1.6.**
> 9 method(s) documented here changed in TRCC 2.1.6 and have NOT been re-read: `FormInitDc`, `GetSystemInfo`, `InitUCXiTongXianShiSub`, `InitializeComponent`, `OnPaint`, `SetSystemVal`, `UCSystemInfo`, `UCXiTongXianShiSub`, `UCXiTongXianShiSubTimer` — read those entries as TRCC 2.0.3 history.
> [`AUDIT_INDEX.md`](AUDIT_INDEX.md#provenance)
<!-- /audit-state -->

Source root: `~/Downloads/TRCCCAPEN/TRCC_decompiled/`

Companion to [`AUDIT_METRICS_CLOCK.md`](AUDIT_METRICS_CLOCK.md), which documents the
**semantics** (units, sentinels, split rules). This file documents **every method** —
one line each, driving variables named — before any consolidation. Coverage is
exhaustive per `audit_coverage.py --dark`, not sampled.

Coverage: **UCSystemInfo 26/26 · UCXiTongXinXi 30/30 · UCXiTongXianShiSub 15/15 ·
UCShiJianXianShi 27/27 · FormSystemInfo 15/15** (property getters + `Dispose`/
`InitializeComponent` included; the audit's `--dark` count omits the `CreateParams`
property getter, hence its 24 vs the 26 real members here).

Driving variables (glossary):
- `myMode` — overlay-element kind (0=data,1=time,2=week,3=date,4=text).
- `myModeSub` — sub-format inside the kind (12/24h, date order).
- `myMainCount` — metric category (0=CPU,1=GPU,2=MEM,3=HDD,4=NET,5=FAN); **`==10000` = FAN sentinel** that bypasses the options list.
- `mySubCount` — which of 4 sub-metrics in the category (1..4).
- `myWendu` — temp unit (1=℃, 2=℉).
- `myVal`/`sysVal` — parsed scalar out-param in `SetSystemVal`.
- `first` — first-build-vs-refresh latch in `UCSystemInfo`.
- `myDCount` — 3-tick post-build countdown firing UI-update callbacks.
- `nyrMode` — date order (1=YMD,2=DMY,3=MD,4=DM) in the clock panel.
- `sjMode` — 12/24h in the clock panel (1=12H,2=24H).
- `mMode` — count of selected sub-metrics (or `-1`/`isLunbo` = carousel) in the info panel.
- `mIndex` — active metric-page M1..M6 in the info panel.

---

## 1. `TRCC.DCUserControl/UCSystemInfo.cs` (26 members) — the metric engine

- `CreateParams` get (`:99-107`) — overrides window style, ORs `ExStyle |= 0x2000000` (`WS_EX_COMPOSITED`) for flicker-free double-buffered painting of the scrolling sensor list. No branches.
- `InitMemorySize` (`:109-112`) — opens/creates the 2 MB named MMF `"shareMemory_SysInfo"` via `MemoryMappedFile.CreateOrOpen`. This is the HWiNFO transport handle. No branches.
- `ReadShareMemory(int n=0)` (`:114-120`) — reads a 1 MB page (offset `n*1048576`) from the MMF into `shareMemoryVal`, then closes+disposes the view stream. `n` selects which 1 MB half. No branches.
- `WriteShareMemory(int n, byte[] b, int count=1048576)` (`:122-128`) — writes `count` bytes of `b` into MMF page `n` (offset `n*1048576`); the app→HWiNFO command channel (page 1 is where TRCC writes the JSON request). No branches.
- `CloseShareMemory` (`:130-133`) — disposes the MMF handle. No branches.
- `FormInitDc` (`:135-209`) — the background bring-up (runs on the `ThreadStart` thread). Kills every running `HWINFO` process (`:141-147`), sleeps 200 ms, launches `HWINFO.exe` hidden from `Application.StartupPath` (`:153-159`), then polls up to 60× (`:164-199`); key branches: **handshake string** `"HWi32_GetNumberOfDetectedSensors"` in first 100 bytes → `break` (`:168`); **process died** (`flag==false`) → relaunch HWiNFO (`:188-197`); each iter sleeps 1 s. On exit starts the **1000 ms auto-reset `m_timer`** → `timer_event` (`:200-203`), then waits for `Form1.formStart != null` and calls `SetMyClose()` (`:204-208`).
- `UCSystemInfo()` ctor (`:211-225`) — `InitializeComponent`, `InitMemorySize`, zero `isMouseDown`/`xPos`/`yPos`, allocate `buttonList`/`myListName`/`myListVal`/`HardDiskInfo`, wire `MouseWheel`→`FrmMain_MouseWheel`. No branches.
- `ThreadStart` (`:248-252`) — spawns a background `Thread(FormInitDc)` and starts it. No branches. **[public entry: the sensor engine is started from outside, not the ctor.]**
- `OnPaint` (UCSystemInfo.cs:254) — chains to the base paint, then draws the scrollbar thumb `P滚动条按钮` at x = 442, y = `imageY`. Key branches: `inner panel1 height greater than the control height (list overflows) -> draw the thumb`; `otherwise -> nothing is drawn, no track, no thumb`.
- `ResetSystemInfoVal(ArrayList arrayList)` (`:264-298`) — the **per-tick fast path**. For each row `i`: set `myListName[i].Text = arrayList2[1]` (name), `myListVal[i].Text = arrayList2[2]` (value), `.Show()` if hidden (`:274-285`). Rows beyond `arrayList.Count` → `.Hide()` (`:286-292`). Branch: `panel1.Height != count*22` → resize panel + invalidate scrollbar (`:293-297`). Consumes the `[flag,name,value]` triples built by `timer_event`.
- `SetSystemInfoVal(ArrayList arrayList)` (`:300-455`) — the **first-build heavy path**. Clears `panel1`, then for each row builds a composite `Label` (22px tall) containing a checkbox `Button` (image `P点选框A` if `arrayList2[0]==1` else `P点选框`, `:340-347`), a name `Label` (grey, `:363-375`) and a value `Label` (white, `:376-388`); wires each button `Click`→`button_Click`. After the real rows it **pre-creates 30 spare hidden rows** (`:397-454`) so later ticks only mutate text. Branch: `panel1.Height > this.Height` → invalidate scrollbar (`:393-396`). **[COPY-PASTE]** the 30-spare loop duplicates the real-row builder almost verbatim (only text set to `""` and `.Hide()`).
- `ButtonClick(int n)` (`:457-483`) — programmatic radio-select of checkbox row `n`. Guard: `n<0 || n>=buttonList.Count` → return (`:463`). For each button: `i==n` → image `P点选框A` + `myCount=i`; else → image `P点选框`; disposes the swapped-out image each time. Called by `FormSystemInfo.ButtonClick`.
- `button_Click(object,EventArgs)` (`:485-509`) — user-click radio-select; matches the clicked button by `TabIndex` (`:495`), sets it `P点选框A` + `myCount=i`, all others `P点选框`, disposing swapped images. **[COPY-PASTE]** near-identical to `ButtonClick(int)` but keyed on TabIndex instead of index.
- `strlen(byte[] s)` (`:511-518`) — C-style NUL scan; returns index of first zero byte. Used to bound the ASCII decode of the MMF blob (`:680`). No branches (loop only).
- `DoSystemInfoUI(ArrayList array)` (`:520-546`) — GUI-thread apply. Branch `first==1` (`:522`) → `first=0`, full `SetSystemInfoVal(array)`, fire `upDateUCInfo?.Invoke(254)`. Else → `ResetSystemInfoVal(array)`; then `myDCount` countdown: `==1`→`Invoke(0)`, `==0`→`Invoke(16)` (`:531-542`). Always fires `Invoke(255)` and `array.Clear()` (`:544-545`). The 254/0/16/255 codes are the parent-form refresh signals.
- `UpdateSystemInfo(object b)` (`:548-552`) — marshals `DoSystemInfoUI` onto the GUI thread via `BeginInvoke`. Thread hop from `timer_event`'s worker thread. No branches.
- `SetSystemVal` (UCSystemInfo.cs:554) — pulls one numeric sensor reading out of the monitoring agent's JSON text blob. Scans `strjs` for the label `str`, skips past the label plus one character, cuts the text from there to the first "}" inclusive, parses that fragment as a JSON object and reads its "type" and "value" members; when "type" contains the requested `types` it truncates "value" at the first "." (integer part only), multiplies by the scale `n` (default 1) and stores it through the by-ref `sysVal`. Key branches: `label not found, or found at index 0 or below -> loop ends, return false and leave sysVal untouched`; `type contains types -> write sysVal, return true`; `type does not match -> continue scanning from just after this label occurrence`; the scan repeats until a match or exhaustion. No exception handling of its own — a fragment that will not parse throws to the caller.
- `GetHarddiskInfo(string head, string info)` (`:581-664`) — disk table. First call only (`HardDiskInfo.Count==0`): parse drive names from `head` after `"drive: "` up to `")"` (`:586-601`), seed each row `[name,100000,100000,100000,100000]`; then match `"s.m.a.r.t.: "` ids to rows by name-contains, falling back to positional `num3` index if no name match (`:604-634`). Every call: per drive read `"Total Activity"`(usage)→[2], `"Read Rate"`(other)→[3], `"Write Rate"`(other)→[4] from the `drive:`-anchored slice, and `"Drive Temperature"`(temperature)→[1] from the `s.m.a.r.t.:`-anchored slice (`:636-663`); sentinel `10000f` marks unread. Branches: name-match vs positional SMART assignment; try/catch swallows per-drive parse failures.
- `timer_event(object,ElapsedEventArgs)` (`:646-960`) — **[GOD]** the 1 Hz tick and the heart of the engine. Ensures HWiNFO alive (relaunch if `flag==false`, `:665-678`); reads MMF, ASCII-decodes to first NUL (`:679-680`); splits at first `{` into `head` (disk table) and `text2` (JSON), try/catch→return on malformed (`:683-692`). Then runs **six near-identical `do…while(num>0)` scans** over `text2`, one per HWiNFO type — `temperature`(`:694-748`), `fan`(`:750-785`), `clock`(`:787-822`), `usage`(`:824-866`), `power`(`:868-903`), `other`(`:905-940`) — each walking back to `{"` for the name, forward to `value`, truncating at `.`, appending the unit suffix, `Insert(0,0)` for the select-flag, and `arrayList.Add`. Branches inside scans: temp `myWendu==1`→℃ / `==2`→℉ (`num5*9/5+32`), zero-value → `"0<unit>"`; temp drops `name.ToLower().Contains("core ")`, usage drops `name.Contains("core ")` (**case differs**). Tail: `SetSystemVal` for CpuPower + the **GPU-power fallback chain** (TGP→GPU Power→GPU ASIC Power→`GpuPower=CpuPower`, `:941-945`) + 11 memory scalars (`:946-956`) + `GetHarddiskInfo` (`:957`); finally spawns `Thread(UpdateSystemInfo)` with the row list (`:958-959`). **[COPY-PASTE] the six type-scans are ~40 lines each, structurally identical — prime consolidation target.**
- `ucSystemInfoClose()` (`:988-1009`) — teardown: stop/close/dispose `m_timer`, `CloseShareMemory`, kill every `HWINFO` process (try/catch). Branch: process name contains `"HWINFO"` → `Kill`.
- `FrmMain_MouseWheel` (`:1011-1027`) — scroll: `imageY -= e.Delta*3/10`, clamp `[0,708]`, invalidate scrollbar, and if list taller than viewport reposition `panel1.Top` proportionally. Branches: two clamps + taller-than-viewport reposition.
- `UCSystemInfo_MouseDown` (`:1029-1053`) — begins drag-scroll only if `e.Button==1048576` (right-button constant); sets `isMouseDown`, `imageY=yPos-15`, clamp `[0,708]`, reposition panel. Branches: right-button gate + two clamps + taller reposition.
- `UCSystemInfo_MouseMove` (`:1055-1076`) — while `isMouseDown`, same clamp+reposition as MouseDown. Branches: drag gate + two clamps + reposition.
- `UCSystemInfo_MouseUp` (`:1078-1100`) — ends drag (`isMouseDown=false`), one final clamp+reposition. Branches: drag gate + two clamps + reposition. **[COPY-PASTE] the clamp+reposition block is duplicated across MouseWheel/Down/Move/Up.**
- `Dispose(bool disposing)` (`:1102-1109`) — standard WinForms dispose of `components`. Branch: `disposing && components != null`.
- `InitializeComponent` (UCXiTongXianShiSub.cs:527) — designer plumbing: lays out one 60x60 double-buffered tile with background `P数据` holding three stacked labels at (2,1), (2,21) and (2,41), each 56x18, font 微软雅黑 10.5pt charset 134, foreground ARGB(50,197,255), text centred, with both the labels and the tile itself wired to the same click and double-click handlers.

---

## 2. `TRCC.DCUserControl/UCXiTongXinXi.cs` (30 members) — System-Info overlay config panel

Emits every edit through `delegateUCXinxi(int cmd, object info, object data, object data1)`. Command map is §3.5 of `AUDIT_METRICS_CLOCK.md`.

- `UCXiTongXinXi()` ctor (`:89-110`) — `InitializeComponent` then strips the focus-border color (`FromArgb(0,0,0,0)`) on 19 buttons. No branches.
- `buttonOnOff_Set(bool bl)` (`:112-123`) — sets `buttonOn=bl` and the slider image `P滑动开`(on)/`P滑动关`(off). Branch: `bl`. Public — parent calls to reflect loaded state without firing the delegate.
- `buttonOnOff_Click` (`:125-137`) — toggles `buttonOn`, calls `buttonOnOff_Set`, emits **cmd 0** with new state. Branch: toggle.
- `ButtonM_Set(int mode)` (`:139-169`) — sets active metric-page `mIndex=mode`; resets all six M-buttons to inactive `PM1..PM6`, then lights `PM{mode}a` for `mode∈1..6`. Branches: `mode` 1..6. Public state-reflect.
- `buttonM1_Click`…`buttonM6_Click` (`:171-211`) — six handlers, each sets `mIndex=k`, `ButtonM_Set(mIndex)`, emits **cmd 1** with `mIndex`. **[COPY-PASTE] six identical bodies differing only by the constant 1..6.**
- `Button_Set(bool a1..a6)` (`:213-269`) — sets the six sub-metric checkbox flags `m1..m6` and each button image `P选择框Ma`(on)/`P选择框M`(off). Six independent `aN` branches. Public state-reflect. **[COPY-PASTE] six identical if/else image blocks.**
- `button1_Click`…`button6_Click` (`:271-359`) — six handlers, each toggles `mN`, recomputes `mMode = Σ(mN?1:0)` (count of selected), calls `Button_Set(...)`, emits **cmd 2** with `(idx, isLunbo? -1 : mMode, mN)`. Branch per handler: toggle; `isLunbo` forces the payload's 2nd arg to `-1` (carousel). **[COPY-PASTE] six identical bodies differing only by index N.**
- `ButtonDL_Set(int mode)` (`:361-390`) — sets multi-select-vs-carousel. Branch **`mode<0`** → `isLunbo=true`, carousel images (`P多选`/`P轮播a`/`P轮播a`), **disable** M1..M6; else → `isLunbo=false`, multi-select images (`P多选a`/`P轮播`/`P多选a`), **enable** M1..M6. Public state-reflect.
- `buttonDuoXuan_Click` (`:392-397`) — recompute `mMode=Σ(mN)`, `ButtonDL_Set(mMode)`, emit **cmd 2** `(0, mMode)` (multi-select chosen).
- `buttonLunBo_Click` (`:399-405`) — force `mMode=-1`, synthesize a `buttonM1_Click`, `ButtonDL_Set(-1)`, emit **cmd 2** `(0, -1)` (carousel chosen). Note it calls `buttonM1_Click(null,null)` which re-emits cmd 1 as a side effect.
- `textBox_KeyPress` (`:407-413`) — digit-only filter: non-numeric and non-backspace → `e.Handled=true` (swallow). Branch: `!IsNumber && != '\b'`.
- `textBoxTimer_TextChanged` (`:415-421`) — if text length>0, emit **cmd 3** with the carousel-interval string. Branch: non-empty.
- `buttonSZZT_Click` (`:423-426`) — emit **cmd 4** (number-font picker). No branches.
- `labelNColor_Click` (`:428-431`) — emit **cmd 6** (number color picker). No branches.
- `buttonWZZT_Click` (`:433-436`) — emit **cmd 5** (text/unit-font picker). No branches.
- `labelTColor_Click` (`:438-441`) — emit **cmd 7** (text/unit color picker). No branches.
- `buttonMode_Click` (`:443-455`) — the combined mode toggle. Branch: `isLunbo` → set false + `buttonDuoXuan_Click`; else set true + `buttonLunBo_Click`.
- `buttonDWXS_Click` (`:457-460`) — emit **cmd 8** (unit-display on/off — the C# analog of "draw unit vs bare number", relevant to the 0xDC flag-template unit memo). No branches.
- `Dispose(bool)` (`:462-469`) — standard dispose. Branch: `disposing && components != null`.
- `InitializeComponent` (`:471-978`) — designer: 26 controls at fixed coords, bg `P01系统信息`, size 712×100, `AutoScaleMode=DPI(3)`; `textBoxTimer` default `"2"`, MaxLength 2; size labels default `"9"`, font labels default `"微软雅黑"` 9pt Point; `buttonDuoXuan`/`buttonLunBo` start hidden. No logic branches.

---

## 3. `TRCC.DCUserControl/UCXiTongXianShiSub.cs` (15 members) — the overlay element (DTO + live text)

One draggable 60×60 element. Three labels: label1=name, label2=numeric, label3=unit. Emits via `delegateXiTongSub`.

- `UCXiTongXianShiSub()` ctor (`:47-53`) — `InitializeComponent`, load selection-highlight image `P选中`. No branches.
- `InitUCXiTongXianShiSub()` (`:55-117`) — sets the background image + label colors by kind. Branch `myMode`: **0=data** → bg `P数据`, `myMainCount` maps label1 color (0→Cpu,1→Gpu,2→Mem,3→Hdd,4→Net,5→Fan,**10000→Fan**, default→ZDY custom), label2/label3 = `myColor`; **1=time** → bg `P时间`, hide label1+label3; **2=week** → bg `P星期`, hide 1+3; **3=date** → bg `P日期`, hide 1+3; **4=text** → bg `P文本`, hide 1+3, label2.Text=`myText`. Non-data modes show only label2.
- `XYSet(int x,int y)` (`:139-143`) — set `myX/myY`; **silent** (no delegate). No branches.
- `ScreenXYSet(int x,int y)` (`:145-150`) — set `myX/myY` **and** emit **cmd 3** `(this,x,y)` (drag-moved on screen). No branches.
- `ColorSet(Color)` (`:152-157`) — set `myColor`, apply to label2+label3 ForeColor (numeric+unit recolor; label1/name keeps its category color). No branches.
- `TextSet(string)` (`:159-166`) — set `myText`; branch **`myMode==4`** → also push to label2 (text elements are live-editable; other kinds ignore the text). 
- `FontSet(Font)` (`:168-176`) — swap `myFont`; disposes the old font unless it was null or the shared `SystemFonts.DefaultFont` (resource-safe). Branch: old-font-disposable guard.
- `OnPaint(PaintEventArgs)` (`:178-186`) — base paint, then if `isSelect` draw the highlight overlay `imageSelect` at (0,0). Branch: `isSelect`.
- `UCXiTongXianShiSub_Click` (`:188-191`) — emit **cmd 1** `(this)` (element selected). No branches.
- `UCXiTongXianShiSub_DoubleClick` (`:193-196`) — emit **cmd 2** `(this)` (element opened/edited). No branches.
- `label_Click` (`:198-201`) — forwards a child-label click to `UCXiTongXianShiSub_Click` (so clicking the text counts as clicking the element). No branches.
- `label_DoubleClick` (`:203-206`) — forwards to `UCXiTongXianShiSub_DoubleClick`. No branches.
- `UCXiTongXianShiSubTimer(string val="", string dw="")` (`:188-288`) — **[GOD]** the per-tick text composer. **FAN sentinel first:** `myMainCount==10000` → label1="FAN", label2=`val`, label3=`dw`, **return** (bypasses everything, `:190-196`). Else `myMode`: **0=data** → read `Form1.ucSystemInfoOptions1.UCSystemInfoOptionsOneList[myMainCount]`, then `mySubCount` 1..4 picks `textBoxN`→label1 (name) and `labelN`→**regex-split** into label2=`Regex.Replace(...,"[^\d]","")` (digits) + label3=remainder (unit); **1=time** `myModeSub` 0→`HH:mm`, 1→`hh:mm tt` InvariantCulture (AM/PM), 2→`HH:mm`; **2=week** builds a 7-name array by `Form1.Language` (1=Chinese 星期*, else SUN..SAT) indexed by `DayOfWeek`; **3=date** `myModeSub` 0/1→`yyyy/MM/dd`, 2→`dd/MM/yyyy`, 3→`MM/dd`, 4→`dd/MM`; **4=text** → echo `myText`. **[COPY-PASTE] the 4 `mySubCount` cases differ only by textBoxN/labelN index.**
- `Dispose(bool)` (`:518-525`) — standard dispose. Branch: `disposing && components != null`.
- `InitializeComponent` (`:299-355`) — designer: three labels at (2,1)/(2,21)/(2,41), 56×18, default YaHei 10.5pt Point, default color `(50,197,255)`; wires each label Click/DoubleClick; size 60×60, `AutoScaleMode=DPI(3)`. No logic branches. Note element default `myFont` is **36pt Point** (`:31`) — the render font — distinct from these 10.5pt panel labels.

---

## 4. `TRCC.DCUserControl/UCShiJianXianShi.cs` (27 members) — Clock/date config panel

Emits via `delegateUCShiJian`. Owns `sjMode`(12/24h), `nyrMode`(date order 1..4), and three independent visibility bools `isNyr`/`isXs`/`isXq`. **NOTE two overload pairs** — `ButtonNYR_Set` and `ButtonXS_Set` each exist as an `int` (radio image) and a `bool` (on/off checkbox) variant; the `int` sets date-order/hour-format, the `bool` sets the field's visibility.

- `UCShiJianXianShi()` ctor (`:77-87`) — `InitializeComponent` + strip focus-border on 7 buttons. No branches.
- `ButtonOnOff_Set(bool bl)` (`:89-100`) — set `buttonOn=bl`, slider image `P滑动开`/`P滑动关`. Branch: `bl`. Public state-reflect.
- `buttonOnOff_Click` (`:102-114`) — toggle `buttonOn`, `ButtonOnOff_Set`, emit **cmd 0** with state. Branch: toggle.
- `ButtonNYR_Set(int nyr)` **[overload A: date order]** (`:116-150`) — set `nyrMode=nyr`; `nyr` 1..4 lights one of buttonNYR1..4 (`P点选框A`) and sets the date-order preview image (1→`PYMD`,2→`PDMY`,3→`PMD`,4→`PDM`). Branches: `switch` 1..4. Public state-reflect.
- `buttonNYR1_Click`…`buttonNYR4_Click` (`:152-178`) — four handlers, each `nyrMode=k`, `ButtonNYR_Set(nyrMode)`, emit **cmd 1** with `nyrMode`. **[COPY-PASTE] four identical bodies differing by 1..4.**
- `ButtonXS_Set(int sj)` **[overload A: hour format]** (`:180-196`) — set `sjMode=sj`; `sj`: 1→light buttonXS1 + preview `P12H`; 2→light buttonXS2 + preview `P24H`. Branches: `switch` 1..2. Public state-reflect.
- `buttonXS1_Click` (`:198-203`) — `sjMode=1`, `ButtonXS_Set(1)`, emit **cmd 2**. No branches.
- `buttonXS2_Click` (`:205-210`) — `sjMode=2`, `ButtonXS_Set(2)`, emit **cmd 2**. No branches.
- `ButtonNYR_Set(bool bl)` **[overload B: date visibility]** (`:212-223`) — set `isNyr=bl`, checkbox image `P选择框Ma`(on)/`P选择框M`(off). Branch: `bl`. Public state-reflect. **[COPY-PASTE] same shape as ButtonXS_Set(bool)/ButtonXQ_Set(bool).**
- `buttonNYR_Click` (`:225-237`) — toggle `isNyr`, `ButtonNYR_Set(isNyr)`, emit **cmd 3** (date on/off). Branch: toggle.
- `ButtonXS_Set(bool bl)` **[overload B: hour visibility]** (`:239-250`) — set `isXs=bl`, checkbox image `P选择框Ma`/`P选择框M`. Branch: `bl`. **[COPY-PASTE].**
- `buttonXS_Click` (`:252-264`) — toggle `isXs`, `ButtonXS_Set(isXs)`, emit **cmd 4** (hour on/off). Branch: toggle.
- `buttonNYRZT_Click` (`:266-269`) — emit **cmd 5** (date-font picker). No branches.
- `labelNColor_Click` (`:271-274`) — emit **cmd 6** (date-color picker). No branches.
- `buttonXSZT_Click` (`:276-279`) — emit **cmd 7** (hour-font picker). No branches.
- `labelXColor_Click` (`:281-284`) — emit **cmd 8** (hour-color picker). No branches.
- `ButtonXQ_Set(bool bl)` **[week visibility]** (`:286-297`) — set `isXq=bl`, checkbox image `P选择框Ma`/`P选择框M`. Branch: `bl`. **[COPY-PASTE].**
- `buttonXQ_Click` (`:299-311`) — toggle `isXq`, `ButtonXQ_Set(isXq)`, emit **cmd 9** (week on/off). Branch: toggle.
- `buttonXQZT_Click` (`:313-316`) — emit **cmd 10** (week-font picker). No branches.
- `labelXQColor_Click` (`:318-321`) — emit **cmd 11** (week-color picker). No branches.
- `buttonXiaoshi_Click` (`:323-335`) — cycles hour format `sjMode` 1↔2, `ButtonXS_Set(sjMode)`, emit **cmd 2**. Branch: `sjMode==1`→2 else 1. (Alternate entry to the same cmd 2 as buttonXS1/XS2.)
- `buttonRiQi_Click` (`:337-346`) — cycles date order `nyrMode++`, wrap 5→1, `ButtonNYR_Set(nyrMode)`, emit **cmd 1**. Branch: wrap.
- `Dispose(bool)` (`:348-355`) — standard dispose. Branch: `disposing && components != null`.
- `InitializeComponent` (`:357-…`) — designer: builds the 24 buttons/labels (NYR1..4, XS1/2, NYR/XS/XQ checkboxes, per-field size/font/color labels default `"9"`/`"微软雅黑"` 9pt Point), bg `P01时间显示`, size 712×100, `AutoScaleMode=DPI(3)`. No logic branches.

---

## 5. `TRCC/FormSystemInfo.cs` (15 members) — the sensor-picker dialog

Hosts one `UCSystemInfo`. Resolves the canonical sensor **name** per metric slot with a name-priority fallback chain, and reports the user's pick back via `upDateInfo`.

- `FormSystemInfo()` ctor (`:29-35`) — `InitializeComponent`, strip OK/Cancel focus-border, wire `ucSystemInfo1.upDateUCInfo = UpDateUCInfo`. No branches.
- `UpDateUCInfo(int mode)` (`:37-40`) — forwards the child's list-update signal outward: `upDateInfo?.Invoke(0, mode)`. No branches.
- `ThreadStart()` (`:42-45`) — starts the child engine's `ThreadStart` (spawns `FormInitDc`). No branches.
- `FormSystemInfoClose()` (`:47-50`) — forwards to `ucSystemInfo1.ucSystemInfoClose()` (timer+MMF+HWiNFO teardown). No branches.
- `FormSystemInfo_FormClosing` (`:52-55`) — **cancels the close** (`e.Cancel=true`) — the dialog hides, never disposes. No branches.
- `GetSystemInfo(int n)` (`:57-271`) — **[GOD]** the metric→sensor-name priority map. `n` 1..8, each case scans `ucSystemInfo1.myListName` in **priority order, first match wins**: **1 CPU-temp** = name=="CPU" AND value has ℃/℉ → "CPU Package" → "CPU Die (average)" → "CPU (Tctl/Tdie)"; **2 CPU-usage** = "Total CPU Usage"; **3 CPU-clock** = "P-core 0 Clock" → contains "Core 0 Clock" → "Ring/LLC Clock"; **4 GPU-temp** = "GPU Temperature"; **5 GPU-usage** = "GPU Utilization"→"GPU Core Load"→"GPU D3D Usage"; **6 GPU-clock** = "GPU Clock"→"GPU Shader Clock"; **7 GPU-power** = "Total Graphics Power (TGP)"→"GPU Power"→"GPU ASIC Power"; **8 GPU-fan** = "GPU Fan1"→"GPU Fan". Default/no-match → `""`. **[COPY-PASTE] every fallback tier is an identical `for` loop with a different literal — 8 cases, ~20 loops. This IS the metric-key table a port must reproduce.**
- `GetSystemInfoVal(string iName, int n, ref string val)` (`:269-294`) — returns the value string of the `n`-th row whose name equals `iName` (handles duplicate names). Branches: found (`i<count`) → `val = myListVal[i].Text`; else → `val=""`. The `n` counter skips to the n-th duplicate.
- `ButtonClick(int n, string name)` (`:296-311`) — programmatically radio-selects the `n`-th list row named `name` by delegating to `ucSystemInfo1.ButtonClick(i)`. Branch: name match with `n<=0` → click+break; else `n--` (skip to n-th duplicate).
- `buttonOK_Click` (`:313-328`) — confirm: read the currently-selected row's name (`myListName[myCount]`), count how many earlier rows share that name → `num` (the duplicate index), emit `upDateInfo?.Invoke(1, num, name)`, hide. Branch: duplicate-name counting loop. This is the "user picked sensor X (the num-th one named X)" report.
- `buttonCancel_Click` (`:330-334`) — emit `upDateInfo?.Invoke(2)` (cancelled), hide. No branches.
- `FormSystemInfo_MouseDown` (`:336-347`) — begin window-drag only if `e.Button==1048576` (right-button): store negative cursor offset in `_mousePoint`, `isMouseDown=true`. Branch: right-button gate.
- `FormSystemInfo_MouseMove` (`:349-357`) — while `isMouseDown`, move the borderless form to follow the cursor via the stored offset. Branch: drag gate.
- `FormSystemInfo_MouseUp` (`:359-370`) — end drag if `e.Button==1048576`; the `_mousePoint.IsEmpty` check is a no-op empty `if`. Branch: right-button gate (+ dead empty branch).
- `Dispose(bool)` (`:372-379`) — standard dispose. Branch: `disposing && components != null`.
- `InitializeComponent` (`:381-460`) — designer: OK (`P圆形确定`) + Cancel (`P圆形关闭`) buttons, embeds `ucSystemInfo1` at (20,50) 466×742, borderless 490×800, bg `P0系统信息`, `AutoScaleMode=DPI(3)`, wires FormClosing/Mouse handlers. No logic branches.

---

## 6. Consolidation targets (ranked)

1. **`timer_event`'s six type-scans (`UCSystemInfo:694-940`)** — six ~40-line `do…while` blocks identical except the `"type":"<t>"` literal, the unit suffix, and the temp/usage `"core "` drop. One `parse_typed_sensors(text2, type, unit_fn, drop_core)` generator collapses ~240 lines to one. **[COPY-PASTE][GOD]** Already the contract of `AUDIT_METRICS_CLOCK.md §2.2`.
2. **`FormSystemInfo.GetSystemInfo`'s 8-case priority map (`:57-251`)** — ~20 hand-rolled `for` loops. Replace with a `dict[slot] → tuple[MatchRule,…]` table (exact/contains/value-has-unit predicates) driven by one resolver. **[COPY-PASTE][GOD]** This is the metric-key table the Linux `FormSystemInfo` name-priority port needs verbatim.
3. **The 12 metric-page/sub-metric click handlers (`UCXiTongXinXi.buttonM1..M6_Click` + `button1..6_Click`)** — 12 bodies differing only by an index constant. Collapse to two parameterized handlers (`on_metric_page(i)`, `on_submetric_toggle(i)`) or a `Sender→index` map. **[COPY-PASTE]**
4. **The `Set` overload/checkbox families** — `UCXiTongXinXi.Button_Set` (6 if/else image blocks), and `UCShiJianXianShi`'s three `*_Set(bool)` visibility toggles + four `buttonNYR{1..4}_Click`. All are "flag→one-of-two-images / emit cmd" shapes; one `ToggleButton` helper (state, on-img, off-img, cmd) covers every occurrence across both panels. **[COPY-PASTE]** — and it removes the `ButtonNYR_Set`/`ButtonXS_Set` int-vs-bool overload ambiguity by giving each a distinct name.
5. **`UCXiTongXianShiSubTimer`'s 4 `mySubCount` cases (`:202-224`)** — identical except `textBoxN`/`labelN` index. Index the options-one control by `mySubCount` instead of switching. Bonus adjacency: the mouse-scroll clamp+reposition block is duplicated 4× across `UCSystemInfo`'s wheel/down/move/up.

## 7. Undetermined (not resolvable from these 5 files)

- **`UCSystemInfoOptionsOne` / `UCSystemInfoOptionsOptions1`** — the `myColorCpu/Gpu/Mem/Hdd/Net/Fan/Zdy` category colors and the `textBoxN`/`labelN` per-metric name/value controls that `UCXiTongXianShiSubTimer` case-0 reads. Defined elsewhere (`Form1.ucSystemInfoOptions1`); their population/format is out of scope here.
- **Panel-`nyrMode` (1=YMD..4=DM) → element-`myModeSub` (0/1=yyyy/MM/dd,2=dd/MM/yyyy,3=MM/dd,4=dd/MM) translation** — the two enumerations differ; the mapping code lives in the parent `Form1`/theme layer, not in `UCShiJianXianShi` or `UCXiTongXianShiSub`. A port must not assume panel index == element index.
- **The `upDateUCInfo` numeric codes (0/16/254/255)** consumed in `DoSystemInfoUI` — their meaning (which parent refresh each triggers) is defined by the `Form1` subscriber, not visible here.
- **`Form1.formStart.SetMyClose()` / `Form1.Language` / `Form1.ucSystemInfoOptions1`** — cross-form globals; only their read-shape is knowable from these files.
- **`WriteShareMemory` callers** — the app→HWiNFO request payload format (what TRCC writes to page `n`) is not in these files.

## 8. Confidence

**High** for every method's control flow, driving variables, branch outcomes, and emitted cmd codes — all line-cited against the verbatim decompile, cross-checked with the existing `AUDIT_METRICS_CLOCK.md` (which independently documents the same unit/sentinel/split semantics). **Medium** only for the *downstream meaning* of the numeric callback codes and the panel→element index translation, which are explicitly listed as undetermined (§7) because their definitions live in unaudited files. No inference is presented as fact.
- `ResolveExecutablePath` (UCXiTongXianShiSub.cs:452) — resolves a user-supplied program name to a full path for the icon/link overlay element, returning null when it cannot. Search order: (1) if the name is already rooted and the file exists, return its full path; (2) otherwise append ".exe" unless the name already ends in ".exe" (case-insensitive, InvariantCulture), then walk every entry of the PATH environment variable split on ';' with empty entries removed; (3) then try four fixed folders in order — ProgramFiles, ProgramFilesX86, the Windows folder plus "System32", and the Windows folder itself. Key branches: `name null/empty/whitespace -> return null`; `rooted and File.Exists -> return the full path`; `rooted probe throws -> swallowed, fall through to the PATH search`; `PATH entry contains the file -> return that combined path`; `per-entry exception -> swallowed, continue with the next entry`; `fixed folder empty -> skipped`; `fixed folder contains the file -> return that path`; `nothing matched -> return null`.
