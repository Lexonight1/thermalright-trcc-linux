# BEHAVIOR_THEME.md — per-method behavioral annotation (theme/mask/crop/preview UCs)

Source: `~/Downloads/TRCCCAPEN/TRCC_decompiled/TRCC.DCUserControl/`
Scope: **every method** in the 5 UserControls below (94 methods total).
Companion audits (crop thresholds, GenerateImage/Rotate primitives): `AUDIT_THEME_MASK.md`,
`AUDIT_LCD_PIPELINE.md`. `GenerateImage`/`RotateImg*` get a one-line entry here + a pointer,
per the mandate (not re-expanded).

Driving variables named throughout: **`directionB`/`angle`** (0/90/180/270 device orientation),
**`isFanZhuan`** (portrait/flipped mount → transposed canvas, set true iff angle∈{90,270}),
**`is<W>x<H>`** resolution flags, **`isBiliPingmu`** (half-scale "ratio screen" preview),
**per-res aspect thresholds** T (canvas H/W, hard-coded literals), **`isTPJCW`** (width-fit vs height-fit),
**`myMode`** (category), **`isDownLoad`** (re-entry guard), **`isLunbo`**/`lunBoArray` (carousel).

FLAGS: **[COPY-PASTE]** = block duplicated near-verbatim across methods/files; **[GOD]** = one method
doing many jobs across all resolutions.

---

## 1. UCThemeLocal.cs (23 methods) — local theme grid browser

- `UCThemeLocal` ctor (UCThemeLocal.cs:88) — builds the control, zeroes 5 button borders, allocs `arrayThemeLocal`, centers `m_format`, loads scrollbar knob `P滚动条按钮` + panel bg `P0本地主题`, hooks `MouseWheel`; no branches.
- `UCThemeLocalRemove` (UCThemeLocal.cs:112) — disposes every cached thumbnail Bitmap (slot [0]) then clears the list; loop only.
- `FrmMain_MouseWheel` (UCThemeLocal.cs:125) — scrolls the grid; key branches: `isOffsetY==false`→no-op; `|delta·200/allImageY|<1`→snap to ±1; clamps `imageY` to knob half-height band; recomputes `offsetY` only if `allImageY > Height−54`. **[COPY-PASTE]** (identical in Web/Mask). [GOD-ish scroll math]
- `Button_Set` (UCThemeLocal.cs:151) — sets `myMode`, resets all 3 category tabs to `P主题分类选择`, highlights one to `…选择0`; key branches: `mode 0→buttonAll`, `1→buttonDefault`, `2→buttonUser`.
- `Reset_Button` (UCThemeLocal.cs:171) — fires `delegateLocal(myMode)` to re-list current category; no branch.
- `buttonAll_Click` (UCThemeLocal.cs:176) — `myMode=0`, `Button_Set`, invoke delegate(0). No guard (unlike Web).
- `buttonDefault_Click` (UCThemeLocal.cs:183) — `myMode=1`, Button_Set, invoke(1).
- `buttonUser_Click` (UCThemeLocal.cs:190) — `myMode=2`, Button_Set, invoke(2).
- `ByteToBitmap` (UCThemeLocal.cs:197) — `MemoryStream`→`Image.FromStream`→cast Bitmap; no branch.
- `BitmapToByte` (UCThemeLocal.cs:209) — saves Bitmap to `MemoryStream` as **PNG**, returns `GetBuffer()`; no branch.
- `SetThemeLocal(array, GifDirectory, fileName, USB_PACKED_Head)` (UCThemeLocal.cs:220) — **[GOD]** the load waterfall: disposes/clears, then per folder picks thumbnail in strict order: `Theme.png`→`00.png`→**DC binary tail**; key branches: `Theme.png exists`→round-trip PNG; else `00.png exists`→round-trip PNG; else open `fileName`, read byte0, `==USB_PACKED_Head`→`[int32 num][num×int32 skipped][int32 count][count bytes]`→`ByteToBitmap` (catch→null). Then fit into 120 (`width>height`→W=120 else H=120), grid origin `30+135·(j%5)`, `60+150·(j/5)`, record `[bmp,x,y,w,h,name]`. Sets `isOffsetY` iff `allImageY>Height−54`. DC-header read side is the ONLY binary parse in these 4 files. **[COPY-PASTE]** fit block shared with Web/Mask.
- `OnPaint` (UCThemeLocal.cs:333) — draws each thumbnail centered in its 120-box + name 20px below, then a badge overlay layer, then panel bg `imageBk` on top, then knob if scrollable; key branches: `isLunbo`→draw carousel badges `P轮播1..6` (index = position in `lunBoArray`, else `P轮播选框`); `myMode<2`→draw close-X `P关闭按钮2` on items **index≥5 only** (defaults are protected); `myMode>=2`→close-X on **all** (user themes all deletable). `isOffsetY`→draw scrollbar knob at 702. [GOD paint].
- `UCThemeLocal_MouseDown` (UCThemeLocal.cs:418) — **[GOD]** click dispatch; first `textBoxTimer_Leave`; key branches: `e.X>692`→scrollbar drag (set `isMouseDown`, recompute offset, else cancel); else hit-test each cell: if hit and `!isLunbo` and in the top-right X zone → `myMode>=2`→`delegate(32,i,1)` delete; `i>=5`→`delegate(32,i,0)` delete + purge from carousel (`delegate(48)`); if `isLunbo` and in bottom-right zone → toggle membership in `lunBoArray` (add if <6, else remove+shift) + `delegate(48)`; plain click (`!isLunbo`) → `delegate(16,i, myMode>=2?1:0)` load/apply.
- `UCThemeLocal_MouseMove` (UCThemeLocal.cs:522) — scrollbar drag only; key branches: `isMouseDown`→recompute `imageY`/`offsetY`, clamp; `allImageY<=Height−54`→cancel drag. **[COPY-PASTE]** with Web/Mask.
- `UCThemeLocal_MouseUp` (UCThemeLocal.cs:547) — `isMouseDown=false`; no branch.
- `textBoxTimer_TextChanged` (UCThemeLocal.cs:552) — parses carousel interval; key branches: empty→return; `>=3`→`delegate(48)`; `<3`→clamp to 3; parse throw→swallow.
- `textBoxTimer_KeyPress` (UCThemeLocal.cs:575) — digits + backspace only; `!IsNumber && !=\b`→`Handled=true`.
- `textBoxTimer_Leave` (UCThemeLocal.cs:583) — `myLunBoTimer<=3`→force textbox text "3".
- `buttonLunbo_Set(bl)` (UCThemeLocal.cs:591) — sets `isLunbo`, swaps button art `P主题轮播a`(on)/`P主题轮播`(off).
- `buttonLunbo_Click` (UCThemeLocal.cs:604) — toggles `isLunbo`, `buttonLunbo_Set`, invalidate, `delegate(48)`.
- `buttonThemeOut_Click` (UCThemeLocal.cs:619) — **empty stub** (export-all button is `Visible=false`).
- `Dispose(disposing)` (UCThemeLocal.cs:623) — dispose components; `disposing && components!=null`.
- `InitializeComponent` (UCThemeLocal.cs:632) — designer layout; builds 6 controls; note `buttonDefault`/`buttonUser`/`buttonThemeOut` `Visible=false`, size 732×652, wires MouseDown/Move/Up; no logic branches.

**Delegate cmd codes (Local):** bare `myMode`=re-list; `16`=load theme (+data `myMode>=2?1:0`); `32`=delete; `48`=carousel-state changed.

---

## 2. UCThemeWeb.cs (20 methods) — cloud BACKGROUND grid browser

Same 120/5-col engine as Local; images arrive pre-decoded (no file parse); **7 category buttons** (All+1..6);
`isDownLoad` guards concurrent fetches; `myDirectionB` triggers re-list on orientation change.

- `UCThemeWeb` ctor (UCThemeWeb.cs:80) — like Local but bg = `p0云端背景`, zeroes 7 button borders, `isDownLoad=true` initial.
- `UCThemeWebRemove` (UCThemeWeb.cs:106) — dispose+clear cached bitmaps. **[COPY-PASTE]**.
- `CheakDirectionB(direction)` (UCThemeWeb.cs:119) — orientation change gate: `myDirectionB != direction`→store + `Display_Button()` (re-list for new direction). **[COPY-PASTE]** with Mask.
- `FrmMain_MouseWheel` (UCThemeWeb.cs:128) — identical scroll math to Local. **[COPY-PASTE]**.
- `Button_Set(mode)` (UCThemeWeb.cs:154) — resets 7 tabs, highlights one; key branches: `mode 0..6`→buttonAll/button1..6.
- `Display_Button` (UCThemeWeb.cs:190) — `delegateWeb(myMode)` then `isDownLoad=false` (release guard).
- `buttonAll_Click` (UCThemeWeb.cs:196) — key branch: `!isDownLoad`→set guard, `myMode=0`, Button_Set, Display_Button (guarded, unlike Local).
- `button1_Click` (UCThemeWeb.cs:207) — same guarded pattern, `myMode=1`. **[COPY-PASTE]** ×6.
- `button2_Click` (UCThemeWeb.cs:218) — `myMode=2`.
- `button3_Click` (UCThemeWeb.cs:229) — `myMode=3`.
- `button4_Click` (UCThemeWeb.cs:240) — `myMode=4`.
- `button5_Click` (UCThemeWeb.cs:251) — `myMode=5`.
- `button6_Click` (UCThemeWeb.cs:262) — `myMode=6`.
- `SetThemeWeb(imageArray, nameArray)` (UCThemeWeb.cs:273) — dispose/clear, then per pre-decoded bitmap: fit into 120 (`width>height`), grid origin `30+135·(j%5)`/`60+150·(j/5)`, record `[bmp,x,y,w,h,name]`, set `isOffsetY`. NO file/URL work. **[COPY-PASTE]** fit block.
- `OnPaint` (UCThemeWeb.cs:339) — draw thumbnails+names, then panel bg `imageBk`, then knob if scrollable. No badge/close layer (simpler than Local). **[COPY-PASTE]** base loop.
- `UCThemeWeb_MouseDown` (UCThemeWeb.cs:364) — key branches: `e.X>692`→scrollbar drag; else `isDownLoad`→return; else set guard, hit-test cells, on hit→`delegateWeb(16, name)` apply, then release guard.
- `UCThemeWeb_MouseMove` (UCThemeWeb.cs:413) — scrollbar drag only. **[COPY-PASTE]**.
- `UCThemeWeb_MouseUp` (UCThemeWeb.cs:438) — `isMouseDown=false`.
- `Dispose` (UCThemeWeb.cs:443) — dispose components.
- `InitializeComponent` (UCThemeWeb.cs:452) — designer; 7 buttons all visible, bg `p0云端背景`, 732×652.

---

## 3. UCThemeMask.cs (12 methods) — cloud MASK grid browser ("online themes"=masks)

Same engine as Web but **no category buttons** (`myMode` always 0). Panel labelled `p0云端主题` ("cloud theme")
yet payload is masks; delegate = `delegateMask`.

- `UCThemeMask` ctor (UCThemeMask.cs:66) — like Web but bg `p0云端主题`, no button-border loop, `isDownLoad=true`.
- `UCThemeMaskRemove` (UCThemeMask.cs:85) — dispose+clear. **[COPY-PASTE]**.
- `CheakDirectionB(direction)` (UCThemeMask.cs:98) — orientation-change re-list. **[COPY-PASTE]** with Web.
- `FrmMain_MouseWheel` (UCThemeMask.cs:107) — identical scroll math. **[COPY-PASTE]**.
- `Display_Button` (UCThemeMask.cs:133) — `delegateMask(myMode)` + `isDownLoad=false`.
- `SetThemeMask(imageArray, nameArray)` (UCThemeMask.cs:139) — dispose/clear, fit into 120, grid origin, record `[bmp,x,y,w,h,name]`, `isOffsetY`. Byte-identical to `SetThemeWeb`. **[COPY-PASTE]**.
- `OnPaint` (UCThemeMask.cs:205) — thumbnails+names, panel bg, knob. Identical to Web's. **[COPY-PASTE]**.
- `UCThemeMask_MouseDown` (UCThemeMask.cs:230) — `e.X>692`→scroll; else guard, hit-test, `delegateMask(16, name)` apply, release. **[COPY-PASTE]** of Web with `delegateMask`.
- `UCThemeMask_MouseMove` (UCThemeMask.cs:278) — scrollbar drag. **[COPY-PASTE]**.
- `UCThemeMask_MouseUp` (UCThemeMask.cs:303) — `isMouseDown=false`.
- `Dispose` (UCThemeMask.cs:308) — dispose components.
- `InitializeComponent` (UCThemeMask.cs:317) — designer; NO buttons, bg `p0云端主题`, 732×652.

---

## 4. UCImageCut.cs (16 methods) — image crop/fit editor

Returns final canvas-sized bitmap via `ucImageCut(Image)`. Fit = letterbox-contain + center, never crop-to-fill.
`isTPJCW` records fitted axis. Zoom "bili" around 248px slider center (0.03/px, reciprocal on shrink, track 12..484).
Full per-res threshold table + preview rects: `AUDIT_THEME_MASK.md §4`.

- `UCImageCut` ctor (UCImageCut.cs:124) — loads slider knob `P滑动条按钮`, sets slider center `bitmapCX=248`, `bitmapCY=569`; no branch.
- `SetImage(image)` (UCImageCut.cs:134) — **[GOD] + [COPY-PASTE]** the initial fit; swaps in `imageAll`, then a giant per-res chain allocating `imageSub` at canvas size (transposed if `isFanZhuan`) and choosing width-fit vs height-fit by the hard-coded aspect threshold T; key drivers: `is<W>x<H>` selects the block, `isFanZhuan`→transposed canvas + transposed threshold, `(double)image.Height > image.Width*T`→height-fit (`isTPJCW=false`) else width-fit (`isTPJCW=true`); center via `xVal/yVal += (canvas−scaled)/2`. Thresholds are literals (0.45, 0.6, 0.56206, 0.5625, 77/320, 0.375, 0.75); squares use plain `Height>Width`; default no-flag = 320×240 / 240×320. Builds `imageSub0` = source@`(wVal,hVal)`, blits onto `imageSub` at `(xVal,yVal)`.
- `OnPaint` (UCImageCut.cs:626) — draws editor chrome + `imageSub` at a fixed on-screen rect per res; key branches: per `is<W>x<H>` a literal `DrawImage` rect (320×320→(90,90); 360→(70,70); 240→(130,130); 480→(10,10); widescreen landscape vs flipped rects differ), default 320×240→(90,130,320,240). **[COPY-PASTE]** per-res literal blocks.
- `buttonTPJCW_Click` (UCImageCut.cs:726) — **[GOD] + [COPY-PASTE]** force WIDTH-fit: `isTPJCW=true`, per res `wVal=canvasW; hVal=imageAll.Height*canvasW/imageAll.Width; yVal+=(canvasH−hVal)/2` (transposed dims if `isFanZhuan`); 21 branches = one per resolution flag. Reused by rotate + zoom-reset.
- `buttonTPJCH_Click` (UCImageCut.cs:994) — **[GOD] + [COPY-PASTE]** force HEIGHT-fit: `isTPJCW=false`, per res `hVal=canvasH; wVal=imageAll.Width*canvasH/imageAll.Height; xVal+=(canvasW−wVal)/2`. Mirror of TPJCW, 21 branches.
- `buttonTPJCOK_Click` (UCImageCut.cs:1262) — returns final `imageSub` via `ucImageCut?.Invoke(imageSub)`; no branch.
- `buttonClose_Click` (UCImageCut.cs:1267) — cancel: `ucImageCut?.Invoke(null)`; no branch.
- `RotateImg(img, angle)` (UCImageCut.cs:1272) — bounds-expanded rotation, HighQualityBicubic + HighQuality smoothing; no branch. (Editor's own copy; see `AUDIT_THEME_MASK.md`.)
- `button1_Click` (UCImageCut.cs:1309) — rotate SOURCE 90° then re-apply current fit; key branch: `isTPJCW`→`buttonTPJCW_Click` else `buttonTPJCH_Click`.
- `ImageBiliBianhuan` (UCImageCut.cs:1325) — **[GOD] + [COPY-PASTE]** LIVE zoom (during drag): `bitmapCX>248`→`bili=1+(cx−248)*0.03` else reciprocal; recenter `xVal/yVal -= (new−old)/2`; realloc `imageSub` per res (same giant `is<W>x<H>`/`isFanZhuan` chain as SetImage); blit `imageSub0` scaled to `(wVal*bili, hVal*bili)`. Does NOT rebuild `imageSub0` (fast path).
- `ImageBiliBianhuanUp` (UCImageCut.cs:1493) — **[GOD] + [COPY-PASTE]** COMMIT zoom (on release): same bili math + same per-res realloc chain, but **rebuilds `imageSub0`** at zoomed size (`new Bitmap(num,num2)` from `imageAll`) then blits — restores full quality. 22-branch res chain duplicated verbatim from ImageBiliBianhuan.
- `UCImageCut_MouseDown` (UCImageCut.cs:1671) — key branch: `e.Y<=546 || e.Y>=592`→pan mode (`isMouseDown`, record xPos/yPos); else slider mode (`isMouseDownGdt`, `bitmapCX=e.X` clamped 12..484, `ImageBiliBianhuan`).
- `UCImageCut_MouseMove` (UCImageCut.cs:1694) — **[GOD] + [COPY-PASTE]** key branches: `isMouseDown`→pan with per-res GAIN (`is1600x720||is1920x462`→×4; `is1280x480`→÷0.375; `640/800/854/960`→×2; else ×1), then realloc `imageSub` per res (third verbatim copy of the chain) + blit; `isMouseDownGdt`→update `bitmapCX` clamped + live `ImageBiliBianhuan`.
- `UCImageCut_MouseUp` (UCImageCut.cs:1887) — key branch: `isMouseDownGdt`→`ImageBiliBianhuanUp()` (commit zoom); clears both drag flags.
- `Dispose` (UCImageCut.cs:1898) — dispose components.
- `InitializeComponent` (UCImageCut.cs:1907) — designer; chrome bg `P0图片裁减320240`; wires OK/Close/TPJCW/TPJCH/button1 + MouseDown/Move/Up.

**Consolidation note:** the per-res `is<W>x<H>`/`isFanZhuan` canvas-alloc chain appears **≥5×** here
(SetImage, TPJCW, TPJCH, ImageBiliBianhuan, ImageBiliBianhuanUp, MouseMove) — the single biggest
copy-paste target in the file.

---

## 5. UCScreenImage.cs (23 methods) — the live device-preview / compose surface

Owns the preview canvas that doubles as the wire-frame source. `directionB`/`isFanZhuan` drive
canvas orientation. `GenerateImage` composes; `SetMyUCScreenImage` sizes the control + bg resource;
overlay text is drawn UPRIGHT at raw coords (never separately rotated). Deep dive: `AUDIT_LCD_PIPELINE.md`.

- `UCScreenImage` ctor (UCScreenImage.cs:96) — center `m_format`, cache `gF=CreateGraphics()` (for MeasureString), default bg `P320240`, round masks `bitmapBGK2`=480 ring, `bitmapBGK3`=360 ring; no branch.
- `SetMyUCScreenImage(angle)` (UCScreenImage.cs:109) — **[GOD] + [COPY-PASTE]** sizes/positions the control + picks `bitmapBGK1` fallback-bg resource per res; key branches: squares (240/320/480) fixed Left/Top/W/H + own bg (`P240240`/`P320320`); 360→fixed; every widescreen splits `angle∈{0,180}` (landscape rect) vs {90,270} (transposed rect), each further split by `isBiliPingmu` (small "ratio" rect) vs full; default 320×240 `switch(angle)` 0/180→320×240+`P320240`, 90/270→240×320+`P240320`. Then `directionB=angle`, `isFanZhuan = angle∉{0,180}`, dispose `myImage`, invalidate. 35 branches.
- `SetDrawMengBan(bl)` (UCScreenImage.cs:407) — set `isDrawMbImage` (mask-overlay draw flag); no branch.
- `SetDrawXiTong(bl)` (UCScreenImage.cs:412) — set `isDrawXitongXinxi` (metric-text draw flag); no branch.
- `SetDrawBkImage(bl)` (UCScreenImage.cs:417) — set `isDrawBkImage` (background draw flag); no branch.
- `SetUCState(angle, array, hn)` (UCScreenImage.cs:422) — public compose entry: stores `arrayXianshi=array`, calls `GenerateImage`, invalidate, returns `myImage`; try/catch swallow.
- `GenerateImage(angle, array, hn)` (UCScreenImage.cs:634) — **[GOD]** the compose pipeline (see AUDIT_LCD_PIPELINE): black-fill canvas per res (transposed at 90/270 for widescreen), then **bg WIDTH-TEST** `bitmapBGK.Width <= canvas.Width+2`→draw theme bg native else draw black `bitmapBGK1`; mask overlay if `isDrawMbImage`; **灵动岛 Dynamic-Island only if `is1600x720`** (`myLddVal`∈{1,2,3} × `directionB`→per-angle resource); metric text UPRIGHT per element if `isDrawXitongXinxi` (skips `i==hn`, the dragged one). Key drivers: `is<W>x<H>`, `angle`, `myLddVal`, `directionB`.
- `OnPaint` (UCScreenImage.cs:927) — blits `myImage` to screen per res; key branches: `is480x480`+`myDevicePingMu==3`→overlay round mask `bitmapBGK2`; `is360x360`+`myDevicePingMu==100`→`bitmapBGK3`; widescreen split `isBiliPingmu`/`isFanZhuan`→literal half/quarter DrawImage dims; **`is640x480`→half-size** `(W/2,H/2)`; else 1:1. **[COPY-PASTE]** per-res dims.
- `SetTextPos(n, xNew, yNew, bili=1)` (UCScreenImage.cs:1134) — moves a dragged element; key branches: `n==100`→move mask (`XvalMB/YvalMB`, clamped to `Width*bili`/`Height*bili` minus half-size); else move `arrayXianshi[n]` element (clamp 0..Width*bili), try/catch. Updates `xPos/yPos`.
- `UCScreenImage_MouseDoubleClick` (UCScreenImage.cs:1194) — key branch: `isBiliPingmu`→`ucScreenImage?.Invoke(0)` (exit ratio-screen).
- `UCScreenImage_MouseDown` (UCScreenImage.cs:1202) — begin drag; key branches: non-left button→return; `isBiliPingmu`→return (no drag); `is1600x720||is640x480||is1920x462`→coords ×2 (half-scale preview); hit-test metric elements (`isDrawXitongXinxi`)→`isMouseDown=i`, `ucScreenImage(1,i)`; else if mask hit→`isMouseDownMB`.
- `UCScreenImage_MouseMove` (UCScreenImage.cs:1257) — drag; key branches: out-of-bounds→return; `is1600x720||is640x480||is1920x462`→coords ×2 + `SetTextPos(...,2f)`; else `SetTextPos(...,1f)`; routes to element (`isMouseDown>-1`) or mask (`isMouseDownMB`).
- `UCScreenImage_MouseUp` (UCScreenImage.cs:1286) — clear `isMouseDown=-1`, `isMouseDownMB=false`; no branch.
- `SetDMouseDown(n)` (UCScreenImage.cs:1292) — keyboard-focus a selected element; key branch: `n!=-1`→clear `isDMouseDownMB`; set `isDMouseDown=n`, `Focus()`.
- `UCScreenImage_KeyDown` (UCScreenImage.cs:1302) — WASD nudge of selected element/mask; key branches: W/S/A/D→`(dx,dy)` ±1 (±10 with Shift); non-WASD→return; `isBiliPingmu`→return; half-scale panels→`bili=2`; `isDMouseDown>-1`→nudge element clamped (try/catch); `isDMouseDownMB`→nudge mask clamped. 20 branches.
- `Dispose` (UCScreenImage.cs:1403) — dispose components.
- `InitializeComponent` (UCScreenImage.cs:1412) — designer; wires MouseDown/Move/Up/DoubleClick/KeyDown.

### Rotation/crop primitives (one-line each; full treatment in AUDIT_LCD_PIPELINE.md)
- `RotateImg(img, angle)` (UCScreenImage.cs:436) — bounds-expanded high-quality rotate, no edge fill.
- `RotateImgHei(img, angle)` (UCScreenImage.cs:473) — RotateImg + on `is480x480` blacks the 1px ring; key branch: `is480x480`.
- `RotateImgBu(img, angle)` (UCScreenImage.cs:520) — RotateImg + on `is480x480` edge-replicates the middle band (i=160..320); key branch: `is480x480`.
- `RotateImg2(b, angle)` (UCScreenImage.cs:567) — trig-sized rotate by `360−angle` (mirror direction); no branch.
- `CropImage(originImage, region)` (UCScreenImage.cs:596) — blit sub-rectangle into a region-sized bitmap; no branch.
- `DrawPic(b, angle)` (UCScreenImage.cs:606) — RotateImg2 onto black canvas, then CropImage back to original size (centered); no branch.

**Consolidation note:** `RotateImg`/`RotateImgHei`/`RotateImgBu` are pixel-identical except the trailing
`is480x480` edge-fill loop — one primitive + an enum edge-fill mode. `SetMyUCScreenImage`/`GenerateImage`/
`OnPaint` share the same per-res + `isFanZhuan`/`angle` canvas-dimension logic in three different shapes.

---

## Top 5 consolidation targets

1. **The per-res `is<W>x<H>` + `isFanZhuan` canvas-dimension chain** — appears ≥5× in UCImageCut
   (SetImage/TPJCW/TPJCH/ImageBiliBianhuan/ImageBiliBianhuanUp/MouseMove) and 3× in UCScreenImage
   (SetMyUCScreenImage/GenerateImage/OnPaint). One resolution→(canvasW,canvasH,threshold,previewRect) table.
2. **The 120×120 / 5-column grid engine** — UCThemeLocal/Web/Mask share fit math, grid origin
   (`30+135·col`,`60+150·row`), record shape `[bmp,x,y,w,h,name]`, OnPaint base loop, scroll math
   (FrmMain_MouseWheel + MouseDown/Move scrollbar), and dispose/clear. One reusable browser component.
3. **`RotateImg` family** (UCScreenImage:436/473/520 + UCImageCut:1272) — one bounds-expanded rotate
   with an edge-fill strategy enum (none / black-ring / replicate-band); kill 3 near-duplicate copies.
4. **The width-fit / height-fit letterbox** (`buttonTPJCW_Click`/`buttonTPJCH_Click`, 21 branches each)
   — one `fit(axis, canvasW, canvasH)` that both call; also unifies `button1_Click`'s re-apply.
5. **The zoom-recompute** (`ImageBiliBianhuan` live vs `ImageBiliBianhuanUp` commit) — identical bili
   math + identical res chain; difference is only "rebuild imageSub0 or not." One method + a `rebuild` flag.

## Undetermined (deliberately not asserted — out of these 5 files)
- `USB_PACKED_Head`, `fileName`, and the full DC element-record layout (only the image-tail READ is
  here; the `num` int32s are skipped, never decoded) — owned by the parent form / DC codec.
- Delegate cmd handlers (16/32/48 for themes; 0/1 for screen-image) — in FormCZTV, not here.
- `directionB` numeric→physical-angle mapping and per-orientation URL/folder selection (Web/Mask only
  signal "re-list" on change) — in the caller.
- `myDevicePingMu`/`myLddVal`/`isBiliPingmu`/resolution-flag SETTERS — set externally; only read here.
- Whether OnPaint's half-scale (640/1600/1920) and `isBiliPingmu` rects match on-glass — reporter-gated.

## Confidence
**High** for every method entry: all 5 files read in full (UCThemeLocal/Web/Mask end-to-end;
UCImageCut ctor/SetImage-head/buttons/zoom/pan/mouse read directly, per-res threshold bodies
cross-referenced to `AUDIT_THEME_MASK.md`; UCScreenImage read 96–1440 in full). Line numbers exact
against the decompiled source. Coverage: **94/94 methods** (23+20+12+16+23). Lower confidence only for
the "Undetermined" items, which are cross-file and deliberately omitted rather than inferred.
