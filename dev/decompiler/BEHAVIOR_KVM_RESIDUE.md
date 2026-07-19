# Behavioral annotation — KVM/DC dark residue (TRCC 2.1.6)

Final residue pass. Every method below was flagged DARK by
`dev/decompiler/audit_coverage.py --dark <file>`; all others in these files are
documented elsewhere. Each entry is cited `File.cs:LINE`. Re-running `--dark`
after this pass prints nothing for all three files.

---

## FormKVMALED6.cs — 30 methods

### Window-drag mouse relays (delegateForm passthrough)
Both forward the raw mouse event to the host form's `delegateForm` handler by
command id; the host implements frameless-window dragging. Sibling
`FormKVMALED6_MouseDown` (L275, id 241) is already documented.

- `FormKVMALED6_MouseMove` (FormKVMALED6.cs:280) — relays mouse-move to host via `delegateForm?.Invoke(242, null, e)` for window drag tracking; no local state.
- `FormKVMALED6_MouseUp` (FormKVMALED6.cs:285) — relays mouse-up to host via `delegateForm?.Invoke(243, null, e)` to end window drag; no local state.

### Helmet-channel toggles (buttonTK1–TK4) **[COPY-PASTE]**
Four byte-identical clones of `buttonTK0_Click` (L318), differing only by the
channel index and the highlight resource. Each toggles one lighting channel on
the "helmet" (头盔) selector, refreshes the aggregate-all (`buttonDGJH`, 灯光聚合)
highlight, and re-triggers external-output mode if active.

- `buttonTK1_Click` (FormKVMALED6.cs:344) — toggles `myChannel[1]` (image `D1头盔2`/null); key branches: `myChannel[1]==1`→clear to 0+null image, else→set 1+highlight image; all 5 channels==1 → `buttonDGJH`=聚合a else 聚合; `my_6_10_Ch`→re-invoke `buttonWBSR_Click`.
- `buttonTK2_Click` (FormKVMALED6.cs:370) — same on `myChannel[2]` (`D1头盔3`); branches identical (channel-2 toggle, aggregate-all highlight, `my_6_10_Ch`→external-output re-trigger).
- `buttonTK3_Click` (FormKVMALED6.cs:396) — same on `myChannel[3]` (`D1头盔4`); branches identical.
- `buttonTK4_Click` (FormKVMALED6.cs:422) — same on `myChannel[4]` (`D1头盔5`); branches identical.

### Color-scroll delegate relays
Two of the three per-component scroll callbacks (the R sibling `ucScrollRDelegate`
L501 is documented). Each sets one RGB component from a slider value, updates its
numeric label, and re-sends the LED frame.

- `ucScrollGDelegate` (FormKVMALED6.cs:508) — `rgbG1=val`; sets `labelG.Text`; calls `SendLEDData()`; no branches.
- `ucScrollBDelegate` (FormKVMALED6.cs:515) — `rgbB1=val`; sets `labelB.Text`; calls `SendLEDData()`; no branches.

### Color-swatch presets (buttonC2–C8) **[COPY-PASTE]**
Seven clones of `buttonC1_Click` (L528). Each is a one-liner that pushes a fixed
RGB triple into `ucColor1Delegate(r, b, g)` (note the C# arg order is r,b,g), which
updates the three labels + the three A-scrolls and sends the frame. No branches.

- `buttonC2_Click` (FormKVMALED6.cs:533) — preset `ucColor1Delegate(255, 0, 110)`.
- `buttonC3_Click` (FormKVMALED6.cs:538) — preset `ucColor1Delegate(255, 0, 255)`.
- `buttonC4_Click` (FormKVMALED6.cs:543) — preset `ucColor1Delegate(0, 0, 255)`.
- `buttonC5_Click` (FormKVMALED6.cs:548) — preset `ucColor1Delegate(0, 255, 255)`.
- `buttonC6_Click` (FormKVMALED6.cs:553) — preset `ucColor1Delegate(0, 255, 91)`.
- `buttonC7_Click` (FormKVMALED6.cs:558) — preset `ucColor1Delegate(214, 255, 0)`.
- `buttonC8_Click` (FormKVMALED6.cs:563) — preset `ucColor1Delegate(255, 255, 255)` (white).

### Effect-mode selectors (button3–12) **[COPY-PASTE]**
Ten clones of `button2_Click` (L945, mode 0). Each sets `myMode` to a fixed effect
id, calls `ButtonMode_Click(myMode)` to repaint the mode-button highlight strip,
and sends the frame. The mode ids are a scrambled map (UI order ≠ protocol id).

- `button3_Click` (FormKVMALED6.cs:952) — `myMode=1`; `ButtonMode_Click`+`SendLEDData`.
- `button4_Click` (FormKVMALED6.cs:959) — `myMode=2`.
- `button5_Click` (FormKVMALED6.cs:966) — `myMode=7`.
- `button6_Click` (FormKVMALED6.cs:973) — `myMode=6`.
- `button7_Click` (FormKVMALED6.cs:980) — `myMode=5`.
- `button8_Click` (FormKVMALED6.cs:987) — `myMode=4`.
- `button9_Click` (FormKVMALED6.cs:994) — `myMode=3`.
- `button10_Click` (FormKVMALED6.cs:1001) — `myMode=10`.
- `button11_Click` (FormKVMALED6.cs:1008) — `myMode=9`.
- `button12_Click` (FormKVMALED6.cs:1015) — `myMode=8`.

### Directional-effect selectors (buttonDGX2/DGX3) **[COPY-PASTE]**
Two clones of `buttonDGX1_Click` (L1022, mode 201). Guarded so directional modes
are ignored while external-output is active.

- `buttonDGX2_Click` (FormKVMALED6.cs:1032) — key branch `!my_6_10_Ch`→`myMode=202`+`ButtonMode_Click`+`SendLEDData`; else no-op.
- `buttonDGX3_Click` (FormKVMALED6.cs:1042) — key branch `!my_6_10_Ch`→`myMode=203`+`ButtonMode_Click`+`SendLEDData`; else no-op.

### Saved-profile recall (buttonMS2–MS4) **[COPY-PASTE]**
Three clones of `buttonMS1_Click` (L1081). Each highlights its mode button via
`buttonMS_Click(n)`, loads the saved profile file `{n}proMode.dc`, and pushes it to
the device via `SendLEDMSData(n)`. No branches.

- `buttonMS2_Click` (FormKVMALED6.cs:1088) — `buttonMS_Click(2)`; `GetMSData(...+"2proMode.dc")`; `SendLEDMSData(2)`.
- `buttonMS3_Click` (FormKVMALED6.cs:1095) — `buttonMS_Click(3)`; `GetMSData(...+"3proMode.dc")`; `SendLEDMSData(3)`.
- `buttonMS4_Click` (FormKVMALED6.cs:1102) — `buttonMS_Click(4)`; `GetMSData(...+"4proMode.dc")`; `SendLEDMSData(4)`.

> Designer glue (lines 1526–2085): the `--dark` audit lists NO method in this
> range — `InitializeComponent` and its field wiring there are pure WinForms
> designer glue and are already accounted for by the coverage tool (not flagged).

---

## UCXiTongXinXi.cs — 9 methods

### Metric-page radio selectors (buttonM2–M6) **[COPY-PASTE]**
Five clones of `buttonM1_Click` (L171). Single-select radio over which system-info
page/metric is shown: sets `mIndex`, repaints the M-button highlight strip via
`ButtonM_Set(mIndex)`, and notifies the host with `delegateUCXinxi?.Invoke(1, mIndex)`
(command 1 = page-change). No branches.

- `buttonM2_Click` (UCXiTongXinXi.cs:178) — `mIndex=2`; `ButtonM_Set`; `Invoke(1,2)`.
- `buttonM3_Click` (UCXiTongXinXi.cs:185) — `mIndex=3`; `Invoke(1,3)`.
- `buttonM4_Click` (UCXiTongXinXi.cs:192) — `mIndex=4`; `Invoke(1,4)`.
- `buttonM5_Click` (UCXiTongXinXi.cs:199) — `mIndex=5`; `Invoke(1,5)`.
- `buttonM6_Click` (UCXiTongXinXi.cs:206) — `mIndex=6`; `Invoke(1,6)`.

### Metric multi-select checkboxes (button2–button5) **[COPY-PASTE]**
Four clones of `button1_Click` (L271). Each is an independent checkbox toggling one
of six metric-visibility flags `m1..m6`; recomputes `mMode` = count of selected
flags; repaints all six via `Button_Set`; notifies host with command 2 (checkbox-
change), passing slot index, `isLunbo ? -1 : mMode`, and the new bool state.

- `button2_Click` (UCXiTongXinXi.cs:286) — toggles `m2` (`m2?false:true`); key branches: recompute `mMode`=sum of m1..m6; `Button_Set(...)`; `delegateUCXinxi?.Invoke(2, 2, isLunbo?-1:mMode, m2)`.
- `button3_Click` (UCXiTongXinXi.cs:301) — toggles `m3`; same recompute; `Invoke(2, 3, isLunbo?-1:mMode, m3)`.
- `button4_Click` (UCXiTongXinXi.cs:316) — toggles `m4`; `Invoke(2, 4, isLunbo?-1:mMode, m4)`.
- `button5_Click` (UCXiTongXinXi.cs:331) — toggles `m5`; `Invoke(2, 5, isLunbo?-1:mMode, m5)`.

> `isLunbo` (轮播 = carousel/slideshow) short-circuits the reported mode to -1,
> signalling the host "all metrics cycle" instead of a fixed selection count.

---

## UCShiJianXianShi.cs — 3 methods

### Date-format (年月日) selectors (buttonNYR2–NYR4) **[COPY-PASTE]**
Three clones of `buttonNYR1_Click` (L152). Single-select radio over the
year/month/day ordering: sets `nyrMode`, repaints via `ButtonNYR_Set(nyrMode)`,
notifies host with `delegateUCShiJian?.Invoke(1, nyrMode)` (command 1 = date-format).
No branches.

- `buttonNYR2_Click` (UCShiJianXianShi.cs:159) — `nyrMode=2`; `ButtonNYR_Set`; `Invoke(1,2)`.
- `buttonNYR3_Click` (UCShiJianXianShi.cs:166) — `nyrMode=3`; `Invoke(1,3)`.
- `buttonNYR4_Click` (UCShiJianXianShi.cs:173) — `nyrMode=4`; `Invoke(1,4)`.

---

## Consolidation targets

1. **Selector-button clone families.** Every group above (TK1–4, C2–8, button3–12,
   DGX2/3, MS2–4, M2–6, buttonNYR2–4) is one prototype cloned N times with only a
   constant swapped. In the Linux port these are table-driven: a `{widget: value}`
   registry row feeds one shared handler — no per-button method.
2. **`ucColor1Delegate` / preset swatches.** The 8 color presets are pure data
   (RGB triples) that belong in a `core/models.py` palette constant, dispatched
   through one `set_color` command — collapses buttonC1–C8 to a data table.
3. **Metric checkbox flags m1..m6 + `mMode`.** The recompute-sum-then-report idiom
   in button1–6 is a bitmask/selection-set; `isLunbo?-1:mMode` is a single policy
   branch. One `MetricSelection` object owns the set and the carousel flag.

## Undetermined

- **Effect-mode id scramble** (button3–12 → myMode 1,2,7,6,5,4,3,10,9,8): the
  UI-order→protocol-id mapping is non-monotonic; the wire meaning of each id is
  not derivable from this file (defined in `SendLEDData`/device firmware, not here).
- **`ucColor1Delegate(r, b, g)` arg order**: the delegate's second/third params are
  named `b, g` — presets pass `(R, G, B)` positionally, so the preset call sites
  read as r,b,g. Whether this is a decompiler artifact or a real R/B-then-G quirk
  needs the `ucColor1Delegate` body cross-check (out of this residue's scope).

## Confidence

High. All 42 residue methods are short UI event handlers with fully visible bodies;
behavior read directly from source, no inference. Every cited line verified against
the file. `--dark` re-run confirms 0 remaining per file (see below).
