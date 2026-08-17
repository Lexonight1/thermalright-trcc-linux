# Behavior: Overlay-Element Editor UserControls

<!-- audit-state: origin=2.0.3.0 addresses=2.1.6.0 known-bad=none -->
> **Audited against TRCC 2.0.3; citations re-anchored to TRCC 2.1.6.**
> 6 method(s) documented here changed in TRCC 2.1.6 and have NOT been re-read: `InitializeComponent`, `UCXiTongXianShiAdd`, `UCXiTongXianShiColor`, `UCXiTongXianShiSelect`, `UCXiTongXianShiSet_UCXiTongXianShiSubArray`, `XiTongXianShiSub` — read those entries as TRCC 2.0.3 history.
> [`AUDIT_INDEX.md`](AUDIT_INDEX.md#provenance)
<!-- /audit-state -->

Per-method behavioral annotation of the four `UCXiTongXianShi*` UserControls that
implement the DC "system-info overlay element" editor. Source under
`~/Downloads/TRCCCAPEN/TRCC_decompiled/TRCC.DCUserControl/`.

These four controls form the **overlay-element authoring surface**:

- **`UCXiTongXianShi`** — the *grid container*: holds the list of overlay elements
  (`UCXiTongXianShiSub` tiles) in a 7-wide grid, plus a master on/off toggle and an
  "add" button. Fans events up to the owner (`Form1`) via `delegateXiTong`.
- `UCXiTongXianShiColor` (UCXiTongXianShiColor.cs:135) — constructor for the overlay element's colour/position/font editor. Sets the two re-entry guards `isTextXYEnabled` and `isTextEnabled` true, the working font to 微软雅黑 9pt GraphicsUnit Point (3) charset 134, `isGetRGB` false (eyedropper idle) and `myColorChange` false (nothing to persist yet), runs `InitializeComponent`, then calls `ClearButtonBouns` and hands its own two callbacks to the child pickers — `UCColorBDelegate` to the brightness/bar picker `ucColorB1` and `UCColorCDelegate` to the colour-wheel picker `ucColorC1`. No branches. EXTERNAL: UCColorB.cs, UCColorC.cs.
- `UCXiTongXianShiAdd` (UCXiTongXianShiAdd.cs:86) — constructor for the "add content" popup (the picker that inserts a new overlay element into the LCD layout). Initializes drawing state before the designer runs: text font 微软雅黑 9pt, GraphicsUnit Point (3), charset 134; a solid brush seeded from `UCSystemInfoOptionsOne.myColorZDY`; a `StringFormat` later set to LineAlignment centre (1) and Alignment near (0); `isMouseDown`/`isMouseDownButton` false; `imageY` and `offsetY` 0; `nowCoutMain`/`nowCoutSub` -1; `myX`/`myY` 0; `isFanLcd` false and `fanLcdVal` 0. After `InitializeComponent` it clears the flat border colour (ARGB 0,0,0,0) on the Timer/Week/Date/Text buttons, loads the row artwork `P点选框` and `P点选框A`, the scrollbar thumb `P滚动条按钮` and the top mask `P01增加内容遮罩`, sets the scroll thumb start position to 64 + thumbHeight/2, and subscribes the mouse-wheel handler. No branches.
- **`UCXiTongXianShiTable`** — the *format-mode switch strip*: a single-row control that
  shows ONE of {unit toggle, 12/24H toggle, date-order cycle, free-text box} depending
  on which element type is selected; emits format changes via `delegateXiTongTable`.

Delegate command vocabulary is integer-`cmd`-based (see per-method notes). The container's
`delegateXiTong(cmd, info, data, data1)` uses: 0=on/off, 1=request-add, 2=deleted/refresh,
3=element-selected (carries X,Y,color,font,text,index), 4=passthrough of a Sub's format edit.

---

## UCXiTongXianShi.cs — grid container (15 methods)

- `UCXiTongXianShi` ctor (UCXiTongXianShi.cs:50) — calls `InitializeComponent()`, zeroes the
  flat-border color on `buttonOnOff`/`buttonAdd`, and news up the empty `UCXiTongXianShiSubArray`
  (`ArrayList` of child tiles). No branches.
- `UCXiTongXianShiSetOneFont` (UCXiTongXianShi.cs:58) — applies a `Font` to the currently-selected
  tile via `FontSet`; key branch: `nowCount != -1` guard (no selection → no-op); inner `try/catch`
  swallows index errors.
- `UCXiTongXianShiSetOneXY` (UCXiTongXianShi.cs:72) — sets the selected tile's X/Y via `XYSet(x,y)`;
  branch: `nowCount != -1` guard + swallow-all `catch`.
- `UCXiTongXianShiSetOneColor` (UCXiTongXianShi.cs:86) — sets the selected tile's color via
  `ColorSet(Color.FromArgb(r,g,b))`; branch: `nowCount != -1` guard + swallow-all `catch`.
  (These three One-setters are a copy-paste trio — same guard+try shape, different mutator.) **[COPY-PASTE]**
- `UCXiTongXianShiSet_UCXiTongXianShiSubArray` (UCXiTongXianShi.cs:100) — **full rebuild of the grid
  from a persisted layout**. Loop 1: reverse-iterates the existing array, removing/disposing every
  child tile. Loop 2: for each incoming `ArrayList` row builds a `UCXiTongXianShiSub`, unpacks 9
  fields by fixed index (0=myMode,1=myModeSub,2=myX,3=myY,4=myMainCount,5=mySubCount,6=myColor
  [Color],7=myFont [Font],8=myText [string]), **clamps myX/myY to ≤1920**, positions it in the
  7-column grid (`Left=5+i%7*67`, `Top=35+i/7*66`), adds it, inits, shows, wires its
  `delegateXiTongSub=XiTongXianShiSub`. Final branch: `arrayList.Count < 42` → reposition+show the
  add button in the next grid slot; else hide it (42 = grid cap). Positional field layout is the
  on-disk overlay-element record shape.
- `UCXiTongXianShiAdd` (UCXiTongXianShi.cs:185) — **appends one new element tile** (defaults
  subMode=1,main=0,sub=1). Builds+positions the tile at the current count's grid slot, marks it
  `isSelect=true`, wires its delegate, then branch: `Count<42` → show add button at next slot / else
  hide. Sets `nowCount` to the new last index, fires `delegateXiTong(3, mode, modeSub, [X,Y,color,
  font,text,nowCount])` (element-selected event), then deselects every OTHER tile in a loop +
  invalidates. Note method NAME collides with the type name (C# allows it; a Python port must rename).
- `UCXiTongXianShiSelect` (UCXiTongXianShi.cs:221) — programmatic selection by index. Sets
  `nowCount=count`; loop sets `isSelect` true only on the matching index (else false) + invalidates
  each; branch: `nowCount>=0` → fire `delegateXiTong(3,...)` with the selected tile's
  [X,Y,color,font,text,nowCount]. Two branches: per-tile match, and the ≥0 emit guard.
- `XiTongXianShiSub` (UCXiTongXianShi.cs:246) — **the child→container event router** (`delegateXiTongSub`
  target). `cmd`: **[GOD]** (dispatch hub) —
  - `case 0`: no-op.
  - `case 1` (a tile was clicked/selected): loop marks the sender tile `isSelect=true` + records its
    index in `nowCount`, all others false, invalidates each; then fires `delegateXiTong(3, mode,
    modeSub, [X,Y,color,font,text,nowCount])` up to the owner.
  - `case 2` (a tile requested delete): loop finds+removes+disposes the sender from array; second loop
    re-flows ALL remaining tiles into grid positions and resets `nowCount=-1` when index 0; repositions
    +shows the add button; fires `delegateXiTong(2)` (refresh).
  - `case 3` (a tile edited its own format/text): passthrough → `delegateXiTong(4, info, data, data1)`.
  Driving var: `cmd`. 8 branches counted (4 cases + inner loops/conditionals).
- `UCXiTongXianShiTimer` (UCXiTongXianShi.cs:309) — per-refresh tick: loops all tiles calling
  `UCXiTongXianShiSubTimer()` so each re-renders its live metric; branch: `isFanLcd` → calls the
  overload `UCXiTongXianShiSubTimer(fanLcdVal.ToString(), "RPM")` to inject the fan RPM value/unit
  instead. Per-tile `try/catch` swallows. (This is the live-update driver; per-tick → DEBUG in a port.)
- `buttonOnOff_Set` (UCXiTongXianShi.cs:331) — sets `buttonOn=bl` and swaps the toggle's background
  image; branch: `bl` → `P滑动开` (on) / else `P滑动关` (off).
- `buttonOnOff_Click` (UCXiTongXianShi.cs:344) — flips `buttonOn`, re-applies via `buttonOnOff_Set`,
  fires `delegateXiTong(0, buttonOn)` (master overlay enable/disable). Branch: the if/else flip.
- `buttonAdd_Click` (UCXiTongXianShi.cs:358) — fires `delegateXiTong(1)` (request-add; owner opens the
  Add popup). No branches.
- `SetXiTongNowSub` (UCXiTongXianShi.cs:363) — writes `myModeSub=sub` and `myText=str` onto the
  currently-selected tile (used when a format switch resolves a sub-mode + label); branch:
  `nowCount != -1` guard + swallow-all `catch`.
- `Dispose` (UCXiTongXianShi.cs:409) — standard WinForms dispose; branch: `disposing && components != null`.
- `InitializeComponent` (UCXiTongXianShiColor.cs:539) — designer plumbing: constructs the editor's controls — the X and Y position boxes, two labels (font name and rounded font size), the R/G/B boxes, the eleven fixed swatch buttons buttonC1..buttonC11, the eleven recent-colour buttons button1..button11, the eyedropper button and the font button, plus the child pickers `UCColorB` and `UCColorC` — then suspends layout. The property/geometry assignments continue past line 689, which was outside the read range.

---

## UCXiTongXianShiColor.cs — property inspector (24 methods)

- `ClearButtonBouns` (UCXiTongXianShiColor.cs:107) — resets the flat-border color to transparent on all
  24 swatch/preset/util buttons (button1-11, buttonC1-11, buttonGetColor, buttonText). No branches.
  (Pure repetition; a port would loop a button list.) **[COPY-PASTE]**
- `UCXiTongXianShiColor` ctor (UCXiTongXianShiColor.cs:176) — `InitializeComponent`, `ClearButtonBouns`,
  wires `ucColorB1.delegateUCColor=UCColorBDelegate` (hue/brightness wheel) and
  `ucColorC1.delegateUCColor=UCColorCDelegate` (color square). No branches.
- `ChangedTextBoxXY` (UCXiTongXianShiColor.cs:152) — sets X/Y textboxes from ints while
  gating `isTextXYEnabled=false` around the writes so the `textBoxXY_TextChanged` handler doesn't
  re-fire (reentrancy guard). No branches.
- `UCXiTongXianShiColorSet` (UCXiTongXianShiColor.cs:160) — **loads a selected element's properties into
  the inspector**. Clears `myColorChange`, sets XY via `ChangedTextBoxXY(array[0],array[1])`, sets color
  via `ChangedTextBoxAndUCColorC((Color)array[2])`, sets `myFont=(Font)array[3]`, writes the font
  name→label1 and rounded size→label2. Fixed positional array (same [X,Y,color,font] shape the container
  emits in cmd 3). No branches.
- `UCXiTongXianShiBackupColor` (UCXiTongXianShiColor.cs:172) — copies each MRU swatch's `BackColor` into
  its own `MouseOver`/`MouseDown` flat colors (so hover doesn't change the visible swatch). 11 buttons,
  no branches. **[COPY-PASTE]**
- `UCXiTongXianShiBackupColorInit` (UCXiTongXianShiColor.cs:198) — **reads the persisted MRU swatch file**.
  Opens `myColorName` (`FileStream OpenOrCreate`), `BinaryReader`; branch: **first byte must == 220 (0xDC
  magic)** → reads 11×(int R,int G,int B) into button1-11 BackColors; `try/catch` swallows short/corrupt
  files. Always closes/disposes reader+stream, then `UCXiTongXianShiBackupColor()`. The 0xDC magic ties
  this to the DC file family.
- `UCXiTongXianShiBackupColorSave` (UCXiTongXianShiColor.cs:230) — **pushes the current RGB onto the MRU
  stack and rewrites the file**. Branch: only if `myColorChange`. Shifts button11←button10←…←button2←
  button1, sets button1 from the textbox RGB, then writes byte 220 + 11×(int R,int G,int B) via
  `BinaryWriter`, flush/close/dispose, then `UCXiTongXianShiBackupColor()`. Mirror of the Init reader.
- `UCXiTongXianShiBackupFont` (UCXiTongXianShiColor.cs:290) — assigns `array[0..10]` fonts onto
  button1-11's `.Font`, disposing the previous font each time unless it's null/`SystemFonts.DefaultFont`.
  11 repeated blocks; the 11 dispose-guards are the "branches". **[COPY-PASTE]** (loop candidate).
- `UCColorBDelegate` (UCXiTongXianShiColor.cs:382) — callback from the hue/brightness wheel (`UCColorB`);
  branch: `cmd==1` → `ChangedTextBoxAndUCColorC(R,G,B)` (updates textboxes AND the color square) and sets
  `myColorChange=true`.
- `UCColorCDelegate` (UCXiTongXianShiColor.cs:391) — callback from the color square (`UCColorC`); branch:
  `cmd==1` → `ChangedTextBoxOnly(R,G,B)` (updates textboxes but NOT the square, since the square is the
  source) and sets `myColorChange=true`.
- `ChangedTextBoxOnly` (UCXiTongXianShiColor.cs:400) — writes R/G/B textboxes under the
  `isTextEnabled=false` reentrancy guard, then fires `ucdelegateColor(2, R, G, B)` (color-changed up to
  owner). Does NOT touch the color square. No branches.
- `ChangedTextBoxAndUCColorC(int,int,int)` (UCXiTongXianShiColor.cs:410) — same as above PLUS
  `ucColorC1.SetUCColorC(R,G,B)` to sync the square. Fires `ucdelegateColor(2,...)`. No branches.
- `ChangedTextBoxAndUCColorC(Color)` (UCXiTongXianShiColor.cs:421) — overload taking a `Color`; identical
  body using color.R/G/B. No branches. (This pair + `ChangedTextBoxOnly` are three near-identical
  textbox-writers differing only in whether they sync the square and int-vs-Color input.) **[COPY-PASTE]**
- `ChangedTextBoxAndUCColorCWB` (UCXiTongXianShiColor.cs:432) — thin public wrapper that just calls
  `ChangedTextBoxAndUCColorC(color)` (the "WB" = external write-back entry point). No branches.
- `ColorKeyPress` (UCXiTongXianShiColor.cs:437) — keypress filter on the RGB/XY textboxes; branch:
  non-digit and non-backspace → `e.Handled=true` (reject). Digits-only input guard.
- `ColorTextChanged` (UCXiTongXianShiColor.cs:445) — R/G/B textbox change handler. Branch: gated on
  `isTextEnabled`. If the edited box's value >255 → clamp to "255" and return. Else reads R,G,B (empty→0),
  syncs the square via `ucColorC1.SetUCColorC`, fires `ucdelegateColor(2,R,G,B)`, sets `myColorChange=true`.
  Driving vars: `isTextEnabled`, the >255 clamp, per-box empty→0 ternaries (5 branches).
- `textBoxXY_TextChanged` (UCXiTongXianShiColor.cs:466) — X/Y textbox change handler; branch: gated on
  `isTextXYEnabled`; reads X,Y (empty→0) and fires `ucdelegateColor(3, x, y)` (position-changed; note
  cmd 3 vs color's cmd 2). 3 branches (guard + two empty→0 ternaries).
- `buttonC_Click` (UCXiTongXianShiColor.cs:476) — preset-swatch click (buttonC1-11): loads that button's
  `BackColor` into the inspector via `ChangedTextBoxAndUCColorC(color)`. No branches.
- `button_Click` (UCXiTongXianShiColor.cs:482) — MRU-swatch click (button1-11): identical body to
  `buttonC_Click`. Two handlers, same behavior, different button group. **[COPY-PASTE]**
- `buttonText_Click` (UCXiTongXianShiColor.cs:488) — opens a `FontDialog` seeded with `myFont`; branch:
  `DialogResult == OK (1)` → dispose old font, adopt `val.Font`, update label1(name)/label2(rounded size),
  fire `ucdelegateColor(4, 0,0,0, myFont)` (font-changed). Always disposes the dialog.
- `buttonGetColor_Click` (UCXiTongXianShiColor.cs:510) — starts the eyedropper; branch: only if
  `!isGetRGB` → set `isGetRGB=true`, `myColorChange=true`, swap the button image to `P取色`, fire
  `ucdelegateColor(1)` (enter pick mode).
- `TimerGetRGB` (UCXiTongXianShiColor.cs:521) — eyedropper tick; branch: if `isGetRGB` reads
  `Cursor.Position` and `Console.WriteLine`s it. **Stub/incomplete** — logs the cursor coords but never
  samples a pixel or updates color; the actual pick is unimplemented in this build. Flag as undetermined
  behavior for a port.
- `Dispose` (UCXiTongXianShiColor.cs:530) — standard dispose; branch `disposing && components != null`.
- `InitializeComponent` (UCXiTongXianShiColor.cs:532) — designer boilerplate (~600 lines). Builds all
  textboxes (X/Y at top; R/G/B at y≈304, MaxLength 3, digit filter), 11 preset swatches buttonC1-11
  (fixed rainbow palette, 14×14 at y=354), 11 MRU swatches button1-11 (14×14 at y=333, default Silver),
  the font button, the eyedropper `buttonGetColor`, and the `UCColorB`/`UCColorC` wheel controls; wires
  every Click/TextChanged/KeyPress. No logic branches. Preset palette RGBs are the source-of-truth swatch
  colors (buttonC1=224,32,32 … buttonC11=black).

---

## UCXiTongXianShiAdd.cs — "add element" owner-drawn popup (12 methods)

- `UCXiTongXianShiAdd` ctor (UCXiTongXianShiAdd.cs:84) — `InitializeComponent`, zeroes flat borders on
  the 4 category buttons, caches the checkbox bitmaps (`P点选框`/`P点选框A` normal/hover), scrollbar
  thumb (`P滚动条按钮`), and mask overlay (`P01增加内容遮罩`); sets `StringFormat` (vertical-center,
  left-align), computes initial scrollbar `imageY`, wires `MouseWheel`. No branches.
- `buttonTimer_Click` (UCXiTongXianShiAdd.cs:144) — fires `delegateAdd(32)` (add a Time element) and hides
  the popup. No branches.
- `buttonWeek_Click` (UCXiTongXianShiAdd.cs:150) — fires `delegateAdd(48)` (add Weekday) + hide. No branches.
- `buttonDate_Click` (UCXiTongXianShiAdd.cs:156) — fires `delegateAdd(64)` (add Date) + hide. No branches.
- `buttonText_Click` (UCXiTongXianShiAdd.cs:162) — fires `delegateAdd(80)` (add free Text) + hide. No
  branches. (These four are a copy-paste quartet; the int arg 32/48/64/80 is the element-type code — a
  multiple of 16, matching the container's `delegateAdd(16, main, sub)` metric-add convention below.) **[COPY-PASTE]**
- `OnPaint` (UCXiTongXianShiAdd.cs:174) — **[GOD]** the entire owner-drawn catalog render. Iterates
  `Form1.ucSystemInfoOptions1.configArrayList` (each metric group = 5 rows of 20px). Per group: picks the
  brush color from `arrayList[0]` via a 0-6 `switch` (0=ZDY/custom,1=CPU,2=GPU,3=MEM,4=HDD,5=NET,6=FAN,
  default=ZDY); left-aligned draws the group label + 4 sub-metric name columns (array indices 1,2,5,8,11)
  and right-aligned draws the live values from the group's `UCSystemInfoOptionsOne.label1..label4`; then
  for each of 4 "+" add-rows draws `bitmapAdda` (hover) if the mouse (`myX/myY`) is over the "+" hit box at
  x∈(199,199+w), else `bitmapAdd`. Branch: `isFanLcd` appends a synthetic "LC5 / FAN / <rpm>RPM" group with
  one "+" row. Always finishes by drawing the scrollbar thumb at `imageY` and the `imageBk` mask over the
  top. Whole loop in `try/catch`. Driving vars: `configArrayList`, `arrayList[0]` (color switch),
  `myX/myY` (hover), `offsetY` (scroll), `isFanLcd`. 14 branches (color switch + 4 hover if/else + fan).
  Row geometry (`67 + i*5*20 + k*20 + offsetY`, "+" at x=199) is the hit-test source of truth reused below.
- `FrmMain_MouseWheel` (UCXiTongXianShiAdd.cs:298) — scroll via wheel. Computes total content height
  `Count*5*20` (+40 if `isFanLcd`), converts `-e.Delta*200/height` into a thumb delta, forces |delta|≥1
  in the wheel direction, adds to `imageY`, clamps `imageY` to [top, bottom] track bounds, then derives
  `offsetY` (content scroll offset) proportionally, invalidates. 5 branches (fan, min-step sign, 2 clamps).
- `UCXiTongXianShiAdd_MouseDown` (UCXiTongXianShiAdd.cs:323) — **[GOD]** two-zone mouse handler. Branch on
  `e.X`: **(a) 199<X<215** = the "+" column → `isMouseDownButton=true`, record myX/myY, then a per-group
  loop hit-tests the 4 "+" rows (`90+i*5*20+k*20+offsetY`) setting `nowCoutMain`=group,`nowCoutSub`=1..4 on
  first hit (break); if `isFanLcd`, also tests the synthetic fan row → main=10000,sub=1; invalidates the
  scrollbar strip. **(b) X>215** = scrollbar track → `isMouseDown=true`, sets `imageY=e.Y` clamped to
  track, recomputes `offsetY`, invalidates. 11 branches (zone split + 4 row hit-tests + fan + 2 clamps).
  Records the *press* target so MouseUp can confirm a click landed on the same row.
- `UCXiTongXianShiAdd_MouseMove` (UCXiTongXianShiAdd.cs:396) — branch: if `isMouseDown` (dragging the
  thumb) → update `imageY` clamped, recompute `offsetY`, invalidate; else if `!isMouseDownButton` → update
  hover `myX/myY` and invalidate just the "+" strip (drives the hover highlight in OnPaint). 5 branches
  (drag guard, 2 clamps, fan, hover else).
- `UCXiTongXianShiAdd_MouseUp` (UCXiTongXianShiAdd.cs:426) — **[GOD]** click-commit + drag-end. If
  `isMouseDownButton`: re-runs the SAME 4-row (+fan) hit-test into local num/num2, then branch **only if
  the release row == the press row (`nowCoutMain`/`nowCoutSub`) and main≥0** → fire `delegateAdd(16,
  nowCoutMain, nowCoutSub)` (add THIS metric's sub-field) and hide the popup. If `isMouseDown`: final
  thumb/offset clamp recompute. Always invalidate + clear both `isMouseDown`/`isMouseDownButton`. 12
  branches (4 hit-tests + fan + press==release confirm + drag clamps). The press/release match is the
  debounce that prevents accidental adds on drag. **[COPY-PASTE]** (hit-test loop duplicated verbatim from
  MouseDown).
- `Dispose` (UCXiTongXianShiAdd.cs:503) — standard dispose; branch `disposing && components != null`.
- `InitializeComponent` (UCXiTongXianShiAdd.cs:478) — designer boilerplate: 4 category buttons
  (Timer/Week/Date/Text, 46×24 across the top, images `P增加时间/星期/日期/文本`), background
  `P01增加内容`, size 230×430, DoubleBuffered, wires MouseDown/Move/Up. No logic branches.

---

## UCXiTongXianShiTable.cs — format-mode switch strip (12 methods)

This control shows exactly ONE widget at a time (unit toggle / 12-24H / date-order / text) selected by
which `UCXiTongXianShiTableN` initializer the owner calls. `myMode`=element type, `myModeSub`=current
format sub-value.

- `UCXiTongXianShiTable` ctor (UCXiTongXianShiTable.cs:29) — just `InitializeComponent()`. No branches.
- `UCXiTongXianShiTable0` (UCXiTongXianShiTable.cs:34) — **unit on/off toggle mode**. Stores mode/sub,
  hides textBox1/button1/button3, shows button0; branch: `myModeSub==0` → image `P单位开关` (off) / else
  `P单位开关a` (on). (Unit shown vs hidden for a metric.)
- `UCXiTongXianShiTable1` (UCXiTongXianShiTable.cs:52) — **12/24-hour toggle mode**. Shows button1 only;
  branch: `myModeSub==1` → `P12H` / else `P24H`.
- `UCXiTongXianShiTable2` (UCXiTongXianShiTable.cs:70) — **no-control mode**: hides all four widgets
  (element has no format option, e.g. weekday). No branches.
- `UCXiTongXianShiTable3` (UCXiTongXianShiTable.cs:80) — **date-order mode**. Shows button3; branch chain
  on `myModeSub`: 1→`PYMD`, 2→`PDMY`, 3→`PMD`, else→`PDM` (date-format ordering image). 3 branches.
- `UCXiTongXianShiTable4` (UCXiTongXianShiTable.cs:106) — **free-text mode**: hides buttons, shows
  textBox1. No branches.
- `button0_Click` (UCXiTongXianShiTable.cs:116) — toggles unit sub between 0↔1, re-applies via
  `UCXiTongXianShiTable0`, fires `delegateXiTongTable(myMode, myModeSub)`. Branch: the 0/1 flip.
- `button1_Click` (UCXiTongXianShiTable.cs:130) — toggles 12/24H sub between 1↔2, re-applies via
  `UCXiTongXianShiTable1`, fires the delegate. Branch: 1/2 flip. (Note sub values 1/2, mapped to
  12H/24H images where Table1 tests `==1`.)
- `button3_Click` (UCXiTongXianShiTable.cs:144) — cycles date sub 1→2→3→4→1, re-applies via
  `UCXiTongXianShiTable3`, fires the delegate. 3 branches (the cycle chain). (button0/1/3 clicks are a
  copy-paste family: mutate sub, re-init, emit.) **[COPY-PASTE]**
- `textBox1_TextChanged` (UCXiTongXianShiTable.cs:166) — free-text edit: fires
  `delegateXiTongTable(myMode, myModeSub, textBox1.Text)` (the third arg carries the literal text). No
  branches.
- `Dispose` (UCXiTongXianShiTable.cs:171) — standard dispose; branch `disposing && components != null`.
- `InitializeComponent` (UCXiTongXianShiTable.cs:180) — designer boilerplate: button0 (unit, 70×24),
  textBox1 (200×22, MaxLength 100), button1 (54×22), button3 (54×22), all initially `Visible=false`,
  background `P01模块设置`, size 230×54; wires clicks + textchanged. No logic branches.

---

## Cross-file notes

- **Delegate `cmd` code map is the wire contract between these controls and `Form1`.** Container up-events:
  0=on/off(+bool), 1=request-add, 2=deleted/refresh, 3=element-selected(+mode,modeSub,[X,Y,color,font,
  text,index]), 4=passthrough. Inspector up-events (`ucdelegateColor`): 1=enter-eyedropper, 2=color(R,G,B),
  3=position(x,y), 4=font(+Font). Add popup (`delegateAdd`): 16=add-metric(main,sub), 32/48/64/80=add
  Time/Week/Date/Text. Table (`delegateXiTongTable`): (mode, sub[, text]).
- **The overlay-element on-disk record is a positional 9-field `ArrayList`** (mode, modeSub, X, Y,
  mainCount, subCount, Color, Font, text) — see `UCXiTongXianShiSet_UCXiTongXianShiSubArray:114-123`. X/Y
  are clamped to ≤1920 on load.
- **0xDC (=220) magic byte** gates the MRU-swatch file (Color.cs:198 read / :240 write), same family
  marker as the DC theme/config files.
- `UpdateUCXiTongXianShiAdd` (UCXiTongXianShiAdd.cs:126) — re-lays the popup's five element buttons into the compact five-across strip used when the icon/link button is available: makes `buttonLink` visible, sets the button lefts to 13, 55, 97, 139 and 181 with `buttonLink` top 31, and sets all four of Timer/Week/Date/Text (and by the chained assignment `buttonLink`'s value 36) to width 36 — narrower than the designer's 46 so five fit in the 230-wide popup. No branches.
- `buttonLink_Click` (UCXiTongXianShiAdd.cs:168) — the icon/link element button: raises `delegateAdd` with element-kind code 96 and immediately hides the popup. No branches.
