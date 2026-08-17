# BEHAVIOR — Display / Panel-Editor UserControls

<!-- audit-state: origin=2.0.3.0 addresses=2.1.6.0 known-bad=none -->
> **Audited against TRCC 2.0.3; citations re-anchored to TRCC 2.1.6.**
> 4 method(s) documented here changed in TRCC 2.1.6 and have NOT been re-read: `TouPingXianShiBackgroundImageSet90du`, `UCInfoImage`, `textBoxH_TextChanged`, `textBoxW_TextChanged` — read those entries as TRCC 2.0.3 history.
> [`AUDIT_INDEX.md`](AUDIT_INDEX.md#provenance)
<!-- /audit-state -->

Per-method behavioral grind of the six editor UserControls under
`TRCC_decompiled/TRCC.DCUserControl/`. These are the **left-rail editor panels**
of the DC theme editor: each one is a thin WinForms control whose real job is to
*(a)* show a localized background image, *(b)* toggle an on/off state via a
graphical button, and *(c)* fire an `int cmd` + boxed-payload delegate up to the
parent form (`FormCZTV`) which owns all the actual model/render logic. There is
almost no business logic here — the exceptions are `UCTouPingXianShi`'s
aspect-ratio lock and `UCInfoImage`'s meter rendering.

Delegate shape is identical in all four editor panels:
`delegate(int cmd, object info=null, object data=null, object data1=null)` — a
stringly/int-typed command bus, boxed args. This is the C# anti-pattern the
Linux port replaces with typed Commands.

Every method returns `void`; "returns" below means the observable side effect.

---

## UCTouPingXianShi.cs — "投屏显示" screencast/projection region editor

Edits the X/Y position and W/H size of the screencast capture region for a
portrait panel. **The only panel with real logic**: W↔H aspect-ratio lock keyed
off eleven `isWxH` resolution flags. Delegate `delegateUCTouPing` cmd map:
`0`=on/off, `1`=X, `2`=Y, `3`=W, `4`=H, `5`=show-border toggle.

- `UCTouPingXianShi` (UCTouPingXianShi.cs:85) — ctor; calls `InitializeComponent()` then zeroes the `FlatAppearance.BorderColor` (transparent) on all 10 buttons. No branches; pure boilerplate wiring.
- `TouPingXianShiBackgroundImageSet90du` (UCTouPingXianShi.cs:90) — swaps `this.BackgroundImage` to the correct localized instruction graphic depending on panel orientation (`bl`) and UI language. Key branches: `bl` true → the "xy" (portrait/upright) image family, false → the "yx" (rotated 90°) family; nested inside each, `Form1.Language` 1..8 selects a language-suffixed resource (`xy`,`xytc`,`xyd`,`xye`,`xyf`,`xyp`,`yxr`,`yxx`) else the English (`yxen`) fallback. **[COPY-PASTE]** — the two `Form1.Language` ladders (lines 94-129 vs 131-166) are near-identical 8-way switches differing only in the resource base name; a `(orientation, language)→resource` table collapses both. Note the `bl==true` branch's cases 7/8 mistakenly reuse the `yxr`/`yxx` (rotated) resources — a transcription slip in the original.
- `buttonOnOff_Set` (UCTouPingXianShi.cs:187) — sets `buttonOn=bl` and swaps the on/off button graphic. Branch: `bl` → `P功能选择a` (active), else `P功能选择` (inactive). Pure view-state setter, no delegate fire (caller-driven, e.g. from parent restoring state). **[COPY-PASTE]** with the same-named method in UCBeiJingXianShi/UCDingYiWenBen (minus the child-enable line).
- `buttonOnOff_Click` (UCTouPingXianShi.cs:200) — toggles `buttonOn`, calls `buttonOnOff_Set`, fires `cmd 0` with the new state. Branch: reads `buttonOn` to toggle — but line 184 hard-sets `buttonOn=false` *before* the toggle, so the `buttonOn` at 185 is **always false** → result is always `true`. Dead-branch quirk: the button can only ever turn ON via click. **[COPY-PASTE]** (same dead toggle in UCBeiJingXianShi:56).
- `buttonAddX_Click` (UCTouPingXianShi.cs:215) — parses `textBoxX` to int, `++`, clamps to ≤9999, writes back, fires `cmd 1`. Branch: `num>9999→9999`. **[COPY-PASTE]** — one of 8 identical ±1 spinner handlers (AddX/SubX/AddY/SubY/AddW/SubW/AddH/SubH), differing only in target textbox, +/- direction, clamp bound, and cmd code.
- `buttonSubX_Click` (UCTouPingXianShi.cs:227) — as above, `--`, clamps ≥0, fires `cmd 1`. Branch: `num<0→0`. **[COPY-PASTE]**.
- `buttonAddY_Click` (UCTouPingXianShi.cs:239) — `textBoxY` `++`, clamp ≤9999, `cmd 2`. Branch: `num>9999→9999`. **[COPY-PASTE]**.
- `buttonSubY_Click` (UCTouPingXianShi.cs:251) — `textBoxY` `--`, clamp ≥0, `cmd 2`. Branch: `num<0→0`. **[COPY-PASTE]**.
- `buttonAddW_Click` (UCTouPingXianShi.cs:263) — `textBoxW` `++`, clamp ≤9999, `cmd 3`. Branch: `num>9999→9999`. **[COPY-PASTE]**. (W-change re-fires the aspect lock via `textBoxW_TextChanged`.)
- `buttonSubW_Click` (UCTouPingXianShi.cs:275) — `textBoxW` `--`, clamp ≥0, `cmd 3`. Branch: `num<0→0`. **[COPY-PASTE]**.
- `buttonAddH_Click` (UCTouPingXianShi.cs:287) — `textBoxH` `++`, clamp ≤9999, `cmd 4`. Branch: `num>9999→9999`. **[COPY-PASTE]**.
- `buttonSubH_Click` (UCTouPingXianShi.cs:299) — `textBoxH` `--`, clamp ≥0, `cmd 4`. Branch: `num<0→0`. **[COPY-PASTE]**.
- `buttonXSBK_Set` (UCTouPingXianShi.cs:311) — sets `myYcbk=bl` (show-border flag) and swaps the border button graphic. Branch: `bl` → `P显示边框A` (active) else `P显示边框`. View-state setter, no delegate.
- `buttonXSBK_Click` (UCTouPingXianShi.cs:324) — toggles `myYcbk`, calls `buttonXSBK_Set`, fires `cmd 5` with new state. Branch: proper toggle (`myYcbk` is inverted) — unlike `buttonOnOff_Click`, this one works correctly.
- `textBoxX_KeyPress` (UCTouPingXianShi.cs:338) — input filter: swallows any keystroke that is not a digit or backspace. Branch: `!IsNumber && != '\b'` → `e.Handled=true` (reject). Shared by all four textboxes (X/Y/W/H all wire `textBoxX_KeyPress`).
- `textBoxX_TextChanged` (UCTouPingXianShi.cs:346) — on non-empty text, fires `cmd 1` with the parsed int. Branch: `Length>0` guard (avoids parsing empty). No aspect coupling (X is position, not size).
- `textBoxY_TextChanged` (UCTouPingXianShi.cs:354) — same as X, fires `cmd 2`. Branch: `Length>0`. **[COPY-PASTE]** of `textBoxX_TextChanged`.
- `textBoxW_TextChanged` (UCTouPingXianShi.cs:344) — fires `cmd 3` (new W), then **aspect-lock**: computes H from W per the active resolution and pushes it into `textBoxH` + `cmd 4`. Key branches: `isToChangedText && Length>0` guard (prevents re-entrancy when H-change writes W back — the `isToChangedText` flag is cleared at 351 and restored at 355); `mySdbl` gates whether the lock runs at all. The resolution selector at 352: square panels (240/320/360/480) → `H=W` (1:1); `1600x720`→`W/0.45`; `854x480`→`W/0.5620608899297423`; `960x540`→`W/0.5625`; `800x480`→`W/0.6`; `1920x462`→`W/(77/320)` (=`/0.240625`); `1280x480`→`W/0.375`; else (default, e.g. 640x480)→`W/0.75`. So H = W × (panelH/panelW). **[GOD]** one nested-ternary expression encodes the entire 11-resolution aspect table inline — the single highest-value consolidation target in this file set (a `resolution→aspect_ratio` map).
- `textBoxH_TextChanged` (UCTouPingXianShi.cs:360) — mirror of W: fires `cmd 4` (new H), then computes W from H (`H × ratio`, the inverse-direction of line 352) and pushes into `textBoxW` + `cmd 3`. Same `isToChangedText`/`mySdbl` guards. Branch selector at 368 is the transpose of 352 (multiply where W divided). **[COPY-PASTE]/[GOD]** — same inline aspect table, opposite direction; the square-panel case at 368 has a latent bug (it reads `textBoxW` not `textBoxH`, so square panels echo W back to W).
- `Dispose` (UCShortcut.cs:139) — standard WinForms designer disposal override. Key branches: `disposing is true AND the components container is non-null -> disposes the container first`; `otherwise -> skipped`. Always chains to the base `ContainerControl.Dispose(disposing)` afterwards. Nothing device- or file-related is released here; the panel owns no unmanaged or USB resources.
- `InitializeComponent` (UCShortcut.cs:148) — [GOD] designer-generated layout method: constructs all six children and sets roughly a hundred properties in one flat body, ~130 lines within the range read (lines 179–268; the tail past 268 was outside the permitted range and is not documented). Builds `buttonShortcut`, `labelShorcut`, `labelVal`, `textBoxY`, `textBoxX`, `ucScrollC1` and a `ComponentResourceManager` bound to this type. Resolved values a porter needs — panel background is the embedded resource `P01快捷方式` (line 260) with layout mode 0 (tile); browse button background is `P添加链接1` (line 188) stretched (layout 3), flat style, zero border, transparent hover/press, at (13, 86) size 24×24, tab index 674 (line 194–198), click wired to `buttonShortcut_Click` (line 200). `labelShorcut` at (38, 88) size 182×27, transparent, silver text, font 微软雅黑 9pt with GraphicsUnit 3 (Point) and GDI charset byte 134 (GB2312), text alignment 16 = MiddleLeft (lines 201–210). `labelVal` at (181, 388) size 39×27, font 微软雅黑 10.5pt, silver, alignment 32 = MiddleRight, and a hardcoded initial text of `"64"` (line 220) — that literal is the slider's implied default. Both text boxes share: background RGB(67, 67, 67), no border, white text, font 微软雅黑 10.5pt, ImeMode 0, size 53×19, `MaxLength = 4`, initial text `"0"`, centre alignment (HorizontalAlignment 1), and both hook `TextBoxXY_TextChanged` plus `Shortcut_KeyPress`; `textBoxX` sits at (32, 32) tab index 689, `textBoxY` at (121, 32) tab index 690 — X is left of Y, 89 px apart (lines 222–249). `ucScrollC1` at (51, 392) size 128×20, background image pulled from the resx by the key `"ucScrollC1.BackgroundImage"` with layout 2 (stretch), tab index 691 (lines 250–257). The control itself uses AutoScaleMode 3 (Dpi), transparent back colour, and DoubleBuffered on (line 268). Z-order is set by add sequence at lines 262–267: ucScrollC1, textBoxY, textBoxX, labelVal, labelShorcut, buttonShortcut (last added is topmost). Key branches: No branches. [COPY-PASTE] the two text-box property blocks (lines 222–235 and 236–249) are identical apart from name, location and tab index — a port should build them from one factory taking (name, x, y, tabIndex).

---

## UCBeiJingXianShi.cs — "背景显示" background-source editor

Chooses the panel's background source. Delegate `delegateUCBeiJing` cmd map:
`0`=on/off, `1`=picture (`button1`), `2`=animation (`button2`, hidden),
`3`=network/cloud (`button3`, hidden), `4`=video (`button4`).

- `UCBeiJingXianShi` (UCBeiJingXianShi.cs:29) — ctor; `InitializeComponent()` then transparent border on all 5 buttons. No branches. **[COPY-PASTE]** ctor shape.
- `buttonOnOff_Set` (UCBeiJingXianShi.cs:39) — sets `buttonOn=bl`, **enables/disables the 4 source buttons** (`button1..4.Enabled=bl`), swaps the on/off graphic. Branch: `bl` → `P功能选择a` else `P功能选择`. The child-enable behavior is what distinguishes it from the TouPing/DingYiWen copies. **[COPY-PASTE]** of the graphic-swap tail.
- `buttonOnOff_Click` (UCBeiJingXianShi.cs:56) — toggle→set→fire `cmd 0`. Same **dead-toggle quirk** as UCTouPingXianShi:182 (line 58 hard-sets `false` before the `if`, so click always yields `true`). **[COPY-PASTE]**.
- `button1_Click` (UCBeiJingXianShi.cs:71) — fires `cmd 1` (picture), no payload. Trivial passthrough. **[COPY-PASTE]** — one of four one-line source dispatchers.
- `button2_Click` (UCBeiJingXianShi.cs:76) — fires `cmd 2` (animation). Button is `Visible=false` in the layout, so effectively dead in the shipped UI. **[COPY-PASTE]**.
- `button3_Click` (UCBeiJingXianShi.cs:81) — fires `cmd 3` (network/cloud). Also `Visible=false`. **[COPY-PASTE]**.
- `button4_Click` (UCBeiJingXianShi.cs:86) — fires `cmd 4` (video). **[COPY-PASTE]**.
- `Dispose` (UCBeiJingXianShi.cs:91) — designer dispose boilerplate. Branch: `disposing && components != null`. **[COPY-PASTE]**.
- `InitializeComponent` (UCBeiJingXianShi.cs:100) — designer boilerplate: 1 on/off button + 4 source buttons (`P图片`/`P动画`/`P网络`/`P视频`), `button2`+`button3` set `Visible=false`, `P01背景显示` background, size 351×100. No behavior; not ported.

---

## UCMengBanXianShi.cs — "蒙板显示" mask/overlay editor

Toggles a mask overlay and picks its source. Delegate `delegateUCMengBan` cmd
map: `0`=on/off, `1`=mask image (`button1`), `3`=network/cloud (`button3`,
hidden). Uses a **slider** on/off graphic (`P滑动开`/`P滑动关`) rather than the
`P功能选择` toggle used by BeiJing/TouPing.

- `UCMengBanXianShi` (UCMengBanXianShi.cs:25) — ctor; only `InitializeComponent()`, no border fixups. No branches.
- `ButtonOnOff_Set` (UCMengBanXianShi.cs:30) — sets `buttonOn=bl`, swaps slider graphic. Branch: `bl` → `P滑动开` (open) else `P滑动关` (closed). Note the PascalCase name (`ButtonOnOff_Set`) vs the camelCase siblings — inconsistent casing across the file set.
- `buttonOnOff_Click` (UCMengBanXianShi.cs:43) — toggle→set→fire `cmd 0`. Branch: **correct toggle** here (no pre-set-false bug) — `buttonOn` is inverted. Differs from the TouPing/BeiJing dead-toggle.
- `button1_Click` (UCMengBanXianShi.cs:57) — fires `cmd 1` (mask image). **[COPY-PASTE]**.
- `button3_Click` (UCMengBanXianShi.cs:62) — fires `cmd 3` (network). `button3` is `Visible=false`. **[COPY-PASTE]**.
- `Dispose` (UCMengBanXianShi.cs:67) — designer dispose. Branch: `disposing && components != null`. **[COPY-PASTE]**.
- `InitializeComponent` (UCMengBanXianShi.cs:76) — designer boilerplate: slider on/off + `button1` (`P蒙板` mask) + `button3` (`P网络`, hidden), `P01布局蒙板` background, size 351×100. Not ported.

---

## UCInfoImage.cs — metric ring/bar meter renderer

The **one panel that actually renders pixels** (no delegate; it is a display
widget, not an editor). Draws a horizontal bar (`bitmap = P环H1`) scaled by a
metric value plus a centered numeric string, into an off-screen `myImage` that
`OnPaint` blits. `myTextMode` selects the value domain: `1`=percent (0–100),
`2`=fan RPM (0–5000), `17`=temperature in °F (32–212, converted to °C).

- `UCInfoImage` (UCInfoImage.cs:58) — ctor; `InitializeComponent()`, configures `m_format` (LineAlignment=center, Alignment=center → text centered), sets `bitmap = P环H1` (the bar sprite). No branches.
- `SetUCState` (UCInfoImage.cs:101) — the public update entry: clamps `val` by `myTextMode`, stores `val`+`val1..3` strings, regenerates the image, invalidates. Key branches: `myTextMode==1` → clamp `[0,100]`; `==2` → clamp `[0,5000]`; `==17` → clamp `[32,212]` then convert `val=(val-32)*5/9` (°F→°C, integer math). Note mode 17 clamps in Fahrenheit first, *then* converts, so the stored `myVal` is Celsius 0–100. **[GOD]-lite** — the clamp/convert-per-mode ladder is the natural home for a `MetricDomain` value-object in the port.
- `SetTextMode` (UCInfoImage.cs:145) — one-liner: `myTextMode = mode`. No branch. (Caller sets domain before `SetUCState`.)
- `GenerateImage` (UCInfoImage.cs:150) — renders the meter into a fresh `Bitmap(Width,Height)`: if `myMode==1` and `myVal!=0`, draws the bar sprite at (35,22) with width scaled by mode — percent/temp (`myTextMode==1||17`) → `width=myVal*2` px; else (fan) → `width=myVal/25` px; height fixed 3px — then draws `val1` centered via `fontNumber`/`fontNumberBrush`. Swaps `myImage` and disposes the previous. Key branches: `myMode==1` gate (only mode 1 draws; other `myMode` values render blank), `myVal!=0` (skip bar at zero), `myTextMode∈{1,17}` bar-scale vs fan-scale. The `myVal*2` implies bar full-width at val=100 (→200px); `myVal/25` gives fan 0–5000→0–200px. `rectangleF` at line 135 is dead (overwritten at 149 before use).
- `OnPaint` (UCInfoImage.cs:182) — calls base `OnPaint`, then blits `myImage` at (0,0) if non-null. Branch: `myImage != null`. The pre-rendered-image pattern (render in `GenerateImage`, cheap blit in paint) avoids per-frame recompute.
- `Dispose` (UCInfoImage.cs:192) — designer dispose. Branch: `disposing && components != null`. **[COPY-PASTE]**.
- `InitializeComponent` (UCInfoImage.cs:201) — designer boilerplate: no child controls, `P0M1` background, DoubleBuffered, size 240×30. Not ported.

---

## UCDingYiWenBen.cs — "自定义文本" custom-text editor

Edits a free-text overlay string plus its font and color. Delegate
`delegateUCWenBen` cmd map: `0`=on/off, `1`=text string (`textBox`),
`2`=open font dialog (`buttonWZZT`), `3`=open color picker (`labelColor`). Slider
on/off graphic like MengBan. `labelFont`/`labelSize` are read-only status labels
(no handlers; parent updates their `.Text`).

- `UCDingYiWenBen` (UCDingYiWenBen.cs:31) — ctor; `InitializeComponent()` then transparent borders on the two buttons. No branches.
- `buttonOnOff_Set` (UCDingYiWenBen.cs:38) — sets `buttonOn=bl`, swaps slider graphic (`P滑动开`/`P滑动关`). Branch: `bl`. **[COPY-PASTE]** of UCMengBanXianShi.ButtonOnOff_Set.
- `buttonOnOff_Click` (UCDingYiWenBen.cs:51) — toggle→set→fire `cmd 0`. Branch: **correct toggle** (no dead-toggle bug). **[COPY-PASTE]** of the MengBan variant.
- `textBox_TextChanged` (UCDingYiWenBen.cs:65) — fires `cmd 1` with `textBox.Text` (the boxed string payload) on every keystroke. No branch/guard (unlike TouPing's `Length>0` gate — empty text is forwarded).
- `buttonWZZT_Click` (UCDingYiWenBen.cs:70) — fires `cmd 2` (parent opens font dialog). No branch. **[COPY-PASTE]** dispatcher.
- `labelColor_Click` (UCDingYiWenBen.cs:75) — fires `cmd 3` (parent opens color picker; the `labelColor` swatch doubles as the click target). No branch. **[COPY-PASTE]** dispatcher.
- `Dispose` (UCDingYiWenBen.cs:80) — designer dispose. Branch: `disposing && components != null`. **[COPY-PASTE]**.
- `InitializeComponent` (UCDingYiWenBen.cs:89) — designer boilerplate: slider on/off, multiline `textBox` (MaxLength 1000), `buttonWZZT` font button, `labelColor` swatch, `labelFont`/`labelSize` status labels, `P01自定文字` background, size 712×100. Not ported.

---

## UCScreenImageBK.cs — preview-bezel wrapper

Pure container: hosts a `UCScreenImage` (the live render preview) at a fixed
offset inside a 500×500 device-bezel background. No logic, no delegate, no
state.

- `UCScreenImageBK` (UCScreenImageBK.cs:14) — ctor; only `InitializeComponent()`. No branches.
- `Dispose` (UCScreenImageBK.cs:19) — designer dispose. Branch: `disposing && components != null`. **[COPY-PASTE]**.
- `InitializeComponent` (UCScreenImageBK.cs:28) — designer boilerplate: constructs `ucScreenImage1` (a `UCScreenImage`) at `Point(90,130)` size 320×240 with black bg, sets the `P预览320X240` bezel background (ImageLayout stretch), size 500×500. **This hard-codes the 320×240 preview geometry** — the legacy default panel size; other resolutions are handled by `UCScreenImage`/`FormCZTV` reconfiguring at runtime, not here. Not ported (Qt preview bezel is data-driven).

---

## Consolidation notes (feeds the port)

1. **Aspect-ratio table** (`UCTouPingXianShi.cs:352` + `:368`) — the inline
   11-resolution nested-ternary is the single biggest win: one
   `resolution → (panelW, panelH)` map, `H = round(W * panelH/panelW)` and its
   inverse, replaces two [GOD] expressions and kills the latent square-panel
   echo bug at :368.
2. **Editor-panel base** — `buttonOnOff_Set` + `buttonOnOff_Click` +
   `Dispose` + the `delegate(int cmd, object…)` bus recur verbatim across
   TouPing/BeiJing/MengBan/DingYiWen. One `EditorPanel` base (typed Command
   instead of `int cmd`) collapses ~4× duplication; fixes the dead-toggle bug
   (TouPing:182 / BeiJing:56) once, centrally.
3. **Spinner buttons** (`UCTouPingXianShi.cs:215–309`) — 8 near-identical
   ±1/clamp handlers → one parameterized spinner (`target, delta, min, max,
   cmd`).

## `UCShortcut.cs` — new since this doc's origin

A DCUserControl that did not exist in the release the rest of this doc was read
from. Documented here because it belongs to the same editor-panel family; its
methods follow, read against the release named in the state block above.

- `UCShortcut` (UCShortcut.cs:31) — parameterless constructor for the shortcut editor panel. Calls `InitializeComponent`, then subscribes its own `UcScrollC1_upDateUCScroll` handler onto the child `ucScrollC1` control's `upDateUCScroll` delegate field (combine, not assign — so any previously attached handler is preserved). That is the only wiring done here; every other event hookup happens inside `InitializeComponent`. No branches.
- `UcScrollC1_upDateUCScroll` (UCShortcut.cs:38) — callback fired by the embedded slider (`UCScrollC`) when the user drags it. Writes the new integer straight into `labelVal`'s text (decimal, no unit, no clamping applied here), then raises the panel's outward delegate `ucdelegateShortcut` with **mode 1** and the slider value in the `X` slot; `Y` and `addr` keep their defaults (0 and empty string). Null-conditional invoke, so an unsubscribed panel is silently inert. Key branches: `ucdelegateShortcut is null -> no notification is sent, but labelVal is still updated` (the label and the consumer can therefore disagree). EXTERNAL: UCScrollC.cs for the slider's own range/step behaviour.
- `buttonShortcut_Click` (UCShortcut.cs:44) — the browse button. **This is the file's only `*_Click` handler; there is no method literally named `button_Click`.** Opens a modal `OpenFileDialog` inside a try/finally that always disposes it, configured with title `"File"`, filter string `"File (*.exe)|*.exe|All (*.*)|*.*"` (so an executable is the intended target but any file can be chosen), `CheckFileExists = true`, and multiselect off. Key branches: `dialog result == 1 (DialogResult.OK) -> stores the FULL selected path in labelShorcut.Tag (Tag is the authoritative value; the visible Text is only the ellipsised tail from GetTailFit) and raises ucdelegateShortcut with mode 2, X = int parse of textBoxX.Text, Y = int parse of textBoxY.Text, addr = the full path`; `any other dialog result -> nothing changes, no notification`. Porting note: the X/Y conversion is an unguarded decimal parse of the two text boxes — an empty box at this moment throws, unlike `TextBoxXY_TextChanged` (line 129) which defaults empty to 0. The two paths disagree on the same inputs.
- `GetTailFit` (UCShortcut.cs:71) — pure display helper that shortens a path so it fits a label, keeping the **tail** (filename end) rather than the head, prefixed with a literal `"..."`. Available width is the label's `ClientSize.Width` minus 2 pixels, floored at 0, and text width is measured with `TextRenderer.MeasureText` in the label's own font. Uses a binary search over the tail length (midpoint biased upward by `(lo + hi + 1) / 2`) to find the longest suffix whose `"..." + suffix` still measures within the budget. Key branches: `text null or empty -> returned unchanged`; `measured width <= available width -> returned unchanged, no ellipsis added`; `otherwise -> returns "..." plus the best-fitting suffix`; `binary search converges to 0 -> returns the bare "..." with an empty suffix`. Note the ellipsis is three ASCII full stops, not U+2026.
- `ChangedText` (UCShortcut.cs:101) — public inbound setter used by the owner to push a stored shortcut path into the panel (e.g. when loading a saved config). Puts the full string in `labelShorcut.Tag` and the `GetTailFit`-shortened form in `labelShorcut.Text`, mirroring exactly what `buttonShortcut_Click` does on a successful pick. Deliberately does **not** raise `ucdelegateShortcut`, so loading a value cannot echo back to the caller. No branches.
- `ChangedTextBoxXY` (UCShortcut.cs:107) — public inbound setter for the X/Y position boxes. Clears the `isTextXYEnabled` guard flag, writes both integers as decimal text, then restores the flag. The guard exists because assigning `.Text` fires `TextChanged`, which would otherwise re-notify the owner with the value it just pushed in (feedback loop). Porting note: the flag is restored unconditionally but not via try/finally, so a throw between the two assignments would leave the panel permanently deaf to user edits. No branches.
- `ChangedScrollC1` (UCShortcut.cs:115) — public inbound setter for the slider. Calls `SetUCScrollC(val)` on the child control and independently writes the same value into `labelVal`'s text — the label is updated by the caller here rather than by the slider's own callback, so `SetUCScrollC` is assumed not to re-raise `upDateUCScroll`. No branches. EXTERNAL: UCScrollC.cs for whether `SetUCScrollC` clamps the value or fires its delegate.
- `Shortcut_KeyPress` (UCShortcut.cs:121) — shared keypress filter attached to both X and Y text boxes (wired at lines 235 and 249). Key branches: `character is a digit, or is backspace (0x08) -> allowed through`; `anything else -> event marked Handled, keystroke swallowed`. Consequence for a porter: the boxes accept unsigned decimal only — no minus sign, no paste filtering (paste bypasses KeyPress entirely), and combined with `MaxLength = 4` the reachable range is 0..9999.
- `TextBoxXY_TextChanged` (UCShortcut.cs:129) — shared change handler for both position boxes (wired at lines 234 and 248); one handler serves both, so the owner always receives the pair, never which box moved. Key branches: `isTextXYEnabled is false (a programmatic push from ChangedTextBoxXY is in progress) -> returns without notifying`; `textBoxX.Text empty -> X treated as 0, else decimal parse`; `textBoxY.Text empty -> Y treated as 0, else decimal parse`; then raises `ucdelegateShortcut` with **mode 3**, the resolved X and Y, and default empty `addr`. Fires on every keystroke — there is no debounce or commit-on-focus-loss.
