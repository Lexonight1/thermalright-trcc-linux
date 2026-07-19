# BEHAVIOR — System-Info Options panels (TRCC 2.1.6)

Per-method behavioral grind of the three "system info options" user controls: the
outer paged grid (`UCSystemInfoOptions`), the single per-source card
(`UCSystemInfoOptionsOne`, which also holds the per-metric color statics + the
name/value TextBox/Label control sources), and the theme-setting hub
(`UCThemeSetting`) that fans every child panel's callback out to the owning form.

The data model shared by the first two: each metric SOURCE (CPU/GPU/Memory/HDD/
Network/FAN + Custom) is a flat `ArrayList` of **14 slots** — index `0`=mode int;
then four `(name, sensorId, sub)` triples at `[1,2,3] / [5,6,7] / [8,9,10] /
[11,12,13]` where index 3/7/10/13 hold an int `sub`, `1/2` hold name+sensor
strings, `4/7/10/13` the trailing int. Slot map used everywhere: name→`1,5,8,11`,
sensor-string→`2,6,9,12`, sub-int→`4,7,10,13`. Persisted to `Data\config` with a
`0xDC` (220) magic byte.

---

## TRCC/UCSystemInfoOptions.cs — outer paged grid (owner of configArrayList)

Grid geometry constants: `startX=44, startY=36, addX=300, addY=199`, page size
`ConfigArrayListPageCount=12` (3 rows × 4 cols per page). Two parallel lists:
`configArrayList` (the 14-slot data rows) and `UCSystemInfoOptionsOneList` (the
matching `UCSystemInfoOptionsOne` widgets), kept index-aligned.

- `UCSystemInfoOptions` ctor (UCSystemInfoOptions.cs:56) — calls
  `InitializeComponent`, news both parallel `ArrayList`s, zeroes the Add/Up/Down
  buttons' `FlatAppearance.BorderColor` to transparent. No data load here (that is
  `GetMyNameFile`).
- `InitUCSystemInfoOptionsOneVal` (UCSystemInfoOptions.cs:66) — **default-row
  factory**. Builds one 14-slot `ArrayList` for source `n` and appends to
  `configArrayList`. `switch(n)`: `1`→CPU (TEMP=`GetSystemInfo(1)`,
  Usage=`GetSystemInfo(2)`, Clock=`GetSystemInfo(3)`, Power="CPU Package Power");
  `2`→GPU (all four via `GetSystemInfo(4..7)`); `3`→Memory ("SPD Hub Temperature",
  "Physical Memory Load", "Memory Clock", "Physical Memory Available" — hardcoded
  sensor names, no GetSystemInfo); `4`→HDD ("Drive Temperature","Total Activity",
  "Read Rate","Write Rate"); `5`→Network ("Current UP rate","Current DL rate",
  "Total UP","Total DL"); `6`→FAN (CPUFAN name="CPU" with **sub=1** not 0,
  GPUFAN=`GetSystemInfo(8)`, FAN1="PUMP1", FAN2="System 1"). No `default` → invalid
  `n` appends a 1-element list (`[n]`) which would later IndexOOB; only ever called
  with 1–6. Key branches: `n → which sensor-name set + which GetSystemInfo ids`.
- `GetMyNameFile` (UCSystemInfoOptions.cs:166) — **load path**. Opens
  `StartupPath\Data\config` (OpenOrCreate) via `BinaryReader`; if first byte==220
  reads `Int32 count` then loops `count` rows reading the exact 14-field
  `int,str,str,str,int,str,str,int,str,str,int,str,str,int` sequence into a fresh
  ArrayList appended to `configArrayList`. `catch` (empty/corrupt file) → seed the
  six built-in sources via `InitUCSystemInfoOptionsOneVal(1..6)`. Always closes
  reader+stream, then `InitUCSystemInfoOptionsOneList()` to build widgets, then
  `myTextChengeEn=true` (arms the TextChanged→save path — suppressed until now so
  loading doesn't trigger a save storm). Key branch: `firstByte==220 → parse rows;
  else/exception → seed defaults 1..6`.
- `SetMyNameFile` (UCSystemInfoOptions.cs:213) — **save path**, exact inverse of
  the reader. Writes `(byte)220`, `configArrayList.Count`, then for each row the
  same 14 typed fields in order. Flush/Close/Dispose both writer+stream. No
  validation — trusts the 14-slot shape.
- `InitUCSystemInfoOptionsOneList` (UCSystemInfoOptions.cs:244) — builds one
  `UCSystemInfoOptionsOne` per config row: sets its color via
  `SetUCSystemInfoOptionsOneColor(mode=slot0)`, fills the 5 text boxes from slots
  `1,2,5,8,11` (name + 4 metric labels), positions it on the grid
  (`Top=36+i/4*199`, `Left=44+i%4*300`, then page-wrap subtract
  `i/12*199*3`), appends to the widget list, wires its `upDateUCSystemInfoOne`
  delegate to `UpdateOne`, adds to Controls. Ends with
  `ButtonSetTopLeftAndHideShow()`. **[COPY-PASTE]** — the 5-textbox-fill +
  3-line-position block is duplicated verbatim in `buttonAdd_Click` and the reposition
  loop in `FormSystemDelete`.
- `ButtonSetTopLeftAndHideShow` (UCSystemInfoOptions.cs:266) — **pager visibility
  state machine**. `count<12` → place Add button after the last card, show Add, hide
  Up/Down, show all cards. `count>=12 && nowPage==0` → hide Add & Up, show Down,
  show cards `0..11`, hide `12..`. Else (page 1) → Add hidden iff `count>=24` else
  shown at slot `count-12`; show Up, hide Down; hide cards `0..11`, show `12..`. Key
  branches: `count<12 / (>=12,page0) / (page1, count>=24?)`.
- `FormSystemInfoShow` (UCSystemInfoOptions.cs:324) — invoked when a card's metric
  button is clicked. Finds the clicked card's index into `oneCount` (identity
  compare against widget list), stores `oneCountSub=mode` (which of the 4 rows),
  shows the shared `Form1.formSystemInfo` picker and seeds it via
  `ButtonClick(sensorId=slot[1+mode*3], name=slot[mode*3])`. Note the slot math uses
  `mode*3` / `1+mode*3` — the picker addresses triples by `mode` 0..3.
- `FormSystemInfoSet` (UCSystemInfoOptions.cs:340) — callback FROM the picker after
  a sensor is chosen: writes chosen `n` (sensorId) into slot `1+oneCountSub*3` and
  `name` into slot `oneCountSub*3` of the `oneCount` row, then `SetMyNameFile()`.
  This is the write-back completing the `FormSystemInfoShow` round-trip.
- `FormSystemDelete` (UCSystemInfoOptions.cs:348) — locate clicked card → `oneCount`;
  remove widget from Controls, remove row from BOTH parallel lists, Dispose the
  widget; then reposition every remaining card with the same grid formula
  (`Top=36+j/4*199`, `Left=44+j%4*300`, page-wrap subtract); `ButtonSetTopLeftAndHideShow()`
  + `SetMyNameFile()`. **[COPY-PASTE]** grid-position formula (3rd copy).
- `UpdateOne` (UCSystemInfoOptions.cs:373) — **central delegate dispatch** from any
  card. `switch(mode)`: `0`→`FormSystemDelete(val)`; `1/2/3/4`→`FormSystemInfoShow(mode,val)`
  (open picker for that metric row); `16`→if `myTextChengeEn`, harvest all cards'
  live TextBox text back into slots `1,2,5,8,11` of every row then `SetMyNameFile()`
  (the debounced "name edited" persist). Key branches: `mode 0=delete / 1-4=pick /
  16=name-edit-save (gated on myTextChengeEn)`.
- `UCSystemInfoOptionsTimer` (UCSystemInfoOptions.cs:411) — **per-tick refresh**
  (called by the form's timer). For each row: reads the four live values via
  `GetSystemInfoVal(sensorString, sub, ref val)` for triples at `(3,4)/(6,7)/(9,10)/
  (12,13)` and writes them into `label1..4`. Then appends **hardcoded unit
  suffixes by mode**: mode 5 (Network) → label1/2 `"KB/s"`, label3/4 `"MB"`; mode 3
  (Memory) → label2 `"%"`, label4 `"MB"`; mode 4 (HDD) → label3 `"MB/s"`, label4
  `"MB/s"`. **[GOD]-ish** — 8 branch arms of stringly unit-suffix logic hardcoded per
  mode; this is the unit-append table the metrics audit was chasing. Key branches:
  `mode==5 / mode==3 / mode==4 → which label gets which suffix`.
- `buttonAdd_Click` (UCSystemInfoOptions.cs:469) — creates a new **Custom** row
  (mode 0, name "Custom", metric names "Custom1..4", empty sensor strings, subs 0),
  builds+positions+wires its widget (duplicating `InitUCSystemInfoOptionsOneList`'s
  body), appends to both lists, `ButtonSetTopLeftAndHideShow()` + `SetMyNameFile()`.
  **[COPY-PASTE]** of the card-build block.
- `buttonUp_Click` (UCSystemInfoOptions.cs:494) — `nowPage=0; ButtonSetTopLeftAndHideShow()`.
- `buttonDown_Click` (UCSystemInfoOptions.cs:500) — `nowPage=1; ButtonSetTopLeftAndHideShow()`.
- `GetSystemInfoVal` (UCSystemInfoOptions.cs:506) — reads a card's **already-rendered
  label text** back out: `switch(sub)` 1→label1.Text … 4→label4.Text into `ref val`.
  Note `main` here indexes the WIDGET list; this is the outer form querying a card's
  displayed value (used to feed the composited theme image). Key branch: `sub → which
  labelN.Text`.
- `Dispose` (UCSystemInfoOptions.cs:526) — standard designer dispose (dispose
  `components` if disposing, chain to base).
- `InitializeComponent` (UCSystemInfoOptions.cs:535) — designer: builds Add/Up/Down
  buttons (transparent flat, resource background images `A增加数组/A上一页a/A下一页a`),
  Add at `(44,36)` size `266×189`, Up `(145,653)`, Down `(1045,653)`, all
  `Visible=false`; panel size `1254×692`, background `A0数据列表`, DoubleBuffered.
  Wires the three Click handlers.

---

## TRCC.DCUserControl/UCSystemInfoOptionsOne.cs — single source card (+ color statics)

**Per-metric color statics** (the externally-flagged constants) — one `Color` per
mode, applied to every TextBox+Label foreground by `SetUCSystemInfoOptionsOneColor`:
`myColorZDY`=Custom `(147,117,255)` purple; `myColorCpu`=`(50,197,255)` cyan;
`myColorGpu`=`(68,215,182)` teal; `myColorMem`=`(109,212,1)` green;
`myColorHdd`=`(247,181,1)` amber; `myColorNet`=`(250,100,1)` orange;
`myColorFan`=`(224,32,32)` red. Control sources: `textBoxName` + `textBox1..4` (name
+ 4 metric captions, user-editable), `label1..4` (live values), `button1..4` (open
picker for metric row), `buttonDelete` (Custom only). All fan events through the
single `upDateUCSystemInfoOne` delegate (`delegate_UCSystemInfoOptionsOne(int mode,
object val=null)`).

- `UCSystemInfoOptionsOne` ctor (UCSystemInfoOptionsOne.cs:61) — `InitializeComponent`
  then zeroes `FlatAppearance.BorderColor` on button1..4 + buttonDelete.
- `SetUCSystemInfoOptionsOneColor` (UCSystemInfoOptionsOne.cs:71) — stores `myMode`,
  then `switch(myMode)` picks `foreColor` from the static above AND sets the card's
  background image (`Resources.A自定义/Acpu/Agpu/Adram/Ahdd/Anet/Afan`). Mode 0
  additionally `buttonDelete.Show()` (only Custom cards are deletable). `default`
  → Custom color+image. Finally applies `foreColor` to all 5 TextBoxes + 4 Labels.
  Key branches: `myMode 0..6 → (color static, bg image); 0 also shows delete;
  default → ZDY`.
- `button1_Click` (UCSystemInfoOptionsOne.cs:122) — `upDateUCSystemInfoOne?.Invoke(1,this)`
  (open picker for metric row 1). **[COPY-PASTE]** with button2/3/4.
- `button2_Click` (UCSystemInfoOptionsOne.cs:127) — `Invoke(2,this)`.
- `button3_Click` (UCSystemInfoOptionsOne.cs:132) — `Invoke(3,this)`.
- `button4_Click` (UCSystemInfoOptionsOne.cs:137) — `Invoke(4,this)`.
- `textBoxAll_TextChanged` (UCSystemInfoOptionsOne.cs:142) — shared handler for all
  5 text boxes → `Invoke(16)` (no `this`; owner harvests all cards). Fires on every
  keystroke; the owner's `myTextChengeEn` gate + `SetMyNameFile` debounce the write.
- `buttonDelete_Click` (UCSystemInfoOptionsOne.cs:147) — localized confirm dialog
  (Language 1/2 → Chinese "是<删除>，否<取消>", else English "Yes<Delete>,No<Cancel>",
  Yes/No + warning icon). If result != 7 (`DialogResult.No`) → `Invoke(0,this)`
  (delete). Key branch: `Language → dialog text; result!=No → delete`.
- `Dispose` (UCSystemInfoOptionsOne.cs:164) — standard designer dispose.
- `InitializeComponent` (UCSystemInfoOptionsOne.cs:173) — designer: 4 picker buttons
  at x=245 (y 49/84/119/154, 16×30, bg `A数据选择`); `textBox1..4`+`textBoxName`
  (微软雅黑 font, dark `(35,34,39)` bg, cyan default fg, MaxLength 12 / 100 for name,
  default text TEMP/Usage/Clock/Power/CPU, all wired to `textBoxAll_TextChanged`);
  `label1..4` (12pt, right-aligned `ContentAlignment 64`, default "88℃/88%/88MHz/88W");
  `buttonDelete` (16×16 top-right, bg `P关闭按钮2`, `Visible=false`); card `266×189`,
  default bg `Acpu`, DoubleBuffered.

---

## TRCC.DCUserControl/UCThemeSetting.cs — theme-setting hub (child→owner router)

Composition-only panel: hosts every theme sub-control (背景/投屏/动画联动/键盘联动A-C/
蒙板/视频播放器/系统显示 + its Add/Color/Table satellites) and, in its ctor, binds each
child's callback to a local handler that translates the child's local `(cmd,info,
data,data1)` into a numeric command on the ONE owner delegate `delegateUCSetting`.
This is the command-code fan-out the UI-contract audit maps to render commands.

- `UCThemeSetting` ctor (UCThemeSetting.cs:51) — `InitializeComponent`, then wires 8
  child delegates to the 8 handler methods below (TouPing→`TouPingXianShi`,
  BeiJing→`BeiJingXianShi`, MengBan→`MengBanXianShi`, ShiPing→`ShiPingBoFangQi`,
  XiTongAdd→`XiTongXianShiAdd`, XiTong→`XiTongXianShi`, Color→`XianShiColor`,
  Table→`XiTongTable`). Note the keyboard-linkage (JianPanLianDong A/B/C) + DongHua
  children are hosted but NOT wired here.
- `XiTongTable` (UCThemeSetting.cs:64) — table sub-panel → element-selection. `cmd`
  0/1/2/3 → `ucXiTongXianShi1.SetXiTongNowSub((int)info)`; `cmd 4` →
  `SetXiTongNowSub((int)info,(string)data)` (with a text arg). Routes the table's
  current-sub selection into the system-display panel.
- `XianShiColor` (UCThemeSetting.cs:80) — color sub-panel → element styling.
  `mode 1`→`delegateUCSetting(112)` (owner-level, no element mutate — likely
  "font-picker open"); `2`→`ucXiTongXianShi1.UCXiTongXianShiSetOneColor(r,g,b)`;
  `3`→`...SetOneXY(r,g)` (x,y carried in r,g); `4`→`...SetOneFont(font)`. Key
  branch: `mode → color / position / font mutate, or 112 to owner`.
- `XiTongXianShi` (UCThemeSetting.cs:99) — **the busiest router** (system-display
  panel). `cmd 0`→`delegateUCSetting(128, info)` (add/select element to render);
  `cmd 1`→show the Add sub-panel, hide Color+Table; `cmd 3`→hide Add, show Color+
  Table, then `switch((int)info)` 0..4 calls the matching
  `ucXiTongXianShiTable1.UCXiTongXianShiTable{0..4}(k,(int)data)` to configure the
  table for that element kind — case 4 additionally seeds `table.textBox1.Text` from
  `((ArrayList)data1)[4]`; afterward backs up + sets the color panel from `data1`
  and fires `delegateUCSetting(129, data1[5])`; `cmd 4`→`ucXiTongXianShiColor1.
  ChangedTextBoxXY((int)data,(int)data1)`. **[GOD]-ish** — nested 5-way switch inside
  a 4-way switch, mixes visibility, table config, color backup, and two owner
  commands. Key branches: `cmd 0=add(128) / 1=show-add / 3=show-color+configure-table
  (info 0-4) / 4=xy-change`.
- `XiTongXianShiAdd` (UCThemeSetting.cs:144) — the Add sub-panel's kind buttons.
  `cmd 16`→`ucXiTongXianShi1.UCXiTongXianShiAdd(0,1,(int)info,(int)data)` (add with
  position args); `32/48/64/80`→`UCXiTongXianShiAdd(1/2/3/4)` (add fixed element
  kind). After the switch, always show Color+Table panels. Key branch: `cmd → which
  element kind added`.
- `MengBanXianShi` (UCThemeSetting.cs:168) — mask panel. `cmd 0`→`delegateUCSetting(96,
  info)` (apply mask); `1`→`(97)`; `3`→`(99)`. Key branch: `cmd → owner command 96/97/99`.
- `BeiJingXianShi` (UCThemeSetting.cs:184) — background panel. `cmd 0` first turns
  OFF the projection (`ucTouPingXianShi1.buttonOnOff_Set(false)`) AND video
  (`ucShiPingBoFangQi1.buttonOnOff_Set(false)`) — mutual-exclusion between bg /
  projection / video — then `delegateUCSetting(1, info)` (set background); `1/2/3/4`
  → owner `49/50/51/52` (background sub-options, e.g. fit modes). Key branch: `cmd 0
  = exclusive-select bg + cmd1; 1-4 = sub-option 49-52`.
- `TouPingXianShi` (UCThemeSetting.cs:208) — projection (screen-cast) panel. `cmd 0`
  turns off bg+video then `delegateUCSetting(2, info)`; `1/2/3/4/5`→owner
  `65/66/67/68/69` each carrying `info`. Same mutual-exclusion shape as background.
  Key branch: `cmd 0=exclusive-select projection+cmd2; 1-5=65-69`.
- `ShiPingBoFangQi` (UCThemeSetting.cs:235) — video-player panel. `cmd 0` turns off
  bg+projection then `delegateUCSetting(3, info)`; `cmd 1`→owner `10`. Third member
  of the bg/projection/video mutual-exclusion trio. Key branch: `cmd 0=exclusive-
  select video+cmd3; 1=10`.
- `Dispose` (UCThemeSetting.cs:250) — standard designer dispose.
- `InitializeComponent` (UCThemeSetting.cs:259) — designer: constructs & lays out all
  12 child controls with background images from `ComponentResourceManager` /
  `Resources.*`; system-display `ucXiTongXianShi1` at `(10,1)` 472×430; Color panel
  `(492,1)` 230×374; Add panel `(492,1)` 230×430 hidden; Table `(492,376)` 230×54;
  the four bottom strips (投屏/背景/蒙板/视频) at y 441/551; keyboard-linkage A/B/C +
  DongHua hidden strips at y 690-960; overall panel `732×661`, bg `P0主题设置`,
  DoubleBuffered.

---

## Consolidation targets (for the Python port)

1. **The 14-slot `ArrayList` config row** appears as an untyped positional array in
   `InitUCSystemInfoOptionsOneVal`, `GetMyNameFile`, `SetMyNameFile`,
   `InitUCSystemInfoOptionsOneList`, `FormSystemInfoSet`, `UpdateOne(16)`,
   `UCSystemInfoOptionsTimer`, `buttonAdd_Click`. → one `@dataclass MetricSource`
   with `mode` + four `MetricSlot(name, sensor, sub)` and `.to_bytes()/.from_bytes()`
   owning the `0xDC` codec. Kills the copy-paste and the slot-index magic.
2. **The grid-position formula** (`Top=36+i/4*199; Left=44+i%4*300; Top-=i/12*199*3`)
   is copy-pasted in 3 places (`InitUCSystemInfoOptionsOneList`, `FormSystemDelete`,
   `buttonAdd_Click`). → one `grid_cell(index) -> (x,y)` helper + page-wrap.
3. **The per-mode presentation table** — `myColor*` statics, background-image name,
   and unit-suffix rules (`UCSystemInfoOptionsTimer` mode 3/4/5 suffixes) are three
   separate hardcoded switches keyed by the same `mode` 0..6. → one
   `METRIC_SOURCE_STYLE[mode] = (color, bg_asset, unit_per_slot)` mapping so color,
   image, and units derive from one row (mirrors the project's `HARDWARE_METRICS`
   ownership rule).

## Undetermined

- **Owner command numbers** (1,2,3,10,49-52,65-69,96,97,99,112,128,129) emitted by
  `UCThemeSetting` handlers are only half-decoded here — meanings inferred from the
  child panel (bg/projection/video/mask/color/add). The authoritative map lives in
  `Form1`'s `delegateUCSetting` receiver, which is NOT in these three files. Confirm
  against `FormCZTV.cs` / the UI-contract audit before wiring them to render commands.
- `Form1.formSystemInfo.GetSystemInfo(1..8)` returns default sensor NAMES per host;
  the actual strings are machine-dependent (LHM sensor names) and resolved in the
  sensor form, not here.
- `nowPage` is only ever 0 or 1 (Up/Down), so the pager is hard-capped at 24 sources
  (2 pages × 12); `buttonAdd` hides at `count>=24`. Whether >24 was ever intended is
  unclear — no third page exists.
