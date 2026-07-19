# Audit — Theme / Mask / Image-Crop subsystem (TRCC 2.1.6 decompile)

Source: `~/Downloads/TRCCCAPEN/TRCC_decompiled/TRCC.DCUserControl/`

Files audited:
- `UCThemeLocal.cs` (777 lines) — local-theme thumbnail browser
- `UCThemeWeb.cs` (608 lines) — cloud/web background browser
- `UCThemeMask.cs` (340 lines) — cloud mask browser
- `UCImageCut.cs` (2025 lines) — image crop/fit editor

Every claim below is `file:line` with a verbatim quote. Anything not quoted is
omitted, not inferred. Cross-file callers (`FormCZTV`, download plumbing,
`ImageToJpg`, DC codec) are **out of scope** — see "Not covered".

---

## 1. UCThemeLocal.cs — local theme browser

### Purpose & lifecycle
Grid browser of LOCAL themes. Loads each theme's thumbnail, lays it out in a
5-column grid, supports scrolling, delete, and a "轮播" (carousel/slideshow)
picker. Communicates to the parent form via a single delegate.

- Class + delegate — `UCThemeLocal.cs:12-16`:
  ```csharp
  public class UCThemeLocal : UserControl
  {
      public delegate void delegateThemeLocal(int cmd, object info = null, object data = null, object data1 = null);
      public delegateThemeLocal delegateLocal;
  ```
- Background art loaded in ctor — `:107-108`:
  ```csharp
  imageGdt = (Image)(object)Resources.P滚动条按钮;   // scrollbar knob
  imageBk = (Image)(object)Resources.P0本地主题;      // "local theme" panel bg
  ```

### Thumbnail source precedence (the load waterfall)
`SetThemeLocal(ArrayList array, string GifDirectory, string fileName, byte USB_PACKED_Head)`
is the entry — `array` is a list of theme-folder names, `GifDirectory` the base
dir. For each folder it picks the thumbnail in strict order:

1. **`Theme.png`** (preferred) — `:241-247`:
   ```csharp
   if (File.Exists(GifDirectory + (string)array[j] + "\\Theme.png"))
   {
       Bitmap val3 = new Bitmap(GifDirectory + (string)array[j] + "\\Theme.png");
   ```
2. **`00.png`** (fallback background) — `:248-254`:
   ```csharp
   else if (File.Exists(GifDirectory + (string)array[j] + "\\00.png"))
   {
       Bitmap val4 = new Bitmap(GifDirectory + (string)array[j] + "\\00.png");
   ```
3. **The DC/`fileName` binary** (last resort) — parses the config header to
   extract an embedded preview image — `:255-281`:
   ```csharp
   FileStream fileStream = new FileStream(GifDirectory + (string)array[j] + "\\" + fileName, FileMode.OpenOrCreate);
   BinaryReader binaryReader = new BinaryReader(fileStream);
   ...
   byte b = binaryReader.ReadByte();
   if (b == USB_PACKED_Head)
   {
       int num = binaryReader.ReadInt32();
       for (int k = 0; k < num; k++)
       {
           binaryReader.ReadInt32();
       }
       int count = binaryReader.ReadInt32();
       val2 = ByteToBitmap(binaryReader.ReadBytes(count));
   }
   ```

### DC/Theme binary header layout (READ side, from the thumbnail fallback)
This is the only place in these four files that parses the DC/Theme binary. The
observed on-disk shape of the `fileName` file — `:261-270`:
- byte 0: a header/magic byte, compared to `USB_PACKED_Head` (passed in) — `:261-262`.
- int32: `num` = a count of leading int32 records — `:264`.
- `num` × int32: skipped (`binaryReader.ReadInt32();` in a loop) — `:265-268`.
- int32: `count` = byte length of the embedded image — `:269`.
- `count` bytes: a PNG/image blob → `ByteToBitmap` — `:270`.

Note the full element/overlay record layout is NOT decoded here — this reader
only skips `num` int32s then grabs the trailing image. `USB_PACKED_Head` and
`fileName` are supplied by the caller (out of scope).

### Bitmap<->byte helpers
- `ByteToBitmap` — `MemoryStream` → `Image.FromStream` — `:197-207`.
- `BitmapToByte` — saves as **PNG** — `:209-218`:
  ```csharp
  ((Image)Bit).Save((Stream)memoryStream, ImageFormat.Png);
  ```
  (Theme.png / 00.png are round-tripped through PNG bytes before display —
  `:243-246`, `:250-253`.)

### Thumbnail fit math (aspect-preserving into 120×120)
`myImageW = myImageH = 120` — `:32-34`. Each thumbnail is scaled to fit 120 on
the long edge — `:284-295`:
```csharp
int width = ((Image)val2).Width;
int height = ((Image)val2).Height;
if (width > height)
{
    width = 120;
    height = 120 * ((Image)val2).Height / ((Image)val2).Width;
}
else
{
    height = 120;
    width = 120 * ((Image)val2).Width / ((Image)val2).Height;
}
```

### Grid layout (5 columns)
`myImageCountX = 5`, `MyThemeCount = 5` — `:36,46`. Cell origin — `:282-283`:
```csharp
int num2 = 30 + 135 * (j % 5);   // x = 30 + 135*col
int num3 = 60 + 150 * (j / 5);   // y = 60 + 150*row
```
Constants: `myImageXS=30, myImageYS=60, myImageX=135, myImageY=150` — `:24-30`.
Per-item ArrayList record stored = `[bitmap, x, y, w, h, name]` — `:301-306`.

### Painting
Thumbnail centered in its 120-box, name drawn 20px below — `:347-349`:
```csharp
graphics.DrawImage((Image)(object)val, num + (120 - num3) / 2, num2 + (120 - num4) / 2 + offsetY, num3, num4);
Rectangle rectangle = new Rectangle(num, num2 + 120 + offsetY, 120, 20);
graphics.DrawString((string)arrayList[5], fontName, ...);
```
Font = `微软雅黑` (Microsoft YaHei) 9pt bold — `:18`.

### Carousel ("轮播" / lunbo) picker
Up to 6 themes selectable, ordered — `LunBoArrayCount = 6`, `lunBoArray = new int[6]{-1,...}` — `:64,72`.
- Overlay numbered badges `P轮播1..6` in paint — `:365-374`.
- User default carousel timer `myLunBoTimer` min 3s — `:66`, clamped `:561-567,583-589`.
- Selection toggles into `lunBoArray` on mouse-down over the badge zone — `:481-509`.

### Delegate command codes (int cmd)
- `delegateLocal?.Invoke(myMode)` — category filter changed (mode 0/1/2) — `:173,180,187,194`.
- `16` = load/apply a theme (click a thumbnail) — `:514`:
  ```csharp
  delegateLocal?.Invoke(16, i, (myMode >= 2) ? 1 : 0);
  ```
- `32` = delete a theme (click the X badge) — `:462,467`.
- `48` = carousel state changed — `:474,507,563,616`.
  `myMode >= 2` distinguishes user-authored (mode 2) vs default (<2) themes,
  passed as the `data` flag.

### Category modes
`Button_Set(mode)` — 0=All, 1=Default, 2=User — `:157-168`. Only the "All"
button is visible by default (`buttonDefault`/`buttonUser`/`buttonThemeOut`
`.Visible = false`) — `:696,711,755`. `buttonThemeOut_Click` (export all) is an
empty stub — `:619-621`.

---

## 2. UCThemeWeb.cs — cloud / web background browser

### Purpose & lifecycle
Grid browser of CLOUD backgrounds ("云端背景"). Same 120-box / 5-col grid engine
as UCThemeLocal, but images are supplied pre-loaded (already downloaded by the
caller), and there are **7 category buttons** (All + 1..6).

- Class + delegate — `:10-14`.
- Background art = `p0云端背景` (cloud background panel) — `:102`:
  ```csharp
  imageBk = (Image)(object)Resources.p0云端背景;
  ```

### Data entry — no file parsing here
`SetThemeWeb(ArrayList imageArray, ArrayList nameArray)` takes **already-decoded
Bitmaps** + names; it does NOT read files or URLs — `:273-337`. Layout/fit math
is byte-identical to UCThemeLocal:
- fit into 120 — `:295-306` (same `if (width > height)` block).
- grid origin `30 + 135*(j%5)`, `60 + 150*(j/5)` — `:293-294`.
- record `[bitmap, x, y, w, h, name]` — `:307-312`.

### `directionB` / rotation awareness
`myDirectionB` tracks the device orientation; a change forces a re-fetch —
`:60,119-126`:
```csharp
private int myDirectionB = 0;
...
public void CheakDirectionB(int direction)
{
    if (myDirectionB != direction)
    {
        myDirectionB = direction;
        Display_Button();
    }
}
```
`Display_Button()` re-invokes the delegate with the current category and clears
the download guard — `:190-194`:
```csharp
public void Display_Button()
{
    delegateWeb?.Invoke(myMode);
    isDownLoad = false;
}
```
So orientation change → re-request the catalog for the new direction. The URL /
resolution / per-orientation folder is built in the caller (out of scope); this
file only signals "direction changed, re-list".

### Category buttons (7)
`buttonAll` + `button1..6`, each sets `myMode` 0..6 and calls `Display_Button()`,
guarded by `isDownLoad` to prevent concurrent fetches — `:196-271`:
```csharp
private void button3_Click(object sender, EventArgs e)
{
    if (!isDownLoad)
    {
        isDownLoad = true;
        myMode = 3;
        Button_Set(myMode);
        Display_Button();
    }
}
```

### Click → apply
Clicking a thumbnail sends the item NAME back with cmd `16` — `:402-406`:
```csharp
string info = (string)arrayList[5];
if (e.X >= num && e.X <= num3 && e.Y >= num2 + offsetY && e.Y <= num4 + offsetY)
{
    delegateWeb?.Invoke(16, info);
    break;
}
```
`isDownLoad` gates re-entry during click handling — `:390-409`.

---

## 3. UCThemeMask.cs — cloud MASK browser

### Purpose & lifecycle
Nearly identical to UCThemeWeb but for MASKS ("online themes" = masks), and it
has **no category buttons**. Confirms the "online-themes-are-masks" caveat: this
control's images are masks, delegate name is `delegateMask`.

- Class + delegate — `:10-14`.
- Panel background = `p0云端主题` ("cloud theme") — `:81,329`:
  ```csharp
  imageBk = (Image)(object)Resources.p0云端主题;
  ```
  (The panel is labelled "cloud theme" but the control class + delegate are
  named Mask — the UI "cloud theme"/"online theme" tab surfaces masks.)

### Data entry / fit / grid — same engine
`SetThemeMask(ArrayList imageArray, ArrayList nameArray)` — pre-decoded bitmaps +
names, no file/URL work — `:139-203`. Fit into 120 — `:161-172`; grid origin
`30+135*(j%5)`, `60+150*(j/5)` — `:159-160`; record `[bitmap,x,y,w,h,name]` — `:173-178`.

### `directionB` / rotation
Identical to UCThemeWeb — `:60,98-105`:
```csharp
private int myDirectionB = 0;
...
public void CheakDirectionB(int direction)
{
    if (myDirectionB != direction)
    {
        myDirectionB = direction;
        Display_Button();
    }
}
```
`Display_Button()` — `:133-137` (invoke delegate with `myMode`, clear
`isDownLoad`). `myMode` here is always 0 (no category buttons).

### Click → apply mask
Clicking a thumbnail returns its NAME with cmd `16` — `:268-272`:
```csharp
if (e.X >= num && e.X <= num3 && e.Y >= num2 + offsetY && e.Y <= num4 + offsetY)
{
    delegateMask?.Invoke(16, arrayList[5]);
    break;
}
```
`isDownLoad` guards re-entry — `:256-274`.

---

## 4. UCImageCut.cs — image crop / fit editor

### Purpose & lifecycle
Interactive crop/fit tool. Given a source image and a target device resolution
(selected via `isNNNxNNN` bool flags), it fits the image to the canvas
(letterbox with centering), lets the user pan, zoom (slider "bili" = ratio),
rotate 90°, and pick width-fit vs height-fit. On OK it returns the final
canvas-sized bitmap via `ucImageCut(Image)`.

- Class + delegate — `:12-14`:
  ```csharp
  public delegate void delegateUCImageCut(Image image);
  public delegateUCImageCut ucImageCut;
  ```
- Working images — `:16-22`:
  ```csharp
  public Image imageAll = null;    // source (full)
  public Bitmap imageSub0 = null;  // scaled source at fit size
  public Bitmap imageSub = null;   // final canvas-sized output
  ```

### Target-resolution flags (the device matrix)
Every fit path branches on these bools — `:66-90`:
```csharp
public bool isFanZhuan = false;     // "翻转" = flipped/portrait mount
public bool is240x240 = false;
public bool is320x320 = false;
public bool is360x360 = false;
public bool is640x480 = false;
public bool is480x480 = false;
public bool is1600x720 = false;
public bool is1280x480 = false;
public bool is1920x462 = false;
public bool is854x480 = false;
public bool is960x540 = false;
public bool is800x480 = false;
public bool isBiliPingmu = false;   // declared, never read in this file
```
Default (no flag set) = **320×240** (landscape) or **240×320** when
`isFanZhuan` — `:574-613`.

### `isFanZhuan` = portrait/flipped mount → swaps canvas dims
For every widescreen res, `isFanZhuan` allocates the **transposed** canvas and
uses a transposed aspect threshold. Example 1600×720 — `:273-315`:
```csharp
if (isFanZhuan)
{
    imageSub = new Bitmap(720, 1600);
    ...
    if ((double)image.Width > (double)image.Height * 0.45)  // 0.45 = 720/1600
    { wVal = 720; hVal = image.Height * 720 / image.Width; yVal += (1600 - hVal)/2; isTPJCW = true; }
    else
    { hVal = 1600; wVal = image.Width * 1600 / image.Height; xVal += (720 - wVal)/2; isTPJCW = false; }
}
else
{
    imageSub = new Bitmap(1600, 720);
    ...
    if ((double)image.Height > (double)image.Width * 0.45)
    { hVal = 720; wVal = image.Width * 720 / image.Height; xVal += (1600 - wVal)/2; isTPJCW = false; }
    else
    { wVal = 1600; hVal = image.Height * 1600 / image.Width; yVal += (720 - hVal)/2; isTPJCW = true; }
}
```

### The fit algorithm (SetImage), general form
For each res the pattern in `SetImage` — `:134-624` — is:
1. Allocate `imageSub` at canvas size (or transposed if `isFanZhuan`).
2. Compare source aspect against the canvas aspect threshold.
3. Scale to fit ONE axis fully; center on the other (`xVal`/`yVal` += half the
   slack). Set `isTPJCW` (true = width-fit, false = height-fit).
4. Build `imageSub0` = source scaled to `(wVal,hVal)`, then blit onto `imageSub`
   at `(xVal,yVal)` — `:614-620`:
   ```csharp
   imageSub0 = new Bitmap(wVal, hVal);
   Graphics val2 = Graphics.FromImage((Image)(object)imageSub0);
   val2.DrawImage(imageAll, 0, 0, wVal, hVal);
   val2.Dispose();
   val2 = Graphics.FromImage((Image)(object)imageSub);
   val2.DrawImage((Image)(object)imageSub0, xVal, yVal);
   val2.Dispose();
   ```

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
`:1309-1323`:
```csharp
Image val = RotateImg(imageAll, 90f);
imageAll.Dispose();
imageAll = val;
if (isTPJCW) buttonTPJCW_Click(null, null);
else buttonTPJCH_Click(null, null);
```
`RotateImg(Image, angle)` builds a bounds-expanded rotated bitmap with
high-quality interpolation — `:1272-1307`:
```csharp
val4.InterpolationMode = (InterpolationMode)3;   // HighQualityBicubic
val4.SmoothingMode = (SmoothingMode)2;           // HighQuality
```

### Zoom (`bili` = ratio) via bottom slider
The knob X (`bitmapCX`) maps to a scale factor around a 248 center — `:1367-1374`:
```csharp
if (bitmapCX > 248)
    bili = 1f + (float)(bitmapCX - 248) * 0.03f;
else
    bili = 1f / (1f + (float)(248 - bitmapCX) * 0.03f);
```
Slider track: center `gdtX=248`, min `gdtX1=12`, max `gdtX2=484` — `:32,36-38`;
knob clamped 12..484 — `:1682-1689,1874-1881`. Zoom recenters via
`xVal -= (num-num3)/2` — `:1380-1381,1550-1551`. `ImageBiliBianhuan` (live) vs
`ImageBiliBianhuanUp` (on release, rebuilds `imageSub0` at zoomed size) — `:1325-1491,1493-1669`.

### Pan (drag) — per-res gain factors
Dragging the image translates `xVal/yVal`; the gain compensates for the preview
being a downscaled view of a large canvas — `:1736-1757`:
```csharp
if (is1600x720 || is1920x462)      { xVal += (e.X - xPos) * 4; yVal += (e.Y - yPos) * 4; }
else if (is1280x480)               { xVal += (int)((double)(e.X - xPos) / 0.375); ... }
else if (is640x480 || is800x480 || is854x480 || is960x540) { xVal += (e.X - xPos) * 2; ... }
else                               { xVal += e.X - xPos; yVal += e.Y - yPos; }
```

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
- OK → returns final `imageSub` — `:1262-1265`:
  ```csharp
  private void buttonTPJCOK_Click(object sender, EventArgs e)
  { ucImageCut?.Invoke((Image)(object)imageSub); }
  ```
- Close → returns null — `:1267-1270`.

### Canvas background asset
Editor chrome background = `P0图片裁减320240` (a 320×240-labelled asset used for
all resolutions) — `:2009`.

---

## Caveats & landmines (for the Python port)

1. **"Online themes" tab = MASKS.** `UCThemeMask` (delegate `delegateMask`) is
   the cloud/online browser; its panel is even labelled "cloud theme"
   (`p0云端主题`, `:81`) but the payload is masks. `UCThemeWeb` (`p0云端背景`,
   `:102`) is cloud *backgrounds*. Do not conflate.

2. **Thumbnail precedence is Theme.png → 00.png → DC-embedded image** in local
   themes (`UCThemeLocal.cs:241-270`). `Theme.png` is a thumbnail only; `00.png`
   is the real background used as a fallback thumbnail; the DC binary carries an
   embedded preview at its tail.

3. **DC header shape (read side): `[byte head][int32 num][num×int32][int32 len][len bytes image]`**
   (`UCThemeLocal.cs:261-270`). The `num` int32s are SKIPPED here — full element/
   overlay records are NOT decoded in these files. `head` is compared to a
   caller-supplied `USB_PACKED_Head`.

4. **`directionB` only signals "re-list", it does not rotate anything here.**
   `CheakDirectionB` on Web/Mask (`UCThemeWeb.cs:119-126`, `UCThemeMask.cs:98-105`)
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
   fit math (`if (width > height) …120…`) and identical scroll/offset code. In
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
**High** for everything quoted: all four files were read in full
(UCImageCut in two passes covering 1-2025). Line cites are exact against the
decompiled source as-is. **Low/none** for anything in "Not covered" — those
claims are deliberately omitted rather than inferred, per the no-speculation
constraint. Caveat: decompiled C# (ILSpy-style) may reorder locals; the
`//IL_xxxx` comment noise was ignored and does not affect the logic quoted.
