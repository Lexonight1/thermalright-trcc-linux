# BEHAVIOR — Video subsystem, per-method grind

<!-- audit-state: origin=2.0.3.0 addresses=2.1.6.0 known-bad=none -->
> **Audited against TRCC 2.0.3; citations re-anchored to TRCC 2.1.6.**
> 13 method(s) documented here changed in TRCC 2.1.6 and have NOT been re-read: `FFmpeg_Video_Bmp`, `GetOneImage`, `InitializeComponent`, `LongToTimer`, `SetImage`, `Timer_event`, `UCBoFangQiKongZhi`, `UCVideoCut`, `UCVideoCut_LongToTimer`, `ZhuanMaPanDuan`, `buttonTPJCH_Click`, `buttonTPJCW_Click` (+1 more) — read those entries as TRCC 2.0.3 history.
> [`AUDIT_INDEX.md`](AUDIT_INDEX.md#provenance)
<!-- /audit-state -->

Exhaustive per-method annotation of the three video UserControls. **Every method
is covered** (52/52). This is the method-level companion to the prose in
`AUDIT_VIDEO.md` (which explains the ffmpeg pipeline, the `Theme.zt` layout, and
the per-resolution magic numbers as a subsystem) — read that for the "why", read
this for the "what each method does". Paths relative to
`/home/ignorant/Downloads/TRCCCAPEN/TRCC_decompiled/TRCC.DCUserControl/`.

Coverage (from `audit_coverage.py --dark`): UCVideoCut 28/28, UCBoFangQiKongZhi
21/21, UCDongHuaLianDong 3/3.

Driving-variable legend (shared across both video controls unless noted):
- `bitAngleW/bitAngleH` — source video W/H after rotation (swapped at 90/270).
- `wVal/hVal` — ffmpeg decode `-s` size (the scaled video rect).
- `xVal/yVal` — paint offset of the decoded frame inside the canvas.
- `wValSub/hValSub` — canvas (target panel) size.
- `xValSub/yValSub` — where the preview canvas is drawn on the control (UCVideoCut only).
- `isTPJCW` — fit-by-Width (`true`) vs fit-by-Height (`false`).
- `isFanZhuan` — portrait/flipped mount (swaps sub-canvas W/H per resolution).
- `is<WxH>` flags — exactly one true = the target panel resolution; else default 320×240.
- `originalImageHz` — output framerate. UCVideoCut default 15 (user 15/24); UCBoFangQiKongZhi fixed 16.

---

## 1. UCVideoCut.cs — video clip/crop/rotate/transcode → `Theme.zt` (28 methods)

- `UCVideoCut` ctor (UCVideoCut.cs:216) — calls `InitializeComponent`, loads the two clip-marker bitmaps (`P剪辑块a`/`P剪辑块b`) + progress bar (`P进度条`), sets temp paths `onePic=Data\Temp\0.png` / `allPicAddr=Data\Temp\`, sets `labelInfo` text; key branches: `Form1.Language==1`→"正在渲染", else→"Rendering".
- `OnPaint` (UCVideoCut.cs:243) — draws the timeline + live preview; key branches: `jindu!=0`→draw progress bar `bitmapJindu` clipped to width `jindu`; else→draw both clip markers; then `imageSub!=null`→draw preview at `(xValSub,yValSub)`.
- `KillFFmpegYulan(bool kill=true)` (UCVideoCut.cs:262) — stops preview: `isYulan=false`, resets Yulan button image; key branches: `!kill`→return early (no process kill); else→enumerate `Process.GetProcesses()` and `Kill()` every `ffmpeg`. **[COPY-PASTE]** (ffmpeg-kill loop reappears in `SetImage`, `FFmpeg_Video_Bmp`, `SetNewFile`).
- `Timer_event()` (UCVideoCut.cs:287) — host-driven per-tick pump (3 modes); key branches: `isMouseDown1`→count to 32 ticks then `GetOneImage(labelStartTimer)`; `isMouseDown2`→same with stop marker; `isYulan`→cadence gate `yuLanTimer*15.625 < yuLanCount*1000/yuLanHz` returns until due, then load `{yuLanCount:0000}.bmp` composite→`imageSub`, `yuLanCount++`; missing file→reset `yuLanCount=1,yuLanTimer=0`; not previewing→reset counters. Tick base **15.625 ms**. **[per-tick — DEBUG-equivalent]**.
- `buttonClose_Click` (UCVideoCut.cs:355) — cancel; key branches: `!isZhuanma`→`KillFFmpegYulan()` + invoke delegate with `null` (host closes editor); if transcoding, ignored.
- `buttonTPJCH_Click` (UCVideoCut.cs:356) — **fit-to-HEIGHT** button: sets canvas + decode rect so the video fills panel height, `isTPJCW=false`; key branches: guarded by `isZhuanma`→return; then a 13-way cascade on `is240x240/320x320/360x360/480x480/1600x720/800x480/854x480/960x540/1920x462/1280x480`, each widescreen branch splitting on `isFanZhuan`, else default 320×240; formula `wVal=bitAngleW*H/bitAngleH`, `xVal+=(subW-wVal)/2`. Tail: `isMouse1`→`GetOneImage(start)` / `isMouse2`→`GetOneImage(stop)`. **[GOD][COPY-PASTE]** (near-identical cascade in `buttonTPJCW_Click`, `SetImage`, and both UCBoFangQiKongZhi fit handlers).
- `buttonTPJCW_Click` (UCVideoCut.cs:625) — **fit-to-WIDTH** button, mirror of above with `isTPJCW=true`; formula `hVal=bitAngleH*W/bitAngleW`, `yVal+=(subH-hVal)/2`; same 13-way `is<WxH>`×`isFanZhuan` cascade + same `isMouse1/2`→`GetOneImage` tail. **[GOD][COPY-PASTE]**.
- `buttonXuanzhuan_Click` (UCVideoCut.cs:1134) — rotate button; key branches: guarded `!isZhuanma`; `bitAngle=(bitAngle+270)%360` (**each click = −90°**); switch on `bitAngle`: 90/270→swap `bitAngleW↔bitAngleH` from originals, 0/180→identity; then re-fit via `isTPJCW`→`buttonTPJCW_Click(null,null)` else `buttonTPJCH_Click(null,null)`. `bitAngle` fed to ffmpeg as `-display_rotation`.
- `ResetAllTempFile()` (UCVideoCut.cs:1170) — wipes `allPicAddr`; key branches: `Directory.Exists`→recursive delete, then always recreate. (No retry, unlike the UCBoFangQiKongZhi variant.)
- `BitmapToByte(Bitmap Bit)` (UCVideoCut.cs:1179) — JPEG-encodes a bitmap to `byte[]` via `MemoryStream` + `ImageFormat.Jpeg`; key branches: retry-loop `flag` — on exception `Thread.Sleep(100)`+`DoEvents()` and retry (never gives up). Note `GetBuffer()` returns the *padded* stream buffer, not `ToArray()`.
- `BmpToThemeFile()` (UCVideoCut.cs:1204) — writes `Theme.zt` from already-decoded `%04d.bmp` frames (used when frames are pre-generated, not by the OK path); layout: `0xDC` header, then poll frames until 100 consecutive misses (`num3<100`), JPEG each via `BitmapToByte`; then `int32 frameCount(num-1)`, `int32[] timestamps = (int)(41.6667*i)` (**hard 24 fps** step, differs from `ZhuanMaPanDuan`), then per-frame `int32 len + jpeg`. key branch: file exists→encode+delete+`num3=0`; else→`num3++`. **[COPY-PASTE]** (writer duplicated in `ZhuanMaPanDuan`).
- `ZhuanMaPanDuan(bool isFF)` (UCVideoCut.cs:1021) — **the real transcode** → `Theme.zt`; key branches (supersample decode select): `is1600x720||is1920x462`→decode `wVal*4 × hVal*4`; `is1280x480`→`wVal/0.375 × hVal/0.375` (×2.6667); `is640x480||is800x480||is854x480||is960x540`→`×2`; `isFF` (else, small panels)→native `wVal × hVal`. Each clamps clip to 300000 ms, runs the `-display_rotation {bitAngle} -ss -t -i -r {Hz} -s WxH` ffmpeg via cmd.exe. Then re-composite loop: build canvas at matching supersample (`wValSub*4/×2//0.375`), `DrawImage` at scaled `(xVal,yVal)`, `BitmapToByte`→arraylist, progress `jindu`. Writer: `0xDC`, `frameCount`, `timestamps=(int)((1000/originalImageHz)*i)` (**Hz-driven step**), `len+jpeg` per frame. `num4` = expected frame count for progress; end at 100 misses. **[GOD][COPY-PASTE]** — the 4 ffmpeg branches are cut-and-paste differing only in `-s`; the ternary supersample expression is written 4× inline.
- `buttonTPJCOK_Click` (UCVideoCut.cs:1485) — OK/confirm orchestrator; key branches: `!isZhuanma`→set `isZhuanma=true`, `flag=!isYulan` (reuse already-decoded bmps if preview ran), `KillFFmpegYulan(flag)`, show `labelInfo`, `ZhuanMaPanDuan(flag)`, invoke delegate with ArrayList[`allPicAddr+"Theme.zt"`], hide label, `isZhuanma=false`.
- `buttonYulan_Click` (UCVideoCut.cs:1861) — start in-editor preview; key branches: `!isZhuanma && !isYulan`→reset temp, run `-display_rotation -ss -t -i -r {Hz} -s {wVal}x{hVal}` decode to `%04d.bmp`, set `yuLanHz=originalImageHz`, `isYulan=true`, swap button image. (Consumed by `Timer_event` isYulan branch.)
- `UCVideoCut_MouseTimer(int val)` (UCVideoCut.cs:1539) — pixel→time: maps X in 480-px track (`(val-9)*originalTimerLen/480`) to a ms position. No branches.
- `UCVideoCut_TimerMouse(long timer)` (UCVideoCut.cs:1546) — time→pixel inverse: `round(timer/originalTimerLen*480 + 9)`. No branches.
- `UCVideoCut_TimerToLong(string timer)` (UCVideoCut.cs:1552) — parses `HH:MM:SS.mmm` → ms long; splits on `:`, fractional seconds via `Convert*Pow(10,-len)`, `round(,2)*1000`. No branches (throws on malformed → caught by callers).
- `UCVideoCut_LongToTimer(long val)` (UCVideoCut.cs:1273) — ms long → `HH:MM:SS.mmm` string via successive `%1000/%60/%60`. No branches.
- `GetOneImage(string timer)` (UCVideoCut.cs:1948) — extract ONE preview frame at `timer`; key branches: reentrancy guard `isGetOneImage`→return; clamp seek `num+1000>originalTimerLen`→`num=len-1000`; run `-display_rotation {bitAngle} -ss {timer} -i -s {wVal}x{hVal} -frames:v 1`; wait for `onePic`, `num2>=100`→give up (`isGetOneImage=false`); then composite decoded frame onto `wValSub×hValSub` canvas at `(xVal,yVal)`→`imageSub`, invalidate.
- `UCVideoCut_MouseDown` (UCVideoCut.cs:1635) — start dragging a clip marker; key branches: `isZhuanma`→return; Y outside `[546,592]`→return; clamp X to `[9,489]`; near marker1 (`|x-cx1|<15 && |y-cy1|<15`)→`isMouseDown1=true`, move start, clamp `cx1<cx2`, cap clip to 300000 ms (push stop); near marker2→`isMouseDown2=true`, move stop, cap clip (pull start). Updates start/stop/duration labels.
- `UCVideoCut_MouseMove` (UCVideoCut.cs:1698) — drag marker; key branches: `isMouseDown1 && !isMouseMove`→reentrancy-guard `isMouseMove`, clamp X `[9,489]`, set `cx1` (min `cx2-1`), recompute `startTimerVal`, cap 300000 (push stop); `isMouseDown2 && !isMouseMove`→mirror for stop (pull start). `isMouseMove` set/cleared inside to drop reentrant ticks.
- `UCVideoCut_MouseUp` (UCVideoCut.cs:1763) — release marker; key branches: `isMouseDown1`→`isMouse1=true,isMouse2=false`, reset temp, `GetOneImage(start)`; `isMouseDown2`→`isMouse1=false,isMouse2=true`, reset temp, `GetOneImage(stop)`; always clear all mouse flags. (`isMouse1/2` records which marker to refresh in the fit handlers.)
- `SetImage(string name)` (UCVideoCut.cs:2152) — load a new video: probe → parse → per-resolution fit → first-frame preview; key branches: guard `isZhuanma`→return; `KillFFmpegYulan`; ffmpeg `-i` to `log.txt`, parse `Duration:`→`originalTimerLen`, cap stop to 300000; `Video: av1`→messagebox reject; else parse `Video:` section, `GetVideoRatio`→W/H/`bitAngle=0`/`isGetVideoInfo`; parse-fail→localized reject box; `originalImageW*originalImageH>8294400` (4K)→reject; `!isGetVideoInfo`→invoke delegate `null` + return. Then the **51-branch per-resolution auto-fit cascade** (`is<WxH>`×`isFanZhuan`, orientation decided by aspect thresholds: 1600x720=0.45, 800x480=0.6, 854x480=0.56206, 960x540=0.5625, 1920x462=77/320, 1280x480=0.375, default=0.75) computing wVal/hVal/xVal/yVal/wValSub/hValSub/xValSub/yValSub/isTPJCW. Finally decode first frame (`-s {wVal}x{hVal} -ss {startTimerVal} -frames:v 1`)→composite→`imageSub`. **[GOD][COPY-PASTE]** — largest method; the fit cascade is the same data as the two fit-button handlers, just gated by an aspect threshold instead of a forced axis.
- `GetVideoRatio(char[] chs)` (UCVideoCut.cs:2638) — scans char array for the first `NxN` resolution token; key branches: 4 sequential passes in priority order — `4x4` (`DDDDxDDDD`), then `4x3` (`DDDDxDDD`), then `3x4` (`DDDxDDDD`), then `3x3` (`DDDxDDD`); returns the matched char[] or null. **[COPY-PASTE]** — byte-identical to UCBoFangQiKongZhi.GetVideoRatio.
- `button1_Click` (UCVideoCut.cs:2712) — framerate 15 fps: select radio `P点选框A` on button1 / `P点选框` on button2, `originalImageHz=15`.
- `button2_Click` (UCVideoCut.cs:2719) — framerate 24 fps: select radio on button2, `originalImageHz=24`.
- `Dispose(bool disposing)` (UCVideoCut.cs:2726) — WinForms teardown: `disposing && components!=null`→`components.Dispose()`. **[boilerplate]**.
- `InitializeComponent()` (UCVideoCut.cs:2735) — WinForms designer layout: 6 buttons (OK/rotate/W-fit/H-fit/preview/close), 2 fps radios, 5 labels; wires `Click` + `MouseDown/Move/Up`; control `500×702`, bg `P0裁减320320`; markers/timeline coordinates. No behavior logic. **[boilerplate]**.

---

## 2. UCBoFangQiKongZhi.cs — device playback controller for `Data\Player\1.mp4` (21 methods)

Streams decoded frames to the host via `Timer_Get_Image()` using a **double-buffered**
ffmpeg pipeline (dirs `1\` and `2\`, 5-second chunks). Returns a `Bitmap imageSub`;
does NOT touch the wire. See `AUDIT_VIDEO.md §2` for the buffer-swap state machine.

- `UCBoFangQiKongZhi` ctor (UCBoFangQiKongZhi.cs:134) — `InitializeComponent`, resolve `boFangQiMuLu=Data\Player\` + `boFangQiFile=…\1.mp4`, delete any stale `1.mp4`, load progress bar `P进度条`; key branch: `boFangQiFileExist`→try-delete old file.
- `OnPaint` (UCBoFangQiKongZhi.cs:170) — draws only the seek progress bar; key branch: `bitmapJDW>0`→draw `bitmapJindu` clipped to `bitmapJDW`. (No preview draw — host owns the frame.)
- `TimerToLong(string timer)` (UCBoFangQiKongZhi.cs:180) — `HH:MM:SS.mmm`→ms long. No branches. **[COPY-PASTE]** — identical to `UCVideoCut_TimerToLong`.
- `LongToTimer(long val)` (UCBoFangQiKongZhi.cs:181) — ms long→`HH:MM:SS.mmm`. No branches. **[COPY-PASTE]** — identical to `UCVideoCut_LongToTimer`.
- `LongToWidth(long timer)` (UCBoFangQiKongZhi.cs:208) — ms→seek-bar pixel width `round(timer/originalTimerLen*479)`. No branches.
- `GetVideoRatio(char[] chs)` (UCBoFangQiKongZhi.cs:214) — same 4-pass `NxN` scanner (4x4/4x3/3x4/3x3). **[COPY-PASTE]** — byte-identical to UCVideoCut.
- `SetImage(bool bl=false)` (UCBoFangQiKongZhi.cs:272) — probe + parse + per-resolution fit (no preview render); key branches: `bl`→reset buffer indices (`dirVal=1,dirCount=0,nowDir=1`); reentrancy guard `dirCount!=0`→return; reset both temp dirs; `!File.Exists(boFangQiFile)`→return; ffmpeg `-i`→`log.txt`, `Duration:`→`originalTimerLen`; `Video: av1`→reject box; `GetVideoRatio`→W/H/`isGetVideoInfo`; parse-fail→reject box; 4K cap `>8294400`→reject; `!isGetVideoInfo`→return. Then the **per-resolution fit cascade** (`is<WxH>`×`isFanZhuan`, same aspect thresholds as UCVideoCut) but **sub-canvas = NATIVE panel size** (e.g. 1600×720, not the 400×180 *display* rect UCVideoCut uses) — this control returns full-res frames for the host to encode. **[GOD][COPY-PASTE]**.
- `ResetAllTempFile(string allPicAddr)` (UCBoFangQiKongZhi.cs:1078) — wipe+recreate a dir; key branches: `Directory.Exists`→delete; on exception `Thread.Sleep(200)` + retry-delete; second failure→`Console.WriteLine("catch")` swallow; always recreate. (Takes a path arg + has retry, unlike UCVideoCut's no-arg version.)
- `Timer_Get_Image()` (UCBoFangQiKongZhi.cs:1102) — **per-frame pump**, returns `imageSub`; key branches: `!isStart`→return current `imageSub`; frame file `{nowDir}\{dirVal:0000}.bmp` exists→composite onto `wValSub×hValSub` at `(xVal,yVal)`, `dirVal++`, `nowTimerVal+=62.5` (**16 fps**), wrap at `originalTimerLen`; **missing file (chunk boundary)**→wrap-tail (`dirVal!=81`→wrap at len-125; ==81→wrap at len-100), **flip `nowDir` 1↔2** (read the other buffer), `dirVal=1`; **decode trigger** `dirCount==nowDir`→flip `dirCount` + `FFmpeg_Video_Bmp()` (fill the buffer we're NOT reading); seek-UI update when `!isMouseDown`. Frame index **81** = chunk length (≈80 frames/5 s @16 fps). **[per-tick — DEBUG-equivalent]**.
- `FFmpeg_Video_Bmp()` (UCBoFangQiKongZhi.cs:1225) — decode ONE 5-second chunk into `dirCount\`; key branches: kill any running `ffmpeg`; `isGetVideoInfo`→reset `dirCount\` dir, compute chunk window (`num=nowStopTimerVal+5000`; `>=originalTimerLen`→last partial `val2=len-start`, reset `nowStopTimerVal=0`; else `val2=4875`, `nowStopTimerVal+=5000`), run `-ss -t -i -r {Hz} -s {wVal}x{hVal}` to `%04d.bmp` (**no `-display_rotation`** — this control has no rotate button). **[COPY-PASTE]** ffmpeg-kill loop.
- `Player()` (UCBoFangQiKongZhi.cs:1240) — public start; key branch: `isGetVideoInfo && !isStart`→`button1_Click(null,null)`.
- `ClosePlayer()` (UCBoFangQiKongZhi.cs:1248) — stop: `isStart=false`, reset button image to `P0播放`. No branches.
- `button1_Click` (UCBoFangQiKongZhi.cs:1254) — play/pause toggle; key branches: `!isGetVideoInfo`→"format not supported" box + return; `isStart`→pause (`isStart=false`, play icon); else→pause icon, if `dirCount==0` prime buffer1 (`dirVal=1,dirCount=1,nowDir=1`, `Sleep(100)`, `FFmpeg_Video_Bmp()`, wait ≤35×100 ms for `1\0001.bmp`), `isStart=true`.
- `buttonTPJCH_Click` (UCBoFangQiKongZhi.cs:1096) — fit-to-HEIGHT; key branches: `!isTPJCW`→return (already H-fit); save `isStart`, pause; 13-way `is<WxH>`×`isFanZhuan` cascade setting native sub-canvas + `wVal=bitAngleW*subH/bitAngleH`, `isTPJCW=false`; then re-prime buffer from `nowTimerVal` (35×100 ms wait), restore `isStart`. **[GOD][COPY-PASTE]**.
- `buttonTPJCW_Click` (UCBoFangQiKongZhi.cs:2026) — fit-to-WIDTH mirror; key branches: `isTPJCW`→return; same cascade with `hVal=bitAngleH*subW/bitAngleW`, `isTPJCW=true`; re-prime + restore. **[GOD][COPY-PASTE]**.
- `UCBoFangQiKongZhi_MouseDown` (UCBoFangQiKongZhi.cs:2033) — begin seek-drag; key branches: `e.Y < button1.Top`→`isMouseDown=true`, clamp X `[10,489]`, `bitmapJDW=x-10`, `jdtTimerVal=bitmapJDW*originalTimerLen/479`, update now-time label.
- `UCBoFangQiKongZhi_MouseMove` (UCBoFangQiKongZhi.cs:2055) — seek-drag; key branch: `isMouseDown && !isMouseMove`→reentrancy-guard, clamp X `[10,489]`, recompute `bitmapJDW`/`jdtTimerVal`, update label.
- `UCBoFangQiKongZhi_MouseUp` (UCBoFangQiKongZhi.cs:2077) — commit seek; key branches: `isMouseDown`→pause; `isGetVideoInfo`→reset buffers, `nowTimerVal=jdtTimerVal` (clamp `+500>len`→`len-500`), decode chunk, wait ≤35×100 ms; **fallback** `i==35` (frame never appeared)→seek to time 0 and re-decode; then `isStart=true`, pause icon; `!isGetVideoInfo`→stop + play icon.
- `SetNewFile(string name)` (UCBoFangQiKongZhi.cs:2132) — swap the source file; key branches: stop, kill running `ffmpeg`, `Sleep(100)`, `boFangQiFile=name`, `SetImage(bl:true)`, `isGetVideoInfo`→auto-play via `button1_Click`. **[COPY-PASTE]** ffmpeg-kill loop.
- `Dispose(bool disposing)` (UCBoFangQiKongZhi.cs:2159) — WinForms teardown. **[boilerplate]**.
- `InitializeComponent()` (UCBoFangQiKongZhi.cs:2168) — designer layout: play + W-fit + H-fit buttons, 2 labels (all/now timer), control `500×56`, bg `P0播放器控制`; wires `Click` + `MouseDown/Move/Up`. **[boilerplate]**.

---

## 3. UCDongHuaLianDong.cs — animation-linkage panel (designer-only stub, 3 methods)

Entire file is field declarations + ctor + `Dispose` + `InitializeComponent`. **No
event handlers, no logic** — the host attaches behaviour after construction. Layout:
on/off toggle `buttonOnOff`, 6-way metric selector `buttonM1..M6` (3×2 grid, M1
pre-selected), 2 numeric threshold `textBox1/2` (MaxLength 4, default "0"), 3
animation slots each with preview/load/cloud (`buttonYL/XZ/WL 1..3`). Control
`682×84`, bg `P01动画联动`. See `AUDIT_VIDEO.md §3`.

- `UCDongHuaLianDong` ctor (UCDongHuaLianDong.cs:48) — calls `InitializeComponent` only. No branches.
- `Dispose(bool disposing)` (UCDongHuaLianDong.cs:53) — WinForms teardown; `disposing && components!=null`→`components.Dispose()`. **[boilerplate]**.
- `InitializeComponent()` (UCDongHuaLianDong.cs:62) — pure designer layout of the 17 named controls; **no `Click`/handler wiring at all** (contrast with the two video controls). No behavior logic. **[boilerplate]**.

---

## 4. Consolidation targets (for the Linux port)

1. **The per-resolution fit cascade — 6 copies** (`GOD`+`COPY-PASTE`): UCVideoCut
   {`SetImage`, `buttonTPJCH_Click`, `buttonTPJCW_Click`} + UCBoFangQiKongZhi
   {`SetImage`, `buttonTPJCH_Click`, `buttonTPJCW_Click`}. All compute the same
   6-tuple (wVal/hVal/xVal/yVal + sub-canvas) from `is<WxH>`, `isFanZhuan`, and an
   axis (forced by button, or aspect-threshold-chosen in `SetImage`). Collapse to
   ONE data-driven `fit(resolution, fanzhuan, source_wh, axis|auto)` — a per-panel
   table of `(canvas_w, canvas_h, display_w, display_h, aspect_threshold)` rows +
   one letterbox formula. This is the single biggest win and the highest-risk
   transcription surface (widescreen thresholds are raw magic doubles).
2. **`Theme.zt` writer — 2 copies** (`ZhuanMaPanDuan` + `BmpToThemeFile`): identical
   `0xDC` header + frameCount + int32 timestamps + len/jpeg loop, differing ONLY in
   the timestamp step (Hz-driven vs hard 41.6667/24 fps). One `ZtWriter(frames, fps)`.
3. **The 4-way supersample ffmpeg block inside `ZhuanMaPanDuan`** (`GOD`): four
   cut-and-paste `Process`/ffmpeg launches differing only in `-s`, plus the
   supersample factor written as an inline ternary 3× (canvas, x, y). Extract one
   `decode(scale_factor)` + a `supersample_for(resolution)` lookup (×4 / ×2.6667 /
   ×2 / ×1).
4. **`GetVideoRatio` + `TimerToLong`/`LongToTimer`** — byte-identical across both
   controls. Move to one shared video-probe/time helper (also folds in the shared
   `ffmpeg -i` probe: Duration parse, `Video: av1` reject, 4K `>8294400` cap, which
   are duplicated in both `SetImage`s).
5. **The ffmpeg-process-kill loop** (`COPY-PASTE`, ~5 sites: `KillFFmpegYulan`,
   `FFmpeg_Video_Bmp`, `SetNewFile`, and inline in probes) — one `kill_ffmpeg()`.
   On Linux this is a single `pkill`/subprocess concern, not per-call-site.

---

## 5. Undetermined / needs a second source

- **Who calls `Timer_event()` / `Timer_Get_Image()` and at what interval.** Both are
  public host-pumped methods with no timer in these files. The 15.625 ms (UCVideoCut)
  vs 62.5 ms/frame (UCBoFangQiKongZhi) cadences imply the host ticks fast and the
  method self-gates; the exact host `QTimer`/`System.Windows.Forms.Timer` interval
  lives in `FormCZTV.cs` (out of scope). Confirm there before choosing the Linux tick.
- **`BmpToThemeFile` call site.** It's `public` and writes `Theme.zt` from pre-decoded
  bmps at a fixed 24 fps, but nothing in these three files invokes it — the OK path
  uses `ZhuanMaPanDuan`. Its caller (and whether it's dead in 2.1.6) is in the host.
- **`isBiliPingmu`** — declared in both controls, never read in either. Purpose (a
  "bilibili screen"? a bili-ratio flag?) undetermined; check `FormCZTV` usage.
- **`imageSub` handoff to the wire.** These controls stop at a `Bitmap`; the RGB565 /
  `mode==48` encode that consumes it is in `FormCZTV.cs` — not verifiable here.

Confidence: **high** on every method's control flow and the driving variables (full
source read, all 52 methods, all branches traced). **Medium** only on the four
cross-file handoff/caller questions in §5, which genuinely live outside these files.
