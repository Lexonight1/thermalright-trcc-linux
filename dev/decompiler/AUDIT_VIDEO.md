# AUDIT — Video / Playback / Animation subsystem (TRCC 2.1.6 decompile)

Line-cited audit of the three video-related UserControls. Every claim quotes the
source. Paths are relative to
`/home/ignorant/Downloads/TRCCCAPEN/TRCC_decompiled/TRCC.DCUserControl/`.

Files audited:
- `UCVideoCut.cs` (2509 lines) — video CLIP / CROP / rotate / transcode → `Theme.zt`
- `UCBoFangQiKongZhi.cs` (1880 lines) — playback controller (plays `1.mp4` on device)
- `UCDongHuaLianDong.cs` (400 lines) — animation-linkage panel (designer-only stub)

> **Scope note up front:** none of these three files contains the wire encode
> (`ImageToJpg` / RGB565 / the `mode==48` send path). They produce **JPEG frames
> inside a `Theme.zt` container** (`UCVideoCut`) and **preview `Bitmap`s**
> (`UCBoFangQiKongZhi`). The wire path that consumes `Theme.zt` / `mode==48` lives
> in `FormCZTV.cs` (out of scope here) — see the "Wire" section for what these
> files hand off and what they do NOT do.

---

## 1. `UCVideoCut.cs` — video clip / crop / rotate / transcode

### 1.1 Purpose & lifecycle

A `UserControl` video editor. The user selects a video, drags two clip markers to
set in/out (`startTimerVal` / `stopTimerVal`), chooses a fit mode (width-fit vs
height-fit), rotates, chooses framerate (15 or 24 fps), previews, then presses OK
to transcode the clip into a `Theme.zt` file which is returned to the host via a
delegate.

Delegate contract (lines 16-18):
```
public delegate void delegateUCVideoCut(ArrayList array);
public delegateUCVideoCut ucVideoCut;
```
- On successful transcode the host gets an `ArrayList` whose element 0 is the
  `Theme.zt` path — `buttonTPJCOK_Click` (lines 1215-1218):
  ```
  ArrayList arrayList = new ArrayList();
  arrayList.Add(allPicAddr + "Theme.zt");
  ucVideoCut?.Invoke(arrayList);
  ```
- On close / failure the host gets `null` — `buttonClose_Click` (line 352)
  `ucVideoCut?.Invoke(null);` and `SetImage` failure (line 1681) `ucVideoCut?.Invoke(null);`.

Temp working dirs, ctor (lines 219-220):
```
onePic = Application.StartupPath + "\\Data\\Temp\\0.png";
allPicAddr = Application.StartupPath + "\\Data\\Temp\\";
```

Key constants (lines 154-158):
```
private const byte USB_PACKED_Head = 220;      // 0xDC container header
private const string fileName = "Theme.zt";
private const int AllTimerVal = 300000;         // 300000 ms = 5 min max clip
```

### 1.2 Video decode → frame

**Video info probe** — `SetImage(string name)` (lines 1488-2144). Runs ffmpeg to
stderr log and text-parses it (lines 1523-1525):
```
string text = $"ffmpeg -i \"{name}\" > \"{Application.StartupPath}\\log.txt\" 2>&1";
```
Parsing:
- Duration string, line 1565: `text = text.Substring(text.IndexOf("Duration: ") + 10);` then `text2 = text.Substring(0, text.IndexOf(","));`
- Duration → ms via `UCVideoCut_TimerToLong` (line 1570) → `originalTimerLen`.
- Resolution from the `Video: ` section (lines 1597-1612):
  ```
  text = text.Substring(text.IndexOf("Video: ") + 7);
  ...
  char[] videoRatio = GetVideoRatio(text.ToCharArray());
  ...
  bitAngleW = (originalImageW = Convert.ToInt32(text2.Split(new char[1] { 'x' })[0]));
  bitAngleH = (originalImageH = Convert.ToInt32(text2.Split(new char[1] { 'x' })[1]));
  bitAngle = 0;
  ```
- `GetVideoRatio` (lines 2146-2218) scans for the first `NNNNxNNNN` / `NNNxNNNN`
  digit-x-digit pattern (4x4, 4x3, 3x4, 3x3 digit variants, in that priority order).

**Rejections** (hard-coded limits):
- AV1 codec rejected, line 1580: `if (text.Contains("Video: av1"))` → messagebox, no info.
- Resolution cap, line 1663: `if (isGetVideoInfo && originalImageW * originalImageH > 8294400)` → rejected. `8294400 = 3840 * 2160` (4K).

**Single-frame preview extract** — `GetOneImage(string timer)` (lines 1284-1337).
Re-entrancy guarded by `isGetOneImage` (line 1288). ffmpeg command (line 1308):
```
"ffmpeg -display_rotation {5} -ss {3} -i \"{0}\" -y -s {1}x{2} -frames:v 1 -f image2 \"{4}\""
//        bitAngle           timer      wVal hVal              onePic
```
Clamps seek so a frame exists (lines 1303-1307):
```
if (num + 1000 > originalTimerLen) { num = originalTimerLen - 1000; timer = ...; }
```
Then composites the decoded frame onto a `wValSub x hValSub` canvas at `(xVal,yVal)`
(lines 1329-1332):
```
imageSub = new Bitmap(wValSub, hValSub);
Image val = Image.FromFile(onePic);
Graphics val2 = Graphics.FromImage((Image)(object)imageSub);
val2.DrawImage(val, xVal, yVal);
```

**Per-resolution crop/fit (decode size vs canvas).** `SetImage` (lines 1684-2115)
and the two fit-button handlers set six numbers per target resolution:
`wVal/hVal` = ffmpeg decode size (the scaled video rect), `xVal/yVal` = paint
offset into the canvas, `wValSub/hValSub` = canvas size, plus `xValSub/yValSub` =
where the preview canvas is drawn on the control. Selection flags: `is240x240`,
`is320x320`, `is360x360`, `is480x480`, `is640x480`, `is800x480`, `is854x480`,
`is960x540`, `is1600x720`, `is1280x480`, `is1920x462`, plus `isFanZhuan` (flip /
portrait mount) and default 320x240 (lines 66-88).

Example, 480x480 auto-fit branch (lines 1753-1774):
```
wValSub = 480; hValSub = 480; xVal = 0; yVal = 0;
if (bitAngleH > bitAngleW) { hVal = 480; wVal = bitAngleW * 480 / bitAngleH; xVal += (480 - wVal) / 2; isTPJCW = false; }
else { wVal = 480; hVal = bitAngleH * 480 / bitAngleW; yVal += (480 - hVal) / 2; isTPJCW = true; }
```

`isTPJCW` = "picture fits by Width" (`true`) vs by Height (`false`) — toggled by
`buttonTPJCW_Click` (lines 625-892, fit-to-width) and `buttonTPJCH_Click`
(lines 356-623, fit-to-height). `isFanZhuan` swaps the sub-canvas W/H (portrait
mount), e.g. default flipped branch (lines 2070-2091) uses `wValSub=240; hValSub=320`.

**Aspect-ratio threshold magic numbers** (used to decide width-fit vs height-fit for
widescreen panels; these are the panel's aspect ratio `shortSide/longSide`):
| Resolution | threshold | lines |
|---|---|---|
| 1600x720 | `0.45` | 1786, 1809 |
| 800x480 | `0.6` | 1835, 1858 |
| 854x480 | `0.56206` | 1884, 1907 |
| 960x540 | `0.5625` | 1933, 1956 |
| 1920x462 | `77.0 / 320.0` (=0.240625) | 1982, 2005 |
| 1280x480 | `0.375` | 2031, 2054 |
| default 320x240 / flipped | `0.75` | 2078, 2101 |

**Rotation** — `buttonXuanzhuan_Click` (lines 894-928):
```
bitAngle = (bitAngle + 270) % 360;
switch (bitAngle) {
  case 0:   bitAngleW = originalImageW; bitAngleH = originalImageH; break;
  case 90:  bitAngleH = originalImageW; bitAngleW = originalImageH; break;
  case 180: bitAngleW = originalImageW; bitAngleH = originalImageH; break;
  case 270: bitAngleH = originalImageW; bitAngleW = originalImageH; break;
}
```
So each click rotates by **270° (= −90°)**; at 90/270 the W/H are swapped. `bitAngle`
is passed to ffmpeg as `-display_rotation`.

### 1.3 Playback control (preview loop inside the cutter)

`Timer_event()` (lines 279-345) is the host-driven tick. Three modes:
1. Marker held (`isMouseDown1/2`): after 32 ticks, refresh the single preview frame
   (lines 283-302).
2. Preview playing (`isYulan`): frame cadence gate (lines 306-310):
   ```
   yuLanTimer++;
   if ((double)yuLanTimer * 15.625 < (double)yuLanCount * 1000.0 / (double)yuLanHz) return;
   ```
   i.e. tick base is **15.625 ms** and it advances a frame when elapsed ≥
   `frameIndex * (1000/yuLanHz)`. Reads `{yuLanCount:0000}.bmp` (line 311), composites
   onto `wValSub x hValSub` at `(xVal,yVal)` (lines 321-327), `yuLanCount++`; missing
   file → reset to frame 1 (lines 334-338).

Preview transcode — `buttonYulan_Click` (lines 1224-1247): decodes the whole
selected clip to `%04d.bmp` and sets `yuLanHz = originalImageHz` (line 1243):
```
"ffmpeg -display_rotation {6} -ss {4} -t {5} -i \"{0}\" -y -r {1} -s {2}x{3} -f image2 \"{7}%04d.bmp\""
//        bitAngle             start  dur       hz    wVal hVal              allPicAddr
```

Framerate selector: `button1_Click` → `originalImageHz = 15` (lines 2220-2225);
`button2_Click` → `originalImageHz = 24` (lines 2227-2232). Default field = 15 (line 112).

Seek/marker geometry: `UCVideoCut_MouseTimer` (lines 1249-1254) and
`UCVideoCut_TimerMouse` (lines 1256-1260) map an X pixel in `[9,489]` (480-px track)
to/from a timer position. Clip capped at `AllTimerVal` (300000 ms) everywhere a
marker moves (e.g. lines 1371-1376).

### 1.4 Wire / transcode output — `Theme.zt` (the `0xDC` container)

`ZhuanMaPanDuan(bool isFF)` (lines 1021-1203) is the real transcode. Per-resolution
it decodes to a **supersampled** size, then re-composites down onto a canvas of the
same supersample, writing JPEG frames into `Theme.zt`.

Supersample factors (decode `-s WxH` in the ffmpeg call):
- 1600x720 or 1920x462 → `wVal*4 x hVal*4` (line 1045)
- 1280x480 → `wVal/0.375 x hVal/0.375` (line 1072) (= ×2.6667)
- 640x480 / 800x480 / 854x480 / 960x540 → `wVal*2 x hVal*2` (line 1099)
- else → native `wVal x hVal` (line 1125)

All four ffmpeg variants use `-display_rotation {bitAngle}` and
`originalImageHz`, e.g. line 1045:
```
"ffmpeg -display_rotation {6} -ss {4} -t {5} -i \"{0}\" -y -r {1} -s {2}x{3} -f image2 \"{7}%04d.bmp\""
```
Clip re-capped to 300000 ms before decode (lines 1029-1036).

Re-composite step (lines 1148-1157) builds a `Bitmap` at the supersampled canvas
size and draws the decoded frame at the supersampled `(xVal,yVal)`:
```
Bitmap val2 = new Bitmap(
  (is1600x720 || is1920x462) ? (wValSub * 4) : ((is640x480||is800x480||is854x480||is960x540) ? (wValSub*2) : (is1280x480 ? ((int)((double)wValSub/0.375)) : wValSub)),
  ... same for height ...);
Graphics val3 = Graphics.FromImage((Image)(object)val2);
val3.DrawImage(val, ...xVal scaled..., ...yVal scaled...);
byte[] value2 = BitmapToByte(val2);   // JPEG-encode
```

`BitmapToByte` (lines 939-962) JPEG-encodes each frame:
```
((Image)Bit).Save((Stream)memoryStream, ImageFormat.Jpeg);
```

**`Theme.zt` binary layout** — written by `ZhuanMaPanDuan` (lines 1132-1201) and,
identically-structured, by `BmpToThemeFile` (lines 964-1019):
```
byte    0xDC (220)                 // header, line 1140 / 974
int32   frameCount (num - 1)       // line 1184 / 1001
int32[] timestamps                 // line 1185-1189 / 1002-1006: value = (int)(num2 * i), i=1..count
int32   len, byte[] jpeg           // per frame, line 1190-1195 / 1007-1012
```
Per-frame timestamp step `num2`:
- `ZhuanMaPanDuan`: `num2 = 1000.0 / originalImageHz` (line 1136) — 15/24 fps.
- `BmpToThemeFile`: `num2 = 41.666666666666664` (line 972) — fixed **24 fps** (1000/24).

Frame count sizing / progress: `num4 = (int)((stopTimerVal-startTimerVal)/1000.0 * originalImageHz)` (line 1138); progress bar `jindu` advances as frames land (lines 1158-1169). End of ffmpeg output detected by 100 consecutive missing-file polls (`num3 < 100`, line 1141 / 975).

`buttonTPJCOK_Click` (lines 1205-1222) orchestrates: sets `isZhuanma`, shows the
"Rendering" label, calls `ZhuanMaPanDuan(flag)` where `flag = !isYulan` (reuse the
already-decoded bmps if a preview was running), then invokes the delegate.

`OnPaint` (lines 235-252) draws the timeline (progress bar `bitmapJindu` when
`jindu != 0`, otherwise the two clip-marker bitmaps) and the live `imageSub` at
`(xValSub,yValSub)`.

---

## 2. `UCBoFangQiKongZhi.cs` — playback controller

### 2.1 Purpose & lifecycle

Plays a fixed file `\Data\Player\1.mp4` and streams decoded frames to the caller
via `Timer_Get_Image()`. A double-buffered ffmpeg-decode pipeline keeps two
5-second chunks of bmp frames on disk (dirs `1\` and `2\`) and alternates between
them so playback never stalls on decode.

Constants (lines 14-16, 118-120):
```
private const string BoFangQiMuLu = "\\Data\\Player\\";
private const string BoFangQiFile = "1.mp4";
private const int ffTimer = 5;      // (declared; unused arithmetic constant)
private const int ffWaitTimer = 35; // wait-loop iterations (×100 ms)
```
ctor (lines 140-147) resolves the paths and deletes any stale `1.mp4`:
```
boFangQiMuLu = Application.StartupPath + "\\Data\\Player\\";
boFangQiFile = boFangQiMuLu + "1.mp4";
... File.Delete(boFangQiFile) ...
```
`SetNewFile(string name)` (lines 1727-1752) swaps the source, kills ffmpeg, calls
`SetImage(true)`, and auto-plays if info parsed.

### 2.2 Video decode → frame

`SetImage(bool bl)` (lines 272-880): reentrancy-guarded by `dirCount != 0` (line
292). Same ffmpeg-`-i`-to-log probe (lines 305-334), same Duration parse
(lines 330-334), AV1 reject (line 337), same `GetVideoRatio` resolution parse
(lines 362-370), same 4K cap `originalImageW*originalImageH > 8294400` (line 419).
Then the same per-resolution fit block (lines 439-879) as `UCVideoCut`, computing
`wVal/hVal` (decode size), `xVal/yVal` (paint offset), `wValSub/hValSub` (canvas).
Note: this control has NO `xValSub/yValSub` (it does not paint itself, it returns a
`Bitmap`).

Default framerate field is **16** here (line 96): `private int originalImageHz = 16;`
(vs 15 in `UCVideoCut`).

`FFmpeg_Video_Bmp()` (lines 985-1033) decodes ONE 5-second chunk into the current
back-buffer dir. Chunk math (lines 1006-1017):
```
long val  = nowStopTimerVal;
long num  = nowStopTimerVal + 5000;
if (num >= originalTimerLen) { val2 = originalTimerLen - nowStopTimerVal; nowStopTimerVal = 0L; }
else                          { val2 = 4875L;                              nowStopTimerVal += 5000L; }
```
ffmpeg command (line 1027):
```
"ffmpeg -ss {4} -t {5} -i \"{0}\" -y -r {1} -s {2}x{3} -f image2 \"{6}%04d.bmp\""
//         start  dur       file      hz   wVal hVal              boFangQiMuLu+dirCount+"\\"
```
NOTE: no `-display_rotation` here (playback control has no rotate button); rotation
is expressed only through the fit block's `isFanZhuan` W/H swap.

### 2.3 Playback control — frame cadence, VideoCount / double-buffer

`Timer_Get_Image()` (lines 906-983) is the per-frame pump; returns `imageSub`.
- Reads `{nowDir}\{dirVal:0000}.bmp` (line 912), composites onto a fresh
  `wValSub x hValSub` bitmap at `(xVal,yVal)` (lines 917-923), swaps it into
  `imageSub`.
- Advance (lines 932-933): `dirVal++; nowTimerVal += 62.5;` — **62.5 ms/frame = 16 fps**.
- Loop-to-start when the clip time is reached (lines 934-937): `if (nowTimerVal >= originalTimerLen) nowTimerVal = 0.0;`
- **Chunk-boundary / buffer swap** when the next bmp is missing (lines 939-961):
  ```
  if (dirVal != 81) { if (nowTimerVal >= originalTimerLen - 125) nowTimerVal = 0.0; }
  else if (nowTimerVal >= originalTimerLen - 100) nowTimerVal = 0.0;
  if (nowDir == 1) nowDir = 2; else nowDir = 1;   // flip read buffer
  dirVal = 1;
  ```
  Frame index `81` is the boundary (≈ 5000 ms × 16 fps + 1 = 80 frames per chunk).
- **Trigger next decode** into the other buffer (lines 962-973):
  ```
  if (dirCount == nowDir) {
    if (dirCount == 1) dirCount = 2; else dirCount = 1;
    FFmpeg_Video_Bmp();
  }
  ```
- Seek-bar UI update when not dragging (lines 974-980): updates `labelNowTimer`,
  progress width `bitmapJDW = LongToWidth(num)`.

`Player()` (lines 1035-1041) → `button1_Click`. `button1_Click` (lines 1049-1094)
toggles play/pause; on first start it primes buffer 1 and waits up to `35 × 100 ms`
for `1\0001.bmp` (lines 1084-1091):
```
for (int i = 0; i < 35; i++) { Thread.Sleep(100); if (File.Exists(boFangQiMuLu + "1\\0001.bmp")) break; }
```
`ClosePlayer()` (lines 1043-1047) sets `isStart=false`.

**Fit-adjust buttons** `buttonTPJCH_Click` (lines 1096-1360) / `buttonTPJCW_Click`
(lines 1362-1626) re-run the per-resolution fit then re-decode the current chunk
(same 35×100 ms prime, e.g. lines 1351-1358). Seek by dragging the progress bar:
`UCBoFangQiKongZhi_MouseDown/Move/Up` (lines 1628-1725); on mouse-up it seeks
`nowTimerVal = jdtTimerVal`, resets both buffers, decodes, and if the first bmp
never appears within 35 tries it falls back to time 0 (lines 1672-1725).

Timer helpers `TimerToLong` (lines 170-179), `LongToTimer` (lines 181-190),
`LongToWidth` (lines 192-196, track width `479`).

### 2.4 Wire

This control does **not** touch the wire; it returns a `Bitmap imageSub` (public,
line 26) to the host each tick. The host (`FormCZTV`) is what encodes that bitmap to
the panel. `OnPaint` (lines 160-168) only draws the seek progress bar.

---

## 3. `UCDongHuaLianDong.cs` — animation linkage (designer-only stub)

### 3.1 Purpose & lifecycle

A `UserControl` whose ENTIRE body is field declarations + `InitializeComponent()` +
`Dispose()`. **There is no logic, no event handler wiring, and no public method** in
this decompiled file — it is a layout-only panel. Any behavior is attached by the
host after construction (no `+=` handler subscription appears in this file, unlike
`UCVideoCut`/`UCBoFangQiKongZhi` which wire `Click`/`Mouse*` in their
`InitializeComponent`).

### 3.2 What it lays out (the "linkage" surface)

Control size `682 x 84` (line 396); background `Resources.P01动画联动` ("01 animation
linkage", line 374). Named controls (lines 12-46, positioned lines 140-371):
- `buttonOnOff` — a slide on/off toggle, image `Resources.P滑动开` ("slide-on",
  line 141), at `(22,24)`.
- `buttonM1`..`buttonM6` — 6 radio-style "point-select" boxes (`Resources.P点选框A`
  selected / `P点选框` unselected, lines 154/167…), laid out as a 3×2 grid
  (`(240,30)`,`(280,30)`,`(320,30)`,`(240,57)`,`(280,57)`,`(320,57)`). `buttonM1`
  starts selected (`P点选框A`).
- `textBox1` (line 236, `(429,5)`) and `textBox2` (line 248, `(547,5)`) — two 4-char
  numeric inputs (`MaxLength = 4`, lines 237/249), default text "0".
- Three triples for 3 slots: `buttonYL1..3` (preview animation, `Resources.P预览动画`,
  lines 256/269/282), `buttonXZ1..3` (load animation, `Resources.P载入动画`,
  lines 295/308/321), `buttonWL1..3` (network/cloud, `Resources.P网络按钮`,
  lines 334/347/360).

Interpretation from the layout (asset names + grid): this panel links **per-metric
animations** — an on/off master, a 6-way metric selector (M1–M6), two numeric
threshold fields, and 3 animation slots each with preview / load-from-disk /
load-from-network actions. The actual linkage logic is NOT in this file.

---

## 4. Caveats / gotchas (for the Linux port)

1. **Two different default framerates.** `UCVideoCut.originalImageHz` defaults 15
   (line 112, user-selectable 15 or 24); `UCBoFangQiKongZhi.originalImageHz`
   defaults **16** (line 96) and is fixed. `Timer_Get_Image` advances 62.5 ms/frame
   (line 933) = exactly 16 fps — coupled to the 16, not configurable.
2. **`Theme.zt` timestamp step differs by writer.** `ZhuanMaPanDuan` uses
   `1000/originalImageHz` (line 1136); `BmpToThemeFile` hard-codes
   `41.666666…` = 24 fps (line 972). Do not assume one constant.
3. **Supersample-then-downscale on widescreen transcode.** Decode size is ×4
   (1600x720/1920x462, line 1045), ×2.6667 (1280x480, `/0.375`, line 1072), ×2
   (640/800/854x480, 960x540, line 1099), ×1 otherwise — and the re-composite canvas
   matches (line 1149). Missing this yields soft/blurry frames on wide panels.
4. **Aspect-fit thresholds are raw magic doubles**, per resolution: `0.45`, `0.6`,
   `0.56206`, `0.5625`, `77.0/320.0`, `0.375`, `0.75` (table in §1.2). They decide
   width-fit vs height-fit; not derivable from a single formula (854x480 uses
   `0.56206`, not `480/854=0.5620…` rounded the obvious way).
5. **Rotation click is −90° (adds 270 mod 360).** `bitAngle=(bitAngle+270)%360`
   (line 899), fed to ffmpeg as `-display_rotation`. At 90/270 `bitAngleW/H` are
   swapped (lines 906-917). `UCBoFangQiKongZhi` has NO rotate — it never passes
   `-display_rotation` (line 1027); rotation there is only the `isFanZhuan` canvas swap.
6. **Hard input limits.** AV1 rejected outright (line 1580 / 337). Resolution
   `W*H > 8294400` (4K) rejected (line 1663 / 419). Clip length capped at
   `AllTimerVal = 300000` ms = 5 min (line 158, enforced at every marker move).
7. **The pipeline is ffmpeg-CLI + disk-bmp, not in-process decode.** Frames go
   video → `%04d.bmp` on disk → `Image.FromFile` → composite → JPEG (`Theme.zt`) or
   `Bitmap` (playback). End-of-stream is detected by polling for a missing next file
   (100 empty polls in the writer; 35×100 ms prime waits in the player). Timing is
   wall-clock/`Thread.Sleep`, not frame-accurate.
8. **`0xDC` (220) is the container header both for `Theme.zt` and USB packets.**
   Named `USB_PACKED_Head = 220` (line 154) but here it is written as the first byte
   of the `.zt` file (line 1140 / 974), not sent on the wire in this file.

---

## 5. Not covered / out of scope in these three files

- **The wire encode (`ImageToJpg`, RGB565, `mode==48`) is NOT here.** These files
  emit `Theme.zt` (JPEG frames in a `0xDC` container) and preview `Bitmap`s. The
  code that reads `Theme.zt` / a returned `Bitmap` and pushes it to the panel
  (including any `mode==48` branch) lives in `FormCZTV.cs` — audit that file for the
  actual send cadence and 565 packing.
- **`UCDongHuaLianDong` linkage semantics** (what M1–M6 map to, what the two
  threshold textboxes mean, how preview/load/network act) — the handlers are not in
  this file; only the layout is. Needs the host (`FormCZTV` / whichever form hosts
  it) to resolve.
- **How the host consumes the `ucVideoCut` delegate `ArrayList`** (registers the
  `Theme.zt` as the active theme, triggers upload, etc.) — caller side, not here.
- **`Data\Player\1.mp4` provenance** — who writes `1.mp4` before
  `UCBoFangQiKongZhi` plays it — is external to this control.
