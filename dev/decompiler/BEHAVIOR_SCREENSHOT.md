# Behavioral annotation — FormScreenshot.cs + FormScreenImage.cs

<!-- audit-state: origin=2.0.3.0 addresses=2.1.6.0 known-bad=none -->
> **Audited against TRCC 2.0.3; citations re-anchored to TRCC 2.1.6.**
> 2 method(s) documented here changed in TRCC 2.1.6 and have NOT been re-read: `FormScreenImage`, `InitializeComponent` — read those entries as TRCC 2.0.3 history.
> [`AUDIT_INDEX.md`](AUDIT_INDEX.md#provenance)
<!-- /audit-state -->

Decompiled path: `~/Downloads/TRCCCAPEN/TRCC_decompiled/TRCC.CZTV/`.
Two sibling borderless top-most preview/overlay windows. Both are draggable-by-body
frameless forms whose ONLY job is to float a preview image over the desktop and
call back to their owner via a delegate on a user gesture. Neither talks to the
device wire — they are pure UI shells. `FormScreenshot` = a 320×240 region-selector
rectangle; `FormScreenImage` = an 820×420 image-preview popup with a power/close button.

Middle-mouse-drag (`e.Button == 1048576` = `MouseButtons.Middle`) moves the window in
both files — a decompiler quirk of the same hand-rolled drag pattern.

## FormScreenshot.cs — 9 dark methods (all covered)

- `FormScreenshot` ctor (FormScreenshot.cs:30) — constructor; calls `InitializeComponent()` only. No branches.
- `OnPaint` (FormScreenshot.cs:35) — overrides paint: calls base `OnPaint`, then draws a light-gray (200,200,200) 5px-wide rectangle border around the full client area (`Width-1 × Height-1`), disposes the pen. This is the region-selector outline. No branches.
- `FormScreenshot_FormClosing` (FormScreenshot.cs:46) — `FormClosing` handler; unconditionally sets `e.Cancel = true` so the form can NEVER be closed (only hidden). No branches.
- `FormScreenshot_DoubleClick` (FormScreenshot.cs:51) — double-click handler; `Hide()`s the form, then fires `ucdelegateForm?.Invoke(0, 0, 0)` — mode 0 = cancel/dismiss the screenshot region. Null-conditional guards the delegate. No decision branches.
- `FormScreenshot_MouseDown` (FormScreenshot.cs:57) — begins a drag; key branch: `e.Button == 1048576` (Middle) → records `_mousePoint = (-e.X, -e.Y)` (negative cursor offset within window) and sets `isMouseDown = true`. Any other button → no-op.
- `FormScreenshot_MouseMove` (FormScreenshot.cs:70) — drag-move; key branch: `isMouseDown` true → new window `Location = Control.MousePosition` offset by `_mousePoint` (keeps grab point under cursor). False → no-op.
- `FormScreenshot_MouseUp` (FormScreenshot.cs:80) — ends drag + reports position; branches: `e.Button == 1048576` (Middle) → if `!_mousePoint.IsEmpty` fire `ucdelegateForm?.Invoke(1, Left, Top)` (mode 1 = commit new top-left coords to owner), then always clear `isMouseDown = false`. Non-middle button → no-op.
- `Dispose` (FormScreenshot.cs:94) — standard WinForms dispose; branch: `disposing && components != null` → dispose components; always calls base `Dispose(disposing)`.
- `InitializeComponent` (FormScreenshot.cs:103) — designer setup: 320×240 client, borderless (`FormBorderStyle=0`), `TopMost=true`, `DoubleBuffered`, `BackColor`/`TransparencyKey = ARGB(1,2,3)` (that near-black color renders transparent → only the painted border shows), AutoScaleMode=DPI(3). Wires all 5 event handlers (FormClosing/DoubleClick/MouseDown/MouseMove/MouseUp). Also note the `CreateParams` property override (FormScreenshot.cs:20) ORs `ExStyle |= 0x2000000` = `WS_EX_COMPOSITED` (flicker-free double buffering). No branches.

## FormScreenImage.cs — 8 dark methods (all covered)

- `FormScreenImage` ctor (FormScreenImage.cs:26) — constructor; calls `InitializeComponent()` only. No branches. Note field `ucScreenImage1` (a `UCScreenImage`, declared but not attached here) + delegate `ucScreenImage`.
- `ResetFormScreenImage` (FormScreenImage.cs:32) — resizes the form to its current `BackgroundImage` dimensions (`Width/Height = BackgroundImage.Width/Height`), then repositions the power button to `Left = Width - 44` (pins it 44px from the right edge). Used after the preview image is swapped to a device-specific resolution. No branches (will NRE if `BackgroundImage` is null — no guard).
- `FormScreenImage_MouseDown` (FormScreenImage.cs:39) — begins drag; branches: `e.Button == 1048576` (Middle) → compute `num = -e.X`; **nested branch** `sender == null` → `num -= 180` (an extra 180px horizontal grab-offset fudge when invoked programmatically with a null sender — likely the widescreen/half-panel case); store `_mousePoint = (num, -e.Y)`, set `isMouseDown = true`. Non-middle → no-op.
- `FormScreenImage_MouseMove` (FormScreenImage.cs:56) — drag-move; identical shape to FormScreenshot: `isMouseDown` true → move window to `Control.MousePosition` offset by `_mousePoint`. **[COPY-PASTE]** of `FormScreenshot_MouseMove`.
- `FormScreenImage_MouseUp` (FormScreenImage.cs:66) — ends drag; branch: `e.Button == 1048576` (Middle) → set `isMouseDown = false`, then an EMPTY an empty guard body when `_mousePoint` is unset (dead branch) block (dead/vestigial — no body, no else; the FormScreenshot sibling's mode-1 delegate report was stripped here). Non-middle → no-op.
- `buttonPower_Click` (FormScreenImage.cs:79) — power/close button handler; `Hide()`s the form then fires `ucScreenImage?.Invoke(0)` (mode 0 = notify owner the preview popup was dismissed). Null-conditional guards the delegate. No branches.
- `Dispose` (FormScreenImage.cs:99) — standard WinForms dispose; branch: `disposing && components != null` → dispose; always base `Dispose(disposing)`. **[COPY-PASTE]** identical to FormScreenshot.Dispose.
- `InitializeComponent` (FormScreenImage.cs:93) — designer setup: builds `buttonPower` (40×40, transparent, flat, background = `Resources.Alogout默认` "logout default" glyph, initial Location (776,2)), wires its `Click`. Form: 820×420 client, borderless, `TopMost=true`, `DoubleBuffered`, `BackColor=White`, `BackgroundImage = Resources.P0预览弹窗800X360` ("P0 preview-popup 800×360"), `StartPosition=CenterScreen(1)`, AutoScaleMode=DPI(3). Wires MouseDown/MouseMove/MouseUp (no FormClosing/DoubleClick handlers here, unlike the sibling). No branches.

## Cross-file notes

- Middle-mouse-button drag idiom (`_mousePoint = -cursor`, move to `MousePosition + _mousePoint`) is duplicated verbatim across the two forms' MouseDown/MouseMove/MouseUp trios. The only real behavioral divergence: FormScreenshot reports its new position on MouseUp (mode 1); FormScreenImage's MouseUp is inert (empty `if`), and FormScreenImage's MouseDown adds the `sender==null → -180` offset.
- Both forms hide-not-close and delegate mode 0 on dismissal (DoubleClick vs buttonPower_Click).
