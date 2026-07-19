# BEHAVIOR — Display / Panel-Editor UserControls (TRCC 2.1.6)

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

- `UCTouPingXianShi` (UCTouPingXianShi.cs:75) — ctor; calls `InitializeComponent()` then zeroes the `FlatAppearance.BorderColor` (transparent) on all 10 buttons. No branches; pure boilerplate wiring.
- `TouPingXianShiBackgroundImageSet90du` (UCTouPingXianShi.cs:90) — swaps `this.BackgroundImage` to the correct localized instruction graphic depending on panel orientation (`bl`) and UI language. Key branches: `bl` true → the "xy" (portrait/upright) image family, false → the "yx" (rotated 90°) family; nested inside each, `Form1.Language` 1..8 selects a language-suffixed resource (`xy`,`xytc`,`xyd`,`xye`,`xyf`,`xyp`,`yxr`,`yxx`) else the English (`yxen`) fallback. **[COPY-PASTE]** — the two `Form1.Language` ladders (lines 94-129 vs 131-166) are near-identical 8-way switches differing only in the resource base name; a `(orientation, language)→resource` table collapses both. Note the `bl==true` branch's cases 7/8 mistakenly reuse the `yxr`/`yxx` (rotated) resources — a transcription slip in the original.
- `buttonOnOff_Set` (UCTouPingXianShi.cs:169) — sets `buttonOn=bl` and swaps the on/off button graphic. Branch: `bl` → `P功能选择a` (active), else `P功能选择` (inactive). Pure view-state setter, no delegate fire (caller-driven, e.g. from parent restoring state). **[COPY-PASTE]** with the same-named method in UCBeiJingXianShi/UCDingYiWenBen (minus the child-enable line).
- `buttonOnOff_Click` (UCTouPingXianShi.cs:182) — toggles `buttonOn`, calls `buttonOnOff_Set`, fires `cmd 0` with the new state. Branch: reads `buttonOn` to toggle — but line 184 hard-sets `buttonOn=false` *before* the toggle, so the `if(buttonOn)` at 185 is **always false** → result is always `true`. Dead-branch quirk: the button can only ever turn ON via click. **[COPY-PASTE]** (same dead toggle in UCBeiJingXianShi:56).
- `buttonAddX_Click` (UCTouPingXianShi.cs:197) — parses `textBoxX` to int, `++`, clamps to ≤9999, writes back, fires `cmd 1`. Branch: `num>9999→9999`. **[COPY-PASTE]** — one of 8 identical ±1 spinner handlers (AddX/SubX/AddY/SubY/AddW/SubW/AddH/SubH), differing only in target textbox, +/- direction, clamp bound, and cmd code.
- `buttonSubX_Click` (UCTouPingXianShi.cs:209) — as above, `--`, clamps ≥0, fires `cmd 1`. Branch: `num<0→0`. **[COPY-PASTE]**.
- `buttonAddY_Click` (UCTouPingXianShi.cs:221) — `textBoxY` `++`, clamp ≤9999, `cmd 2`. Branch: `num>9999→9999`. **[COPY-PASTE]**.
- `buttonSubY_Click` (UCTouPingXianShi.cs:233) — `textBoxY` `--`, clamp ≥0, `cmd 2`. Branch: `num<0→0`. **[COPY-PASTE]**.
- `buttonAddW_Click` (UCTouPingXianShi.cs:245) — `textBoxW` `++`, clamp ≤9999, `cmd 3`. Branch: `num>9999→9999`. **[COPY-PASTE]**. (W-change re-fires the aspect lock via `textBoxW_TextChanged`.)
- `buttonSubW_Click` (UCTouPingXianShi.cs:257) — `textBoxW` `--`, clamp ≥0, `cmd 3`. Branch: `num<0→0`. **[COPY-PASTE]**.
- `buttonAddH_Click` (UCTouPingXianShi.cs:269) — `textBoxH` `++`, clamp ≤9999, `cmd 4`. Branch: `num>9999→9999`. **[COPY-PASTE]**.
- `buttonSubH_Click` (UCTouPingXianShi.cs:281) — `textBoxH` `--`, clamp ≥0, `cmd 4`. Branch: `num<0→0`. **[COPY-PASTE]**.
- `buttonXSBK_Set` (UCTouPingXianShi.cs:293) — sets `myYcbk=bl` (show-border flag) and swaps the border button graphic. Branch: `bl` → `P显示边框A` (active) else `P显示边框`. View-state setter, no delegate.
- `buttonXSBK_Click` (UCTouPingXianShi.cs:306) — toggles `myYcbk`, calls `buttonXSBK_Set`, fires `cmd 5` with new state. Branch: proper toggle (`if(myYcbk) false else true`) — unlike `buttonOnOff_Click`, this one works correctly.
- `textBoxX_KeyPress` (UCTouPingXianShi.cs:320) — input filter: swallows any keystroke that is not a digit or backspace. Branch: `!IsNumber && != '\b'` → `e.Handled=true` (reject). Shared by all four textboxes (X/Y/W/H all wire `textBoxX_KeyPress`).
- `textBoxX_TextChanged` (UCTouPingXianShi.cs:328) — on non-empty text, fires `cmd 1` with the parsed int. Branch: `Length>0` guard (avoids parsing empty). No aspect coupling (X is position, not size).
- `textBoxY_TextChanged` (UCTouPingXianShi.cs:336) — same as X, fires `cmd 2`. Branch: `Length>0`. **[COPY-PASTE]** of `textBoxX_TextChanged`.
- `textBoxW_TextChanged` (UCTouPingXianShi.cs:344) — fires `cmd 3` (new W), then **aspect-lock**: computes H from W per the active resolution and pushes it into `textBoxH` + `cmd 4`. Key branches: `isToChangedText && Length>0` guard (prevents re-entrancy when H-change writes W back — the `isToChangedText` flag is cleared at 351 and restored at 355); `mySdbl` gates whether the lock runs at all. The resolution selector at 352: square panels (240/320/360/480) → `H=W` (1:1); `1600x720`→`W/0.45`; `854x480`→`W/0.5620608899297423`; `960x540`→`W/0.5625`; `800x480`→`W/0.6`; `1920x462`→`W/(77/320)` (=`/0.240625`); `1280x480`→`W/0.375`; else (default, e.g. 640x480)→`W/0.75`. So H = W × (panelH/panelW). **[GOD]** one nested-ternary expression encodes the entire 11-resolution aspect table inline — the single highest-value consolidation target in this file set (a `resolution→aspect_ratio` map).
- `textBoxH_TextChanged` (UCTouPingXianShi.cs:360) — mirror of W: fires `cmd 4` (new H), then computes W from H (`H × ratio`, the inverse-direction of line 352) and pushes into `textBoxW` + `cmd 3`. Same `isToChangedText`/`mySdbl` guards. Branch selector at 368 is the transpose of 352 (multiply where W divided). **[COPY-PASTE]/[GOD]** — same inline aspect table, opposite direction; the square-panel case at 368 has a latent bug (it reads `textBoxW` not `textBoxH`, so square panels echo W back to W).
- `Dispose` (UCTouPingXianShi.cs:376) — standard WinForms designer dispose; disposes `components` if non-null then base. Branch: `disposing && components != null`. Boilerplate. **[COPY-PASTE]** across all six files.
- `InitializeComponent` (UCTouPingXianShi.cs:385) — WinForms designer boilerplate: constructs 14 controls (2 image-buttons, 4 textboxes, 8 spinner buttons), sets flat-transparent styling, absolute `Point`/`Size` layout, wires the event handlers, sets the default `P01投屏显示xy` background, control size 351×100. No behavior; pure layout. Not ported (designer artifact).

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
- `buttonOnOff_Click` (UCMengBanXianShi.cs:43) — toggle→set→fire `cmd 0`. Branch: **correct toggle** here (no pre-set-false bug) — `if(buttonOn) false else true`. Differs from the TouPing/BeiJing dead-toggle.
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
- `SetUCState` (UCInfoImage.cs:80) — the public update entry: clamps `val` by `myTextMode`, stores `val`+`val1..3` strings, regenerates the image, invalidates. Key branches: `myTextMode==1` → clamp `[0,100]`; `==2` → clamp `[0,5000]`; `==17` → clamp `[32,212]` then convert `val=(val-32)*5/9` (°F→°C, integer math). Note mode 17 clamps in Fahrenheit first, *then* converts, so the stored `myVal` is Celsius 0–100. **[GOD]-lite** — the clamp/convert-per-mode ladder is the natural home for a `MetricDomain` value-object in the port.
- `SetTextMode` (UCInfoImage.cs:124) — one-liner: `myTextMode = mode`. No branch. (Caller sets domain before `SetUCState`.)
- `GenerateImage` (UCInfoImage.cs:129) — renders the meter into a fresh `Bitmap(Width,Height)`: if `myMode==1` and `myVal!=0`, draws the bar sprite at (35,22) with width scaled by mode — percent/temp (`myTextMode==1||17`) → `width=myVal*2` px; else (fan) → `width=myVal/25` px; height fixed 3px — then draws `val1` centered via `fontNumber`/`fontNumberBrush`. Swaps `myImage` and disposes the previous. Key branches: `myMode==1` gate (only mode 1 draws; other `myMode` values render blank), `myVal!=0` (skip bar at zero), `myTextMode∈{1,17}` bar-scale vs fan-scale. The `myVal*2` implies bar full-width at val=100 (→200px); `myVal/25` gives fan 0–5000→0–200px. `rectangleF` at line 135 is dead (overwritten at 149 before use).
- `OnPaint` (UCInfoImage.cs:161) — calls base `OnPaint`, then blits `myImage` at (0,0) if non-null. Branch: `myImage != null`. The pre-rendered-image pattern (render in `GenerateImage`, cheap blit in paint) avoids per-frame recompute.
- `Dispose` (UCInfoImage.cs:171) — designer dispose. Branch: `disposing && components != null`. **[COPY-PASTE]**.
- `InitializeComponent` (UCInfoImage.cs:180) — designer boilerplate: no child controls, `P0M1` background, DoubleBuffered, size 240×30. Not ported.

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
3. **Spinner buttons** (`UCTouPingXianShi.cs:197–291`) — 8 near-identical
   ±1/clamp handlers → one parameterized spinner (`target, delta, min, max,
   cmd`).
