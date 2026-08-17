# AUDIT — Video / Playback / Animation subsystem

<!-- audit-state: origin=2.0.3.0 addresses=2.1.6.0 known-bad=UCVideoCut.cs::ZhuanMaPanDuan -->
> **Audited against TRCC 2.0.3; citations re-anchored to TRCC 2.1.6.**
> 1 method(s) documented here changed in TRCC 2.1.6 and have NOT been re-read: `ZhuanMaPanDuan` — read those entries as TRCC 2.0.3 history.
> [`AUDIT_INDEX.md`](AUDIT_INDEX.md#provenance)
<!-- /audit-state -->

Line-cited audit of the three video-related UserControls. Every claim quotes the
source. Paths are relative to
`/home/ignorant/Downloads/TRCCCAPEN/TRCC_decompiled/TRCC.DCUserControl/`.

Files audited:
- `UCVideoCut.cs` (3,001 lines) — video CLIP / CROP / rotate / transcode → `Theme.zt`
- `UCBoFangQiKongZhi.cs` (2,285 lines) — playback controller (plays `1.mp4` on device)
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

Delegate contract (lines 16-18): a delegate type `delegateUCVideoCut` taking one
`ArrayList` argument, and a public field `ucVideoCut` of that type that the host
assigns to receive the editor's result.

- On successful transcode the host gets an `ArrayList` whose element 0 is the
  `Theme.zt` path — `buttonTPJCOK_Click` (lines 1215-1218) allocates a fresh
  `ArrayList`, adds the single string `allPicAddr + "Theme.zt"`, and invokes
  `ucVideoCut` with it (null-conditional, so an unassigned delegate is a no-op).
- On close / failure the host gets `null` — `buttonClose_Click` (line 360) and the
  `SetImage` failure path (line 1681) both invoke `ucVideoCut` with a `null`
  argument instead of a list.

Temp working dirs, ctor (lines 219-220): `onePic` is set to the app startup path
plus `\Data\Temp\0.png` (the single-frame scratch file), and `allPicAddr` to the
startup path plus `\Data\Temp\` (the frame-sequence and `Theme.zt` output dir).

Key constants (lines 154-158):
- `USB_PACKED_Head` — byte constant `220` (0xDC), the container header.
- `fileName` — string constant `"Theme.zt"`, the transcode output filename.
- `AllTimerVal` — int constant `300000`, i.e. 300000 ms = 5 min max clip.

### 1.2 Video decode → frame

**Video info probe** — `SetImage(string name)` (lines 1488-2144). Runs ffmpeg to a
stderr log and text-parses it (lines 1523-1525): the shell command is `ffmpeg -i`
on the quoted source path, with stdout redirected to `<Application.StartupPath>\log.txt`
and stderr merged into it (`2>&1`); the log file is then read back as text.

Parsing:
- Duration string, line 1565: the log text is advanced past the literal
  `"Duration: "` (skipping its 10 characters) and the substring up to the next
  `","` is taken as the duration token.
- Duration → ms via `UCVideoCut_TimerToLong` (line 1570) → `originalTimerLen`.
- Resolution from the `Video: ` section (lines 1597-1612): the text is advanced
  past the literal `"Video: "` (skipping its 7 characters), the remaining
  characters are handed to `GetVideoRatio`, and the returned `WxH` token is split
  on the `'x'` character — element 0 is converted to int and stored in both
  `originalImageW` and `bitAngleW`, element 1 in both `originalImageH` and
  `bitAngleH`. `bitAngle` is reset to `0`.
- `GetVideoRatio` (lines 2146-2218) scans for the first `NNNNxNNNN` / `NNNxNNNN`
  digit-x-digit pattern (4x4, 4x3, 3x4, 3x3 digit variants, in that priority order).

**Rejections** (hard-coded limits):
- AV1 codec rejected, line 1580: if the probe text contains the literal
  `"Video: av1"` a messagebox is shown and no info is published.
- Resolution cap, line 1663: if info parsed (`isGetVideoInfo`) and
  `originalImageW * originalImageH > 8294400`, the file is rejected.
  `8294400 = 3840 * 2160` (4K).

**Single-frame preview extract** — `GetOneImage(string timer)` (lines 1284-1337).
Re-entrancy guarded by `isGetOneImage` (line 1288). The ffmpeg command (line 1308)
is `ffmpeg -display_rotation <bitAngle> -ss <timer> -i "<source>" -y -s <wVal>x<hVal>
-frames:v 1 -f image2 "<onePic>"` — exactly one frame, scaled by ffmpeg to the
decode size, written to the `0.png` scratch path.

Clamps seek so a frame exists (lines 1303-1307): if the requested position plus
`1000` ms would exceed `originalTimerLen`, the position is pulled back to
`originalTimerLen - 1000` and the timer string is rebuilt from the clamped value.

Then composites the decoded frame onto a `wValSub x hValSub` canvas at `(xVal,yVal)`
(lines 1329-1332): a new `imageSub` bitmap of the canvas size is allocated, the
scratch `onePic` file is loaded from disk, and it is drawn into that bitmap's
`Graphics` at offset `(xVal, yVal)` — drawn 1:1, with no scaling at this step
(ffmpeg already produced the scaled rect).

**Per-resolution crop/fit (decode size vs canvas).** `SetImage` (lines 1684-2115)
and the two fit-button handlers set six numbers per target resolution:
`wVal/hVal` = ffmpeg decode size (the scaled video rect), `xVal/yVal` = paint
offset into the canvas, `wValSub/hValSub` = canvas size, plus `xValSub/yValSub` =
where the preview canvas is drawn on the control. Selection flags: `is240x240`,
`is320x320`, `is360x360`, `is480x480`, `is640x480`, `is800x480`, `is854x480`,
`is960x540`, `is1600x720`, `is1280x480`, `is1920x462`, plus `isFanZhuan` (flip /
portrait mount) and default 320x240 (lines 66-88).

Example, 480x480 auto-fit branch (lines 1753-1774): the canvas is set to
`wValSub = 480`, `hValSub = 480`, with `xVal` and `yVal` starting at `0`. If the
rotation-adjusted source is taller than wide (`bitAngleH > bitAngleW`), the decode
height is pinned to `480`, the decode width becomes `bitAngleW * 480 / bitAngleH`,
the horizontal leftover is halved into `xVal` (`(480 - wVal) / 2`), and `isTPJCW`
is cleared. Otherwise the decode width is pinned to `480`, the height becomes
`bitAngleH * 480 / bitAngleW`, the vertical leftover is halved into `yVal`
(`(480 - hVal) / 2`), and `isTPJCW` is set.

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

**Rotation** — `buttonXuanzhuan_Click` (lines 894-928): each click advances
`bitAngle` to `(bitAngle + 270) % 360`, then a four-way switch on the new angle
recomputes the working dimensions. At `0` and `180`, `bitAngleW`/`bitAngleH` take
`originalImageW`/`originalImageH` unchanged; at `90` and `270` they take them
swapped (`bitAngleH` = `originalImageW`, `bitAngleW` = `originalImageH`).

So each click rotates by **270° (= −90°)**; at 90/270 the W/H are swapped. `bitAngle`
is passed to ffmpeg as `-display_rotation`.

### 1.3 Playback control (preview loop inside the cutter)

`Timer_event()` (lines 279-345) is the host-driven tick. Three modes:
1. Marker held (`isMouseDown1/2`): after 32 ticks, refresh the single preview frame
   (lines 283-302).
2. Preview playing (`isYulan`): frame cadence gate (lines 306-310) — `yuLanTimer` is
   incremented once per tick, and the handler returns early for as long as
   `yuLanTimer * 15.625` is still below `yuLanCount * 1000.0 / yuLanHz` (both sides
   evaluated as doubles). I.e. tick base is **15.625 ms** and it advances a frame
   when elapsed ≥
   `frameIndex * (1000/yuLanHz)`. Reads `{yuLanCount:0000}.bmp` (line 319), composites
   onto `wValSub x hValSub` at `(xVal,yVal)` (lines 321-327), `yuLanCount++`; missing
   file → reset to frame 1 (lines 334-338).

Preview transcode — `buttonYulan_Click` (lines 1224-1247): decodes the whole
selected clip to `%04d.bmp` and copies `originalImageHz` into `yuLanHz` (line 1243).
The command is `ffmpeg -display_rotation <bitAngle> -ss <startTimerVal>
-t <duration> -i "<source>" -y -r <originalImageHz> -s <wVal>x<hVal> -f image2
"<allPicAddr>%04d.bmp"` — a 4-digit zero-padded BMP sequence in the temp dir.

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
`originalImageHz`; the one at line 1045 is `ffmpeg -display_rotation <bitAngle>
-ss <start> -t <duration> -i "<source>" -y -r <originalImageHz> -s <W>x<H>
-f image2 "<allPicAddr>%04d.bmp"` — the same shape as the preview decode, differing
only in the supersampled `-s` size. Clip re-capped to 300000 ms before decode
(lines 1029-1036).

Re-composite step (lines 1148-1157) allocates a `Bitmap` at the supersampled canvas
size, then draws the decoded frame into it at the supersampled `(xVal,yVal)`. The
canvas width is picked by a nested conditional over the resolution flags: `wValSub * 4`
when `is1600x720` or `is1920x462`; else `wValSub * 2` when `is640x480`, `is800x480`,
`is854x480` or `is960x540`; else `wValSub / 0.375` truncated to int when `is1280x480`;
else plain `wValSub`. The height runs the identical ladder over `hValSub`. A
`Graphics` is taken on that bitmap, the frame is drawn at the same-factor-scaled
`xVal`/`yVal`, and the composited bitmap is passed to `BitmapToByte` for JPEG
encoding.

`BitmapToByte` (lines 939-962) JPEG-encodes each frame: the bitmap is saved into a
`MemoryStream` with `ImageFormat.Jpeg` — no encoder-parameter quality is passed, so
the GDI+ default applies — and the stream's bytes are returned.

**`Theme.zt` binary layout** — written by `ZhuanMaPanDuan` (lines 1132-1201) and,
identically-structured, by `BmpToThemeFile` (lines 964-1019):

| # | Field | Value | `ZhuanMaPanDuan` | `BmpToThemeFile` |
|---|---|---|---|---|
| 1 | header byte | `0xDC` (220) | line 1140 | line 974 |
| 2 | int32 frame count | frames written minus 1 | line 1184 | line 1001 |
| 3 | int32 timestamp array | one entry per frame, `num2 * i` truncated to int, for i = 1..count | lines 1185-1189 | lines 1002-1006 |
| 4 | per frame: int32 length, then that many JPEG bytes | | lines 1190-1195 | lines 1007-1012 |

Per-frame timestamp step `num2`:
- `ZhuanMaPanDuan`: `1000.0 / originalImageHz` (line 1773) — 15/24 fps.
- `BmpToThemeFile`: the literal `41.666666666666664` (line 1212) — fixed **24 fps**
  (1000/24).

Frame count sizing / progress: the expected frame count `num4` is the clip length in
seconds — `(stopTimerVal - startTimerVal) / 1000.0` — multiplied by
`originalImageHz` and truncated to int (line 1138); progress bar `jindu` advances as
frames land (lines 1158-1169). End of ffmpeg output is detected by 100 consecutive
missing-file polls (the poll counter `num3` is compared against `100`,
line 1141 / 975).

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
- `BoFangQiMuLu` — string constant `"\Data\Player\"`, the playback working dir.
- `BoFangQiFile` — string constant `"1.mp4"`, the fixed source filename.
- `ffTimer` — int constant `5` (declared; unused arithmetic constant).
- `ffWaitTimer` — int constant `35`, wait-loop iterations (×100 ms).

ctor (lines 140-147) resolves `boFangQiMuLu` to the app startup path plus
`\Data\Player\` and `boFangQiFile` to that directory plus `1.mp4`, then deletes any
stale `1.mp4` left behind by a previous run.

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

Default framerate field is **16** here (line 96) — the private int field
`originalImageHz` is initialised to `16` (vs 15 in `UCVideoCut`).

`FFmpeg_Video_Bmp()` (lines 985-1033) decodes ONE 5-second chunk into the current
back-buffer dir. Chunk math (lines 1006-1017): the decode start is the current
`nowStopTimerVal` and the tentative end is `nowStopTimerVal + 5000`. If that end
reaches or passes `originalTimerLen`, the decode duration becomes the remainder
(`originalTimerLen - nowStopTimerVal`) and `nowStopTimerVal` resets to `0` (wrapping
to the top of the clip); otherwise the duration is a fixed `4875` ms — 125 ms
shorter than the 5000 ms step — and `nowStopTimerVal` advances by `5000`.

ffmpeg command (line 1027): `ffmpeg -ss <start> -t <duration> -i "<source>" -y
-r <originalImageHz> -s <wVal>x<hVal> -f image2 "<boFangQiMuLu><dirCount>\%04d.bmp"`
— frames land as a 4-digit zero-padded BMP sequence inside the numbered back-buffer
directory (`1\` or `2\`).

NOTE: no `-display_rotation` here (playback control has no rotate button); rotation
is expressed only through the fit block's `isFanZhuan` W/H swap.

### 2.3 Playback control — frame cadence, VideoCount / double-buffer

`Timer_Get_Image()` (lines 906-983) is the per-frame pump; returns `imageSub`.
- Reads `{nowDir}\{dirVal:0000}.bmp` (line 1108), composites onto a fresh
  `wValSub x hValSub` bitmap at `(xVal,yVal)` (lines 917-923), swaps it into
  `imageSub`.
- Advance (lines 932-933): the frame index `dirVal` increments by one and
  `nowTimerVal` advances by `62.5` — **62.5 ms/frame = 16 fps**.
- Loop-to-start when the clip time is reached (lines 934-937): once `nowTimerVal`
  reaches `originalTimerLen` it is reset to `0.0`.
- **Chunk-boundary / buffer swap** when the next bmp is missing (lines 939-961): for
  any frame index other than `81`, the clock wraps to `0.0` only once `nowTimerVal`
  reaches `originalTimerLen - 125`; at index `81` exactly, the wrap threshold is
  `originalTimerLen - 100` instead. Either way `nowDir` then flips between `1` and
  `2` (switching which directory is read) and `dirVal` restarts at `1`.
  Frame index `81` is the boundary (≈ 5000 ms × 16 fps + 1 = 80 frames per chunk).
- **Trigger next decode** into the other buffer (lines 962-973): if `dirCount` still
  equals the freshly-selected `nowDir`, `dirCount` flips between `1` and `2` and
  `FFmpeg_Video_Bmp()` is called, so the decoder always fills the buffer that
  playback is not currently reading.
- Seek-bar UI update when not dragging (lines 974-980): updates `labelNowTimer`,
  progress width `bitmapJDW = LongToWidth(num)`.

`Player()` (lines 1035-1041) → `button1_Click`. `button1_Click` (lines 1049-1094)
toggles play/pause; on first start it primes buffer 1 and waits up to `35 × 100 ms`
for `1\0001.bmp` (lines 1084-1091): a loop of 35 iterations, each sleeping 100 ms on
the calling thread and breaking out as soon as `<boFangQiMuLu>1\0001.bmp` exists on
disk.

`ClosePlayer()` (lines 1043-1047) clears the `isStart` flag.

**Fit-adjust buttons** `buttonTPJCH_Click` (lines 1096-1360) / `buttonTPJCW_Click`
(lines 1362-1626) re-run the per-resolution fit then re-decode the current chunk
(same 35×100 ms prime, e.g. lines 1351-1358). Seek by dragging the progress bar:
`UCBoFangQiKongZhi_MouseDown/Move/Up` (lines 1628-1725); on mouse-up it moves
`nowTimerVal` to the dragged position `jdtTimerVal`, resets both buffers, decodes,
and if the first bmp never appears within 35 tries it falls back to time 0
(lines 1672-1725).

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
5. **Rotation click is −90° (adds 270 mod 360).** Each click advances `bitAngle` to
   `(bitAngle + 270) % 360` (line 899), fed to ffmpeg as `-display_rotation`.
   At 90/270 `bitAngleW/H` are
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
