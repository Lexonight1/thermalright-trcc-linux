# Behavioral annotation — LED info panels + keyboard-linkage + video player

<!-- audit-state: origin=2.0.3.0 addresses=2.1.6.0 known-bad=none -->
> **Audited against TRCC 2.0.3; citations re-anchored to TRCC 2.1.6.**
> Every method it documents is byte-identical in TRCC 2.1.6.
> [`AUDIT_INDEX.md`](AUDIT_INDEX.md#provenance)
<!-- /audit-state -->

Exhaustive per-method behavioral pass over 6 WinForms `UserControl`s in
`TRCC.DCUserControl`. Every DARK method reported by
`audit_coverage.py --dark` is documented below with an explicit `File.cs:LINE`
citation. These are **pure View widgets** (WinForms designer + a couple of thin
event slots) — no protocol/wire logic lives here; behaviour is label-text
mutation, background-image swaps, and delegate callbacks up to the parent form.

Source dir: `~/Downloads/TRCCCAPEN/TRCC_decompiled/TRCC.DCUserControl/`

---

## UCLEDMemoryInfo.cs — LED memory-info panel (11 gold labels + a UCComboBoxB)

A read-only display of DRAM info: 11 `Label`s in the brand gold `Color.FromArgb(180,150,83)`, 微软雅黑 9.75pt, plus one `UCComboBoxB` selector at (430,35). Panel is 506×132, transparent, double-buffered.

- `UCLEDMemoryInfo` (UCLEDMemoryInfo.cs:36) — ctor; only calls `InitializeComponent()`. No branches.
- `UCLEDMemoryInfo_Click` (UCLEDMemoryInfo.cs:41) — panel-body click handler (wired at :279 `Click += UCLEDMemoryInfo_Click`); single effect: `ucComboBoxB1.ReSetUCComboBoxMode()` — clicking anywhere on the panel resets/collapses the embedded combo box (dismiss-on-outside-click pattern). No branches.
- `SetUCLEDMemoryInfo` (UCLEDMemoryInfo.cs:46) — the one public data-sink; takes 11 strings `str1..str11` and assigns them positionally to labels: `str1→label1, str2→label2, str3→label3, str4→label4, str5→label5, str6→label6, str7→label7, str8→label8, str9→label9, str10→label10, str11→label2_1`. Note the ORDER quirk: `str11` lands in `label2_1` (the second-row right field at (311,35)), NOT a `label11`. Pure setter, no formatting, no branches — caller pre-formats every string.
- `Dispose` (UCLEDMemoryInfo.cs:61) — standard WinForms dispose; key branch: `disposing && components != null` → `components.Dispose()`, then always chains `base.Dispose(disposing)`. (`components` is always `null` here → the guarded call never fires; boilerplate.)
- `InitializeComponent` (UCLEDMemoryInfo.cs:70) — **[DESIGNER]** designer-generated layout. Constructs 11 labels + `ucComboBoxB1`, sets each label's transparent bg / gold fg / 微软雅黑 font / fixed `Point` location / 166×23 (rows) or 38×23 (the 5 small metric cells label5..label10) size / `TextAlign` MiddleLeft(16). Positions encode the layout grid: labels 1–4 stacked at x=136 (rows y=15/35/54/74), label2_1 at (311,35), the five 38-wide cells label5..label10 across y=94 (x=169/228/283/346/401/464). Adds controls, sets panel Size 506×132, wires `Click`. No runtime logic.

## UCLEDHarddiskInfo.cs — LED hard-disk-info panel (4 gold labels + a UCComboBoxC)

Sibling of the memory panel, smaller: 4 gold labels (x=170, rows y=21/43/66/88) + `UCComboBoxC` at (304,21). Same 506×132 transparent double-buffered shell. **Has NO `Set…` data-sink and NO click handler** — the parent form writes label `.Text` directly and there is no combo-reset-on-click wiring (asymmetry vs the memory panel).

- `UCLEDHarddiskInfo` (UCLEDHarddiskInfo.cs:21) — ctor; only `InitializeComponent()`. No branches.
- `Dispose` (UCLEDHarddiskInfo.cs:26) — identical boilerplate to the memory panel; branch `disposing && components != null` → `components.Dispose()`; always `base.Dispose(disposing)`.
- `InitializeComponent` (UCLEDHarddiskInfo.cs:35) — **[DESIGNER]** builds label1..label4 + `ucComboBoxC1`; gold/微软雅黑/166×23/MiddleLeft styling matching the memory panel. Control-add order is label4,label3,label1,label2 (z-order only). No runtime logic.

## UCShiPingBoFangQi — video/live-stream player toggle widget

The only widget here with real interaction. A 351×100 panel whose background is `P01播放器`, holding two buttons: `buttonOnOff` (a 50×50 on/off toggle at (0,0)) and `button1` (a 40×40 "load video" button at (149,30), initially disabled). Talks to its parent via a delegate `delegateUCShiPingBoFangQi(int cmd, object info, object data, object data1)`.

- `UCShiPingBoFangQi` (UCShiPingBoFangQi.cs:23) — ctor; `InitializeComponent()` then zeroes both buttons' `FlatAppearance.BorderColor` to fully-transparent ARGB(0,0,0,0) (borderless look). No branches.
- `buttonOnOff_Set` (UCShiPingBoFangQi.cs:30) — public state applier; sets field `buttonOn = bl`, sets `button1.Enabled = bl` (the load-video button is only usable while the player is ON), then branch on `bl`: `true` → `buttonOnOff.BackgroundImage = Resources.P功能选择a` (active art), `false` → `Resources.P功能选择` (inactive art). This is how the parent forces the toggle state programmatically.
- `buttonOnOff_Click` (UCShiPingBoFangQi.cs:44) — toggle handler. **[DECOMPILE ARTIFACT]** the flip is written as dead-code: an assignment to `buttonOn` then `if (buttonOn) buttonOn=false; else buttonOn=true;` — since `buttonOn` was just forced to `false`, the result is unconditionally `true`. Net effect: **every click turns the player ON** (it never toggles OFF via this path — OFF only comes from `buttonOnOff_Set(false)` called by the parent). Then applies `buttonOnOff_Set(buttonOn)` and fires `delegateUCShiPing?.Invoke(0, buttonOn)` — cmd 0 = "on/off changed", payload = the new bool. Null-conditional so a parent that never assigned the delegate is safe.
- `button1_Click` (UCShiPingBoFangQi.cs:59) — load-video button; fires `delegateUCShiPing?.Invoke(1)` — cmd 1 = "load/browse video", no payload. Only reachable while enabled (i.e. player ON). No branches.
- `Dispose` (UCShiPingBoFangQi.cs:64) — boilerplate; branch `disposing && components != null` → `components.Dispose()`; always `base.Dispose(disposing)`.
- `InitializeComponent` (UCShiPingBoFangQi.cs:73) — **[DESIGNER]** builds `button1` (bg `P直播视频载入`, 40×40 @ (149,30), `Enabled=false`, `Click += button1_Click`) and `buttonOnOff` (bg `P功能选择`, 50×50 @ (0,0), `Click += buttonOnOff_Click`); both flat/borderless/transparent-hover. Panel bg `P01播放器`, size 351×100. Encodes the initial state: player OFF art + load button disabled.

## UCJianPanLianDong{A,B,C} — keyboard-linkage panels **[COPY-PASTE FAMILY]**

Three near-identical designer-only widgets, all `682×84`, all transparent/double-buffered, each with a decorative `buttonOnOff` slide-toggle at (22,24) (18×36, art `P滑动开`) and a distinct background image (`P01键盘联动1/2/3`). **None of the three defines any event handler, `Set…` method, or field mutation** beyond the ctor+Dispose+designer triad — they are pure static layout. The buttons carry NO `Click +=` wiring in these files, so interaction (if any) is attached by the parent form after construction. The A/B/C variants differ ONLY in how many "row" button-triplets they carry and whether C adds a font/colour row:

- **A** = one row: `buttonYL1` (preview, `P预览动画`), `buttonXZ1` (load, `P载入动画`), `buttonWL1` (network, `P网络按钮`).
- **B** = two rows: adds `buttonYL2/buttonXZ2/buttonWL2` — the same three buttons duplicated on a lower y (18→47), i.e. two independent keyboard-linkage channels.
- **C** = one row (like A) PLUS a text-style row: `labelSize` ("9"), `labelFont` ("微软雅黑"), `labelColor` (a 24×24 white swatch), and `buttonWZZT` (font button, `P文字字体`) — so C is the "linkage with on-panel text formatting" variant.

Methods (structurally identical across the three — same ctor/Dispose boilerplate, InitializeComponent differs only in control count/coords):

- `UCJianPanLianDongA` (UCJianPanLianDongA.cs:20) / `UCJianPanLianDongB` (UCJianPanLianDongB.cs:26) / `UCJianPanLianDongC` (UCJianPanLianDongC.cs:28) — ctors; only `InitializeComponent()`. No branches.
- `Dispose` (UCJianPanLianDongA.cs:25 / UCJianPanLianDongB.cs:31 / UCJianPanLianDongC.cs:33) — byte-identical WinForms boilerplate; branch `disposing && components != null` → `components.Dispose()`; always `base.Dispose(disposing)`.
- `InitializeComponent` (UCJianPanLianDongA.cs:34 / UCJianPanLianDongB.cs:40 / UCJianPanLianDongC.cs:42) — **[DESIGNER]** builds the buttons/labels listed above, positions them, sets the `P01键盘联动{1,2,3}` background, panel size 682×84. A: 4 buttons. B: 7 buttons (the row duplicated). C: 4 buttons + 3 labels + 1 font button. No `Click` wiring, no runtime logic.

---

### FLAGS

- **[COPY-PASTE FAMILY]** `UCJianPanLianDong{A,B,C}` — same shell, same three method shapes, differ only by button-row count (A=1, B=2) and C's extra text-format row. Prime consolidation candidate: one parametrised `UCKeyboardLinkage(rows, showTextStyle)` collapses all three.
- No **[GOD]** methods — every method is short; the only large bodies are designer `InitializeComponent`s (mechanical, not god-objects).
- **[DECOMPILE ARTIFACT]** `UCShiPingBoFangQi.buttonOnOff_Click` (:44) — the toggle logic decompiles to unconditional `buttonOn=true`; verify against the live app before assuming symmetric toggling (OFF is parent-driven via `buttonOnOff_Set(false)`).

### Notes for the Linux port
- These are Windows-skin View widgets; the Linux equivalents live in `ui/gui/uc_led_*.py` / keyboard panels. The load-bearing facts to carry over are the **delegate command codes** (`UCShiPingBoFangQi`: cmd 0 = on/off toggle w/ bool payload, cmd 1 = load video) and the **data-sink contract** (`SetUCLEDMemoryInfo`'s 11 positional strings, with str11→label2_1). Layout coords/colours are cosmetic.
