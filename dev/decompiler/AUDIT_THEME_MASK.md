# Audit — Theme / Mask / Image-Crop subsystem

<!-- audit-state: origin=2.0.3.0 addresses=2.1.6.0 known-bad=UCThemeWeb.cs::UCThemeMask -->
> **Audited against TRCC 2.0.3; citations re-anchored to TRCC 2.1.6.**
> 3 method(s) documented here changed in TRCC 2.1.6 and have NOT been re-read: `UCThemeLocal`, `buttonTPJCH_Click`, `buttonTPJCW_Click` — read those entries as TRCC 2.0.3 history.
> [`AUDIT_INDEX.md`](AUDIT_INDEX.md#provenance)
<!-- /audit-state -->

Source: `~/Downloads/TRCCCAPEN/TRCC_decompiled/TRCC.DCUserControl/`

Files audited:
- `UCThemeLocal.cs` (963 lines) — local-theme thumbnail browser
- `UCThemeWeb.cs` (620 lines) — cloud/web background browser
- `UCThemeMask.cs` (352 lines) — cloud mask browser
- `UCImageCut.cs` (2,645 lines) — image crop/fit editor

Every claim below is anchored to a `file:line` citation and describes what the
code at that line does. Anything not cited is omitted, not inferred. Cross-file
callers (`FormCZTV`, download plumbing, `ImageToJpg`, DC codec) are **out of
scope** — see "Not covered".

---

## 1. UCThemeLocal.cs — local theme browser

### Purpose & lifecycle
Grid browser of LOCAL themes. Loads each theme's thumbnail, lays it out in a
5-column grid, supports scrolling, delete, and a "轮播" (carousel/slideshow)
picker. Communicates to the parent form via a single delegate.

- `UCThemeLocal` (UCThemeLocal.cs:12-16) — a `UserControl` declaring exactly one
  outbound delegate type, `delegateThemeLocal(int cmd, object info, object data,
  object data1)`, whose three payload parameters all default to null, plus a single
  instance field `delegateLocal`. Every message this control sends the parent form
  travels through that one field; `cmd` selects the meaning of the payload.
- Background art loaded in ctor — `:107-108` — `imageGdt` is assigned the
  scrollbar-knob resource `Resources.P滚动条按钮`, `imageBk` the "local theme"
  panel background `Resources.P0本地主题`. Both are held as `Image` for the
  lifetime of the control.

### Thumbnail source precedence (the load waterfall)
`SetThemeLocal(ArrayList array, string GifDirectory, string fileName, byte USB_PACKED_Head)`
is the entry — `array` is a list of theme-folder names, `GifDirectory` the base
dir. For each folder it picks the thumbnail in strict order:

1. **`Theme.png`** (preferred) — `:241-247` — existence-tests the path built by
   concatenating `GifDirectory`, the folder name from `array[j]`, and the literal
   `\Theme.png`; if present, loads it straight into a `Bitmap`.
2. **`00.png`** (fallback background) — `:248-254` — the `else if` arm: same
   concatenation with the literal `\00.png`, loaded into a `Bitmap`. Reached only
   when `Theme.png` is absent.
3. **The DC/`fileName` binary** (last resort) — parses the config header to
   extract an embedded preview image — `:255-281` — opens
   `GifDirectory + folder + "\" + fileName` as a `FileStream` with
   `FileMode.OpenOrCreate`, wraps it in a `BinaryReader`, and reads one byte. Only
   if that byte equals the caller-supplied `USB_PACKED_Head` does it continue: it
   reads an int32 `num`, loops `num` times discarding one int32 per pass, reads an
   int32 `count` (the image byte length), reads exactly `count` bytes, and converts
   them with `ByteToBitmap`. A head-byte mismatch yields no thumbnail at all.

### DC/Theme binary header layout (READ side, from the thumbnail fallback)
This is the only place in these four files that parses the DC/Theme binary. The
observed on-disk shape of the `fileName` file — `:261-270`:
- byte 0: a header/magic byte, compared to `USB_PACKED_Head` (passed in) — `:261-262`.
- int32: `num` = a count of leading int32 records — `:264`.
- `num` × int32: skipped (a 32-bit little-endian read in a loop) — `:277-280`.
- int32: `count` = byte length of the embedded image — `:269`.
- `count` bytes: a PNG/image blob → `ByteToBitmap` — `:282`.

Note the full element/overlay record layout is NOT decoded here — this reader
only skips `num` int32s then grabs the trailing image. `USB_PACKED_Head` and
`fileName` are supplied by the caller (out of scope).

### Bitmap<->byte helpers
- `ByteToBitmap` — `MemoryStream` → `Image.FromStream` — `:227-237`.
- `BitmapToByte` — saves the incoming bitmap into a `MemoryStream` with
  `ImageFormat.Png` and returns the stream's bytes, so the encoding is always
  **PNG** regardless of the source file's format — `:239-248`.
  (Theme.png / 00.png are round-tripped through PNG bytes before display —
  `:243-246`, `:250-253`.)

### Thumbnail fit math (aspect-preserving into 120×120)
`myImageW = myImageH = 120` — `:32-34`. Each thumbnail is scaled to fit 120 on
the long edge — `:284-295`. The loaded bitmap's own width and height are read
first, then compared: when width is strictly greater than height the drawn width
is pinned to 120 and the drawn height becomes `120 * srcHeight / srcWidth`;
otherwise the drawn height is pinned to 120 and the drawn width becomes
`120 * srcWidth / srcHeight`. All of it is **integer** division, so the result
truncates — a square source takes the `else` branch and lands on 120×120.

### Grid layout (5 columns)
`myImageCountX = 5`, `MyThemeCount = 5` — `:36,46`. Cell origin — `:282-283` —
for item index `j` the cell's x is `30 + 135 * (j % 5)` and its y is
`60 + 150 * (j / 5)`, i.e. column = index modulo 5, row = index integer-divided
by 5, with a 30/60 px margin and a 135/150 px pitch. The literals are inlined at
the call site rather than read from the fields.
Constants: `myImageXS=30, myImageYS=60, myImageX=135, myImageY=150` — `:24-30`.
Per-item ArrayList record stored = `[bitmap, x, y, w, h, name]` — `:301-306`.

### Painting
Thumbnail centered in its 120-box, name drawn 20px below — `:347-349`. The
thumbnail is blitted at `cellX + (120 - drawnW) / 2` horizontally and
`cellY + (120 - drawnH) / 2 + offsetY` vertically, at its own `drawnW`×`drawnH`
size — so the aspect-fit leftovers become symmetric padding inside the 120-box,
and `offsetY` (the scroll offset) shifts the whole row. The caption then gets a
`Rectangle` at `(cellX, cellY + 120 + offsetY)` measuring 120×20, into which
`DrawString` renders the theme name held at slot `arrayList[5]` of the item's
record, using `fontName`.
Font = `微软雅黑` (Microsoft YaHei) 9pt bold — `:18`.

### Carousel ("轮播" / lunbo) picker
Up to 6 themes selectable, ordered — `LunBoArrayCount = 6`, `lunBoArray = new int[6]{-1,...}` — `:64,72`.
- Overlay numbered badges `P轮播1..6` in paint — `:365-374`.
- User default carousel timer `myLunBoTimer` min 3s — `:66`, clamped `:606-612,583-589`.
- Selection toggles into `lunBoArray` on mouse-down over the badge zone — `:481-509`.

### Delegate command codes (int cmd)
- `delegateLocal?.Invoke(myMode)` — category filter changed (mode 0/1/2) — `:203,180,187,194`.
- `16` = load/apply a theme (click a thumbnail) — `:514` — fires `delegateLocal`
  (null-conditionally) with cmd `16`, the clicked item's index `i` as `info`, and
  a `data` flag of `1` when `myMode >= 2` (user-authored category) else `0`.
- `32` = delete a theme (click the X badge) — `:462,467`.
- `48` = carousel state changed — `:474,507,563,616`.
  `myMode >= 2` distinguishes user-authored (mode 2) vs default (<2) themes,
  passed as the `data` flag.

### Category modes
`Button_Set(mode)` — 0=All, 1=Default, 2=User — `:187-198`. Only the "All"
button is visible by default (`buttonDefault`/`buttonUser`/`buttonThemeOut`
`.Visible = false`) — `:696,711,755`. `buttonThemeOut_Click` (export all) is an
empty stub — `:687-689`.

---

## 2. UCThemeWeb.cs — cloud / web background browser

### Purpose & lifecycle
Grid browser of CLOUD backgrounds ("云端背景"). Same 120-box / 5-col grid engine
as UCThemeLocal, but images are supplied pre-loaded (already downloaded by the
caller), and there are **7 category buttons** (All + 1..6).

- Class + delegate — `:10-14`.
- Background art = `p0云端背景` (cloud background panel) — `:102` — the ctor
  assigns `Resources.p0云端背景` to the `imageBk` field, the same field name the
  local browser uses for its own panel art.

### Data entry — no file parsing here
`SetThemeWeb(ArrayList imageArray, ArrayList nameArray)` takes **already-decoded
Bitmaps** + names; it does NOT read files or URLs — `:285-349`. Layout/fit math
is byte-identical to UCThemeLocal:
- fit into 120 — `:307-318` (the same landscape-vs-portrait width/height test).
- grid origin `30 + 135*(j%5)`, `60 + 150*(j/5)` — `:305-306`.
- record `[bitmap, x, y, w, h, name]` — `:319-324`.

### `directionB` / rotation awareness
`myDirectionB` tracks the device orientation; a change forces a re-fetch —
`:60,119-126`. The private int field `myDirectionB` starts at `0`; the public
`CheakDirectionB(int direction)` (sic — the decompile spells it "Cheak") compares
the argument with the stored value, and **only when they differ** stores the new
value and calls `Display_Button()`. Same-value calls are a no-op, so the caller
can poll it every tick without re-triggering downloads.
`Display_Button()` re-invokes the delegate with the current category and clears
the download guard — `:202-206`: it calls `delegateWeb` null-conditionally
passing `myMode` as the sole argument (cmd position — no info/data), then sets
`isDownLoad` back to false so the next category click is accepted.
So orientation change → re-request the catalog for the new direction. The URL /
resolution / per-orientation folder is built in the caller (out of scope); this
file only signals "direction changed, re-list".

### Category buttons (7)
`buttonAll` + `button1..6`, each sets `myMode` 0..6 and calls `Display_Button()`,
guarded by `isDownLoad` to prevent concurrent fetches — `:208-283`. All seven
handlers are the same four steps in the same order, differing only in the literal
mode: `button3_Click`, for instance, returns immediately unless `isDownLoad` is
false, then raises `isDownLoad` to true, assigns `myMode = 3`, calls
`Button_Set(myMode)` to repaint the button strip, and finally `Display_Button()`
to re-request the catalog. `isDownLoad` is therefore both the re-entry guard and
the in-flight flag, and it is cleared inside `Display_Button()` itself.

### Click → apply
Clicking a thumbnail sends the item NAME back with cmd `16` — `:414-418`. The
handler walks the item records, pulls the name from `arrayList[5]` of each, and
hit-tests the mouse point against that item's stored rect — x within its left and
right edges, y within its top and bottom edges **shifted by the scroll `offsetY`**
(the stored y values are unscrolled). On the first hit it invokes `delegateWeb`
with cmd `16` and the name string as `info`, then breaks out of the loop, so a
click can only ever match one cell.
`isDownLoad` gates re-entry during click handling — `:402-421`.

---

## 3. UCThemeMask.cs — cloud MASK browser

### Purpose & lifecycle
Nearly identical to UCThemeWeb but for MASKS ("online themes" = masks), and it
has **no category buttons**. Confirms the "online-themes-are-masks" caveat: this
control's images are masks, delegate name is `delegateMask`.

- Class + delegate — `:10-14`.
- Panel background = `p0云端主题` ("cloud theme") — `:81,329` — `imageBk` is
  assigned `Resources.p0云端主题`, once in the ctor and once again on the repaint
  path.
  (The panel is labelled "cloud theme" but the control class + delegate are
  named Mask — the UI "cloud theme"/"online theme" tab surfaces masks.)

### Data entry / fit / grid — same engine
`SetThemeMask(ArrayList imageArray, ArrayList nameArray)` — pre-decoded bitmaps +
names, no file/URL work — `:151-215`. Fit into 120 — `:173-184`; grid origin
`30+135*(j%5)`, `60+150*(j/5)` — `:171-172`; record `[bitmap,x,y,w,h,name]` — `:185-190`.

### `directionB` / rotation
Identical to UCThemeWeb — `:60,98-105`: a private int `myDirectionB` initialised
to `0`, and a public `CheakDirectionB(int direction)` that stores the new
orientation and calls `Display_Button()` only when the incoming value differs
from the stored one.
`Display_Button()` — `:145-149` (invoke delegate with `myMode`, clear
`isDownLoad`). `myMode` here is always 0 (no category buttons).

### Click → apply mask
Clicking a thumbnail returns its NAME with cmd `16` — `:280-284`. Same hit-test
as the Web browser — mouse x inside the cell's left/right bounds, mouse y inside
its top/bottom bounds offset by the scroll `offsetY` — and on a hit it invokes
`delegateMask` with cmd `16` and the record's `arrayList[5]` element (the mask
name) as `info`, then breaks. Note it passes the raw object rather than casting to
string first, which is the only textual difference from `UCThemeWeb`.
`isDownLoad` guards re-entry — `:268-286`.

---

## 4. UCImageCut.cs — image crop / fit editor

### Purpose & lifecycle
Interactive crop/fit tool. Given a source image and a target device resolution
(selected via `isNNNxNNN` bool flags), it fits the image to the canvas
(letterbox with centering), lets the user pan, zoom (slider "bili" = ratio),
rotate 90°, and pick width-fit vs height-fit. On OK it returns the final
canvas-sized bitmap via `ucImageCut(Image)`.

- Class + delegate — `:12-14` — one delegate type `delegateUCImageCut(Image image)`
  carrying a single `Image` and nothing else (no cmd code, unlike the browsers),
  plus the public instance field `ucImageCut` the parent form subscribes to.
- Working images — `:16-22` — three public fields, all initialised null:
  `imageAll` (`Image`) is the untouched full-size source; `imageSub0` (`Bitmap`)
  is the source rescaled to the current fit size `wVal`×`hVal`; `imageSub`
  (`Bitmap`) is the canvas-sized output that is both painted and handed back on OK.

### Target-resolution flags (the device matrix)
Every fit path branches on these bools — `:66-90`. There is no resolution enum
and no width/height pair: the target is encoded as thirteen public bool fields,
all defaulting to false, and every fit/paint/pan routine re-tests them in a long
if/else-if chain. The fields, in declaration order:

| Field | Meaning |
|---|---|
| `isFanZhuan` | "翻转" = flipped/portrait mount; orthogonal to the rest |
| `is240x240` | 240×240 square |
| `is320x320` | 320×320 square |
| `is360x360` | 360×360 square |
| `is640x480` | 640×480 |
| `is480x480` | 480×480 square |
| `is1600x720` | 1600×720 widescreen |
| `is1280x480` | 1280×480 widescreen |
| `is1920x462` | 1920×462 widescreen |
| `is854x480` | 854×480 widescreen |
| `is960x540` | 960×540 widescreen |
| `is800x480` | 800×480 widescreen |
| `isBiliPingmu` | declared, never read in this file |

Default (no flag set) = **320×240** (landscape) or **240×320** when
`isFanZhuan` — `:574-613`.

### `isFanZhuan` = portrait/flipped mount → swaps canvas dims
For every widescreen res, `isFanZhuan` allocates the **transposed** canvas and
uses a transposed aspect threshold. Example 1600×720 — `:273-315`. The flag
splits the code into two mirror-image branches:

- **`isFanZhuan` true (portrait mount)** — `imageSub` is allocated as a
  720×1600 `Bitmap`. The aspect test compares source *width* against source
  *height* × `0.45` (the threshold `0.45` being 720/1600). Wider than that means
  width is the binding axis: `wVal = 720`, `hVal = srcHeight * 720 / srcWidth`,
  and the vertical slack `(1600 - hVal) / 2` is added to `yVal`, with
  `isTPJCW = true`. Otherwise height binds: `hVal = 1600`,
  `wVal = srcWidth * 1600 / srcHeight`, horizontal slack `(720 - wVal) / 2` added
  to `xVal`, `isTPJCW = false`.
- **`isFanZhuan` false (landscape mount)** — `imageSub` is a 1600×720 `Bitmap`
  and the same `0.45` threshold is applied the other way round: source *height*
  against source *width* × `0.45`. Taller than that means height binds:
  `hVal = 720`, `wVal = srcWidth * 720 / srcHeight`, slack `(1600 - wVal) / 2`
  into `xVal`, `isTPJCW = false`. Otherwise width binds: `wVal = 1600`,
  `hVal = srcHeight * 1600 / srcWidth`, slack `(720 - hVal) / 2` into `yVal`,
  `isTPJCW = true`.

The aspect comparison is done in `double`; the scale arithmetic is integer.

### The fit algorithm (SetImage), general form
For each res the pattern in `SetImage` — `:134-624` — is:
1. Allocate `imageSub` at canvas size (or transposed if `isFanZhuan`).
2. Compare source aspect against the canvas aspect threshold.
3. Scale to fit ONE axis fully; center on the other (`xVal`/`yVal` += half the
   slack). Set `isTPJCW` (true = width-fit, false = height-fit).
4. Build `imageSub0` = source scaled to `(wVal,hVal)`, then blit onto `imageSub`
   at `(xVal,yVal)` — `:614-620`. Concretely: allocate `imageSub0` as a new
   `wVal`×`hVal` bitmap, take a `Graphics` on it, draw `imageAll` at origin
   `(0,0)` stretched to `wVal`×`hVal`, dispose that `Graphics`; then take a
   second `Graphics` on the canvas `imageSub`, draw `imageSub0` unscaled at
   `(xVal, yVal)`, and dispose again. Two draws, never one — the rescale and the
   placement are separate steps, which is why `imageSub0` survives as a field for
   the zoom path to reuse.

### Per-resolution aspect thresholds (the magic numbers)
The `(double)image.Height > (double)image.Width * T` test picks width-fit vs
height-fit. `T` = canvas H/W:
| Res | threshold T | line |
|---|---|---|
| 1600×720 | `0.45` | `:280,300` |
| 800×480 | `0.6` | `:323,343` |
| 854×480 | `0.56206` | `:366,386` |
| 960×540 | `0.5625` | `:409,429` |
| 1920×462 | `77.0/320.0` (=0.240625) | `:452,472` |
| 1280×480 | `0.375` | `:495,515` |
| 640×480 | `0.75` (`Width*0.75 > Height`) | `:558` / portrait `:538` |
| 320×240 default | `0.75` (`Width*0.75 > Height`) | `:599` |
Square resolutions (240/320/360/480) use plain `image.Height > image.Width` —
e.g. `:198,218,238,258`.

### Width-fit / Height-fit buttons (force one axis)
- `buttonTPJCW_Click` — force WIDTH fit: `wVal = canvasW; hVal = imageAll.Height*canvasW/imageAll.Width; yVal += (canvasH-hVal)/2` — `:726-992` (per-res, e.g. 320×320 at `:780-788`).
- `buttonTPJCH_Click` — force HEIGHT fit: `hVal = canvasH; wVal = imageAll.Width*canvasH/imageAll.Height; xVal += (canvasW-wVal)/2` — `:994-1260` (320×320 at `:1048-1056`).

### Rotate 90°
`button1_Click` rotates the SOURCE 90° then re-applies the current fit mode —
`:1749-1763`. It calls `RotateImg(imageAll, 90f)`, disposes the old `imageAll`,
installs the rotated result in its place, and then re-runs whichever fit the user
was already in by invoking the button handler directly with null sender/args —
`buttonTPJCW_Click` when `isTPJCW` is set, `buttonTPJCH_Click` otherwise. So
rotation never changes the fit mode, only the pixels it operates on, and pan/zoom
state is recomputed from scratch by the re-fit.
`RotateImg(Image, angle)` builds a bounds-expanded rotated bitmap with
high-quality interpolation — `:1712-1747` — the destination `Graphics` has its
`InterpolationMode` set to `3` (HighQualityBicubic) and its `SmoothingMode` to
`2` (HighQuality) before the rotated draw. Those two enum ordinals are what the
decompile shows; a port needs the equivalent quality settings or repeated
rotations visibly degrade.

### Zoom (`bili` = ratio) via bottom slider
The knob X (`bitmapCX`) maps to a scale factor around a 248 center — `:1367-1374`.
The mapping is asymmetric: right of centre, `bili` is `1` plus the pixel distance
`(bitmapCX - 248)` times `0.03`; left of centre (and at exactly 248), `bili` is
the **reciprocal** of `1` plus the distance `(248 - bitmapCX)` times the same
`0.03`. Both sides compute in `float`. That reciprocal is what makes one step
left and one step right cancel out rather than 0.97/1.03 drifting.
Slider track: center `gdtX=248`, min `gdtX1=12`, max `gdtX2=484` — `:32,36-38`;
knob clamped 12..484 — `:2242-2249,1874-1881`. Zoom recenters via
`xVal -= (num-num3)/2` — `:1380-1381,1550-1551`. `ImageBiliBianhuan` (live) vs
`ImageBiliBianhuanUp` (on release, rebuilds `imageSub0` at zoomed size) — `:1325-1491,1493-1669`.

### Pan (drag) — per-res gain factors
Dragging the image translates `xVal/yVal`; the gain compensates for the preview
being a downscaled view of a large canvas — `:1736-1757`. The mouse delta is the
current point minus the last recorded `xPos`/`yPos`, and the same gain is applied
to both axes, chosen by a four-arm if/else-if chain over the resolution flags:

| Condition | Gain applied to the mouse delta |
|---|---|
| `is1600x720` or `is1920x462` | ×`4` |
| `is1280x480` | ÷`0.375` (cast back to int through `double`) |
| `is640x480`, `is800x480`, `is854x480` or `is960x540` | ×`2` |
| anything else (squares, default 320×240) | ×`1` — the raw delta |

### Preview draw positions & sizes (per-res, in OnPaint)
The editor draws `imageSub` at a fixed on-screen rect per res — `:626-724`.
Examples:
- 320×320 → `DrawImage(imageSub, 90, 90)` — `:634`.
- 360×360 → `(70,70)` — `:638`; 240×240 → `(130,130)` — `:642`; 480×480 → `(10,10)` — `:646`.
- 1600×720 → landscape `(50,160,400,180)`, flipped `(160,50,180,400)` — `:652-656`.
- 854×480 → landscape `(36,130,427,240)`, flipped `(130,36,240,427)` — `:674-678`.
- 1920×462 → landscape `(10,192,480,116)`, flipped `(192,10,116,480)` — `:696-700`.
- default 320×240 → `(90,130,320,240)`; flipped 240×320 → `(130,90,240,320)` — `:716,720`.
Layout constants `X_Val`/`Y_Val`/`X_Val360`… declared `:42-64` (mostly unused in
paint; paint uses literals).

### OK / Cancel
- OK → returns final `imageSub` — `buttonTPJCOK_Click`, `:1702-1705` — its whole body is
  one null-conditional invocation of `ucImageCut` passing the current `imageSub`
  as the delegate's `Image`. No copy, no re-render, no validation: the parent
  receives the live canvas bitmap the editor has been painting all along.
- Close → returns null — `:1707-1710` — the same delegate is invoked with a null
  image, which is how the parent distinguishes cancel from accept.

### Canvas background asset
Editor chrome background = `P0图片裁减320240` (a 320×240-labelled asset used for
all resolutions) — `:2629`.

---

## Caveats & landmines (for the Python port)

1. **"Online themes" tab = MASKS.** `UCThemeMask` (delegate `delegateMask`) is
   the cloud/online browser; its panel is even labelled "cloud theme"
   (`p0云端主题`, `:81`) but the payload is masks. `UCThemeWeb` (`p0云端背景`,
   `:102`) is cloud *backgrounds*. Do not conflate.

2. **Thumbnail precedence is Theme.png → 00.png → DC-embedded image** in local
   themes (`UCThemeLocal.cs:253-282`). `Theme.png` is a thumbnail only; `00.png`
   is the real background used as a fallback thumbnail; the DC binary carries an
   embedded preview at its tail.

3. **DC header shape (read side): `[byte head][int32 num][num×int32][int32 len][len bytes image]`**
   (`UCThemeLocal.cs:261-270`). The `num` int32s are SKIPPED here — full element/
   overlay records are NOT decoded in these files. `head` is compared to a
   caller-supplied `USB_PACKED_Head`.

4. **`directionB` only signals "re-list", it does not rotate anything here.**
   `CheakDirectionB` on Web/Mask (`UCThemeWeb.cs:131-138`, `UCThemeMask.cs:110-117`)
   just re-invokes the catalog delegate on orientation change. Actual
   per-orientation URL/folder selection lives in the caller (out of scope).

5. **`isFanZhuan` swaps canvas W↔H and the aspect threshold** for every
   widescreen res (`UCImageCut.cs:273-315` etc.). Portrait-mount panels allocate
   the transposed bitmap (e.g. 1600×720 → 720×1600). Square resolutions ignore it
   for canvas size.

6. **Per-resolution aspect thresholds are hard-coded magic numbers**, NOT derived
   from W/H at runtime: `854×480`→`0.56206` (`:366`), `1920×462`→`77.0/320.0`
   (`:452`), `1600×720`→`0.45`, `1280×480`→`0.375`, `960×540`→`0.5625`,
   `800×480`→`0.6`, `640×480`/default→`0.75`. Port them verbatim; `0.56206`
   ≠ 480/854 (=0.5620…) exactly and `0.45` ≠ 720/1600 (=0.45 exact) — reproduce
   the literals, don't recompute.

7. **Fit = letterbox-contain + center, never crop-to-fill.** One axis fills the
   canvas, the other is centered with slack (`xVal/yVal += (canvas-scaled)/2`);
   the surrounding canvas stays transparent/black. `isTPJCW` records which axis
   was fitted (true=width). The pan/zoom then let the user push content off-edge.

8. **Pan drag has per-resolution gain factors** (`×4`, `÷0.375`, `×2`, `×1`) at
   `UCImageCut.cs:1736-1757` — the preview is a downscaled view, so screen-pixel
   deltas are multiplied to canvas space. Any port that pans must replicate these
   or panning speed will be wrong per device.

9. **All three browsers share ONE 120×120 / 5-column grid engine** with identical
   fit math (the landscape test, fitting the long edge to 120) and identical scroll/offset code. In
   the Python port this is one reusable component, not three.

10. **Zoom factor is linear-ish around a 248px slider center**, `0.03`/px, with
    reciprocal on the shrink side (`UCImageCut.cs:1367-1374`). Track is 12..484.

---

## Not covered (out of scope of these 4 files)
- The actual cloud DOWNLOAD (URLs, `web/zt{w}{h}` vs `theme{res}` folder naming,
  per-resolution/orientation catalog fetch) — done by the caller / `FormCZTV`,
  not in these UserControls. These files receive pre-decoded bitmaps
  (`SetThemeWeb`/`SetThemeMask`) or a folder list (`SetThemeLocal`).
- **DC WRITE path / full element+overlay record encoding** — only a partial READ
  (image-tail extraction) exists here (`UCThemeLocal.cs:261-270`).
- `directionB` numeric values and how they map to angles — only compared for
  change here.
- `ImageToJpg` / wire encoding / `get_encode_rotation` — different file.
- The `int cmd` codes' handlers (16/32/48) — in the parent form.

## Confidence
**High** for everything cited: all four files were read in full
(UCImageCut in two passes covering 1-2025). Line cites are exact against the
decompiled source as-is. **Low/none** for anything in "Not covered" — those
claims are deliberately omitted rather than inferred, per the no-speculation
constraint. Caveat: decompiled C# (ILSpy-style) may reorder locals; the
`//IL_xxxx` comment noise was ignored and does not affect the logic described.
