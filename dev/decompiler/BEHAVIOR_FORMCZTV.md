# BEHAVIOR — `TRCC.CZTV/FormCZTV.cs` (per-method audit)

Exhaustive per-method behavioral annotation of the LCD host form, driving the
hexagonal consolidation. Source:
`~/Downloads/TRCCCAPEN/TRCC_decompiled/TRCC.CZTV/FormCZTV.cs` (7218 lines).

The LCD compose→rotate→wire pipeline methods (`FormCZTVInit`, `ImageToJpg`,
`ImageTo565`, `Timer_event`, `GenerateImage`) are documented in
`AUDIT_LCD_PIPELINE.md` — **not re-documented here**. This file covers the 76
remaining (UI-glue, theme I/O, web/mask download, DC read/write, memory, device
callback) methods flagged DARK by `audit_coverage.py`.

`**[GOD]**` = huge multi-responsibility method to split. `**[COPY-PASTE]**` =
per-resolution / per-product / per-language duplicated block to fold into a
lookup keyed on the profile.

---

## Lifecycle / construction

- `GetScreenScalingFactor` (FormCZTV.cs:441) — returns the Windows DPI scale as `GetDeviceCaps(hdc, DESKTOPVERTRES=117) / Screen.PrimaryScreen.Bounds.Height`; used as `sfblJP` for the eyedropper/screenshot popups. No branches.
- `CheckDirectoryExist` (FormCZTV.cs:449) — creates `GifDirectory` (`…\Data\USBLCD\Theme`) if absent; key branch: `Directory.Exists → skip/create`.
- `FormCZTVLanguageSet` (FormCZTV.cs:457) — **[COPY-PASTE]** assigns every panel's `BackgroundImage`/`imageBk` from a localized `Resources.*` set (12 assignments) selected by `Form1.Language`; key branches: `Language∈{1..8}→zh/tc/d/e/f/p/r/x resource suffix`, else `en`. Nine near-identical 12-line blocks that differ only by suffix — collapse to `assets.localized(name, lang)`.
- `FormCZTV` ctor (FormCZTV.cs:605) — builds the form: `InitializeComponent`, caches `sfblJP`, zeroes flat-button borders, constructs `FormScreenshot`/`FormGetColor`, **wires every child-panel delegate** (`ThemeLocal`/`ThemeWeb`/`ThemeSetting`/`ThemeMask`, the three `UpDateUCComboBox*`, `UpDateUCImageCut`/`UpDateUCVideoCut`, `UpDateFormUCScreenImage`), seeds the two rotation combos `{0°,90°,180°,270°}` + the temp-unit combo `{℃,℉}`, sets `GifDirectory`, allocates `imageArray`/`themeArray`, hides the popup forms. No branches — this is the composition root of the form's callback graph.
- `FormCZTVRemove` (FormCZTV.cs:652) — teardown on device detach: stops the timer, disposes `gifPicture`/`imagePicture`/`bitmapMB`/`bitmapBGK`, clears the two ArrayLists, and calls each child panel's `*Remove()` + `Dispose()`; key branches: null-guards before each `Dispose`.
- `Dispose` (FormCZTV.cs:6785) — standard WinForms designer dispose; branch: `disposing && components != null → components.Dispose()`.
- `InitializeComponent` (FormCZTV.cs:6794) — designer-generated widget tree construction (control instantiation, bounds, event hookups). No logic branches; not ported (Qt owns layout).

## Memory management (mostly dead)

- `ClearMemoryMy` (FormCZTV.cs:1093) — **empty body** (the `SetProcessWorkingSetSize` trim was compiled out); called all over as a no-op. Do NOT port.
- `ClearMemorySelf` (FormCZTV.cs:1097) — **empty body**, no-op. Do NOT port.
- `ClearMemoryAll` (FormCZTV.cs:1101) — real: `GC.Collect` + `WaitForPendingFinalizers`, then `EmptyWorkingSet` on **every** process on the machine (try/catch per process). Aggressive and global; a Linux port would drop this entirely.

## Popup-form delegate callbacks

- `UpDateFormUCScreenImage` (FormCZTV.cs:1063) — screen-image widget callback; branch: `mode==0 → detach the preview control from `ucScreenImageBK1`, reparent into `formScreenImage`, `isBiliPingmu=false`, `SetMyUCScreenImage(directionB)`, show the popup`; else `ucXiTongXianShi1.UCXiTongXianShiSelect(count)` (metric-field pick).
- `UpDateFormScreenImage` (FormCZTV.cs:1079) — inverse of above: reparent the preview control back into `ucScreenImageBK1`, `isBiliPingmu=true`, re-`SetMyUCScreenImage(directionB)`. No branches.
- `UpDateFormScreenshot` (FormCZTV.cs:1119) — screenshot-crop popup callback; branch: `mode==0 → set `myYcbk=true` + update the hide-bg toggle`; else write the returned `x`/`y` into the projection X/Y textboxes.
- `UpDateFormGetColor` (FormCZTV.cs:1133) — eyedropper callback; key branches: `mode 0 → commit picked color to the color panel + restore pipette icon`; `mode 1 → if `isGetRGB`, live-preview the magnified swatch bitmap`; `mode 2 → cancel (restore pipette icon, clear `isGetRGB`)`.

## Top-bar buttons / window drag

- `buttonPower_Click` (FormCZTV.cs:1158) — `delegateForm?.Invoke(255)` (app-level power/close command). No branch.
- `buttonPower_MouseEnter` (FormCZTV.cs:1163) / `buttonPower_MouseLeave` (FormCZTV.cs:1168) — swap the power button's hover/default background image. No branches.
- `FormCZTV_MouseDown` (FormCZTV.cs:1173) — `delegateForm?.Invoke(241,…,e)` (begin window drag) + collapse all three combo dropdowns. No branch.
- `FormCZTV_MouseMove` (FormCZTV.cs:1181) — `delegateForm?.Invoke(242,…,e)` (drag move). No branch.
- `FormCZTV_MouseUp` (FormCZTV.cs:1186) — `delegateForm?.Invoke(243,…,e)` (drag end). No branch.
- `buttonHelp_Click` (FormCZTV.cs:6742) — `Process.Start(StartupPath\LCDHelp.pdf)` in try/catch. No branch.

## Left-nav mode tabs

- `ButtonNewMode` (FormCZTV.cs:1191) — resets all four nav buttons to their default image, then per `mode` highlights one button + shows its panel and hides the other three; branches: `mode 1→Local, 2→Web(云端背景), 3→Setting, 4→Mask(云端主题)`.
- `buttonBDZT_Click` (FormCZTV.cs:1230) — `myModeUC=1; ButtonNewMode(1)` (local themes). No branch.
- `buttonYDZT_Click` (FormCZTV.cs:1236) — cancel carousel if running, `myModeUC=2; ButtonNewMode(2)` (web backgrounds); branch: `isLunbo → stop carousel`.
- `buttonZTSZ_Click` (FormCZTV.cs:1246) — same guard, `myModeUC=3; ButtonNewMode(3)` (theme settings).
- `buttonYDMB_Click` (FormCZTV.cs:1256) — same guard, `myModeUC=4; ButtonNewMode(4)` (cloud masks).

## Rotation / temp-unit combos

- `UpDateUCComboBox1` (FormCZTV.cs:1266) — display-angle combo → `directionB=(mode-1)*90`, then re-run `buttonSelectBackgroundImage()` with the timer paused around it. This is the **primary rotation entry point**. No explicit branch (arithmetic).
- `UpDateUCComboBox2` (FormCZTV.cs:1444) — boot-animation-orientation combo → temporarily set `directionB`, run `buttonFXTB_Click()` (re-encode boot gif for that orientation), then restore `directionB`. No branch.
- `UpDateUCComboBox3` (FormCZTV.cs:1454) — **empty body**; the ℃/℉ combo's effect is applied elsewhere (settings panel), so this callback is a no-op. Do NOT port as behavior.

## Boot-animation (startup gif) encode

- `GifToJPG` (FormCZTV.cs:1274) — **empty body** (JPEG-mode boot animation unsupported / stubbed). No-op.
- `GifTo565` (FormCZTV.cs:1278) — **[GOD]** encodes a multi-frame GIF into the RGB565 boot-animation wire format. Reads GIF frame-delay property (`0x5100`), rejects `frameCount≥250` with a MessageBox; sends a 4-byte header `{0,1,0,30}` via `delegateForm.Invoke(0,…)`; per frame: `SelectActiveFrame` → rotate by the **inverse-of-`directionB`** table (`0→90°, 90→0°, 180→270°, 270→180°`, default 90°) → lock bits → pack BGRA→RGB565 with byte order chosen by `is320x320 || myDeviceSPIMode==2 → big-endian word` else little-endian → `Invoke(0, frameIndex(+×2 for 320x320), payload)`; finally emits a 153600-byte delay table (`delay×10`, clamped 250). Key branches: frame-count reject; `directionB` rotate; `is320x320`/`SPIMode==2` endianness; `array[i]==0→10` delay floor.
- `buttonFXTB_Click` (FormCZTV.cs:1381) — picks the correct built-in boot-anim resource by resolution (`is320x320→_320320`, `is240x240→_240240`, portrait `_240320`/landscape `_320240` for default) and **early-returns for all widescreen/round panels** (360/480/640/1600/1280/1920/854/960/800 unsupported); shows the "adjusting…" label, pauses timer, dispatches `GifTo565` (mode1) or `GifToJPG` (mode2), then a ~30s blinking-label progress wait. Key branches: resolution→resource; `myDeviceMode 1→565, 2→JPG`; `Language→label text`.

## Local image / gif / video authoring

- `ImageCut_Open` (FormCZTV.cs:1458) — OpenFileDialog (JPG/BMP/PNG); on a valid file: reset yield-scale, `myUISubMode=1`, clear bg/gif/image state, hand the bitmap to `ucImageCut1.SetImage`, `buttonMS_Mode()`. Branches: dialog OK; `FileInfo.Exists`.
- `UpDateUCImageCut` (FormCZTV.cs:1496) — image-crop callback; branch: `image==null → cancel (reload current theme via `Theme_Click`/`Theme_Click_Event`)`; else save the crop as `00.png`, delete stale `Theme.zt`, round-trip PNG bytes, set it as `bitmapBGK`/`imagePicture`, `ImageCount=100` (static-image mode).
- `GifSelect_Open_Generate` (FormCZTV.cs:1537) — OpenFileDialog (GIF); on valid file pauses timer, clears image state, `myUISubMode=0`, `My_Gif_Generate_Event(file)`, resets counters, `isGifMode=true`. Branches: dialog OK; Exists.
- `VideoCut_Open` (FormCZTV.cs:1881) — OpenFileDialog (MP4/AVI/MKV/MOV/GIF); on valid file clears state, `myUISubMode=2`, `buttonMS_Mode()`, `ucVideoCut1.SetImage(file)` (enters the crop UI). Branches: dialog OK (else re-enable timer); Exists.
- `My_Video_Generate_Event` (FormCZTV.cs:1923) — moves the produced `Theme.zt` into `GifDirectory`, deletes stale `00.png`, then parses the `.zt` (`magic 220` → N delays as int32, then N length-prefixed frame byte-blobs into `imageArray`). Branch: `firstByte==220 → parse`.
- `My_Video_To_Theme` (FormCZTV.cs:1975) — runs `ffmpeg -i <name> -r 24 -f image2 …%04d.bmp` via a hidden `cmd.exe`, then `ucVideoCut1.BmpToThemeFile()` packs the BMPs into a `.zt`, then `My_Video_Generate_Event`. Security note for the port: shell-launched ffmpeg with interpolated path. No logic branch.
- `UpDateUCVideoCut` (FormCZTV.cs:1996) — video-crop callback; branch: `array==null → cancel + reload theme`; else show "working" label (localized), pause timer, clear image state, `My_Video_Generate_Event(name)`, `isGifMode=true`, hide label. Branches: null; `Language→label`.
- `VideoSelect_Open_Generate` (FormCZTV.cs:2045) — OpenFileDialog (video) → hand to the projection player `ucBoFangQiKongZhi1.SetNewFile(file)` (live screencast source). Branches: dialog OK; Exists.

## Per-tick housekeeping

- `GetSystemInfo` (FormCZTV.cs:2066) — per-tick sensor/UI refresh clock: every 3rd tick runs the eyedropper timer; every 64th tick invalidates the metric-add panel + `UCXiTongXianShiTimer()` (repaint metric fields); decrements the flash timer (`shanPingTimer`) and clears `shanPingCount` at expiry. Key branches: `InfoCount%3==0`, `InfoCount>=64`, `shanPingTimer>=0`. (Per-tick → DEBUG-level in our port.)

## JPEG helpers

- `CompressionImage` (FormCZTV.cs:2605) — encodes an `Image` to JPEG bytes at the given `quality` via `EncoderParameter(Encoder.Quality)`; disposes both the temp bitmap and the source image in `finally`. No branch.
- `GetEncoder` (FormCZTV.cs:2640) — returns the `ImageCodecInfo` whose `FormatID` matches the requested `ImageFormat` (used for JPEG). Note: queries `GetImageDecoders()` (a decoders/encoders quirk, works because GUIDs match). No branch.

## UI-mode presentation (the big per-resolution methods)

- `buttonSelectBackgroundImage` (FormCZTV.cs:3099) — **[GOD]** **[COPY-PASTE]** (51 branches). Rebuilds the entire preview surface for the current resolution + `directionB` + `myUIMode`: sets the crop/video/preview panel background images per resolution, toggles `isFanZhuan` (portrait flip) for widescreen at 90/270, positions the screenshot popup (`formJP.SetBounds`) with W/H swapped at portrait angles, sets the projection-preview and screencast-popup backgrounds, and finally `SetMyUCScreenImage(directionB)`. Key branch axes: resolution (`is320x320`/`is360x360`/`is480x480`/`is240x240`/`is1600x720`/`is1280x480`/`is1920x462`/`is800x480`/`is854x480`/`is960x540`/default 320×240) × `directionB∈{0/180 landscape, 90/270 portrait}` × `myUIMode∈{1|4 crop, 2 projection}` × `myDevicePingMu==3 round-480`. Every resolution block is a near-identical 6-8 line asset-swap — the single largest consolidation target: fold into a `profile→{cropAsset,previewAsset,popupAsset,isFlipped}` table keyed on `(resolution, orientation)`.
- `buttonMS_Mode` (FormCZTV.cs:3522) — mode dispatcher for the three authoring surfaces; branches: `myUIMode==1 → bg mode (show image/video crop per `myUISubMode` 0/1/2, hide player)`, `==2 → projection mode (drop bg bitmap, show projection scale, hide crop)`, `==4 → screencast mode (drop bg, show player)`. Each calls `SetDrawBkImage(scale)` + `buttonSelectBackgroundImage()`.

## Local-theme delegate

- `ThemeLocal` (FormCZTV.cs:3582) — local-theme panel command bus; branches: `cmd 0 → list all themes`, `1 → first 5 (defaults)`, `2 → user themes (index≥5)`, `16 → select theme (myTheme=info+data*5) + load`, `32 → delete theme (confirm dialog, reindex myTheme around the deleted slot, `Directory.Delete`, notify via Invoke(128))`, `48 → persist carousel state (`ChangeFileTheme`)`. The delete branch has 3 sub-cases (`myTheme ==/</> num`) for reindexing.

## Web-background download

- `DownLoadFile` (FormCZTV.cs:3684) — HTTP GET `webDir+name` → stream to `dir+name` in 1 MiB chunks; returns false on empty body or exception. Key branches: `Language→label + domain rewrite czhorde.com→czhorde.cc` (langs 2/other); `bytesRead>0` success vs `false`; catch→false.
- `GetWebBackgroundImageDirectory` (FormCZTV.cs:3749) — **[COPY-PASTE]** (19 branches) returns the local `…\Data\USBLCD\Web\<WxH>\` folder for the current resolution, with a portrait/landscape dir swap (`1600720`↔`7201600`, etc.) at 90/270. Pure resolution→string ladder.
- `GetWebBackgroundHttpDirectory` (FormCZTV.cs:3830) — **[COPY-PASTE]** (19 branches) identical ladder returning `http://www.czhorde.com/tr/bj<WxH>/`. Same shape as the two dir methods below — one table with a `{localRoot, httpRoot, ztRoot}` triple per profile kills three methods.
- `GetFileList` (web, no-arg) (FormCZTV.cs:3911) — `new DirectoryInfo(GetWebBackgroundImageDirectory()).GetFiles()`. No branch. (Distinct from the recursive `GetFileList(dir,list)` below — a name collision.)
- `CheakWebFile` (FormCZTV.cs:3917) — loads the web-bg thumbnails for a category; collects `.png` names, filters by category letter (`mode 1→a,2→b,3→c,4→d,5→e,6→y`), then round-trips each PNG into `imageArray`. Returns false if the folder is empty. Key branches: empty→false; `mode→letter filter`.
- `ThemeWeb` (FormCZTV.cs:3991) — web-bg panel command bus; branches: `cmd 0..6 → load category thumbnails via CheakWebFile`; `cmd 16 → download `<info>.mp4` if missing (`DownLoadFile`), then `My_Video_To_Theme` to convert into a live theme (localized "Loading" label, timer paused)`.

## Cloud-mask (online-theme) download

- `MengBanSelect_Open` (FormCZTV.cs:4078) — **[COPY-PASTE]** (26 branches) opens a local PNG as a mask overlay with a **per-resolution oversize guard** (`is480x480→1.5×`, `is1600x720→4×`, `is1280x480→×0.375`, `is1920x462→×0.25`, `640/800/854/960→2×`, default→1×); each failure path duplicates the same 3-language MessageBox. On success saves `01.png`, sets `bitmapMB` + its W/H/X/Y center. Fold the guard into `profile.maskMaxScale` and the MessageBox into one localized helper.
- `GetFileListMBDir` (FormCZTV.cs:4255) — **[COPY-PASTE]** (20 branches) resolution→`…\Web\zt<WxH>\` ladder (round-480 special-cases `zt480480y`); mirror of the two Web dir methods above.
- `GetFileListMB` (FormCZTV.cs:4340) — `Directory.GetDirectories(GetFileListMBDir())`. No branch.
- `CheakMaskFile` (FormCZTV.cs:4346) — for each mask subdir loads `Theme.png` thumbnail into `imageArray` and the dir name into `nameArray`; returns false if empty. Branch: empty→false; all in one try/catch.
- `ThemeMask` (FormCZTV.cs:4374) — cloud-mask panel command bus; branches: `cmd 0 → list mask thumbnails`; `cmd 16 → apply mask `<info>`: load its `01.png` as `bitmapMB`, then `ReadSystemConfiguration(config1.dc, readMyMode:false)` to pull its overlay layout, sync rotation combo, re-render`. This is the "online theme = mask + DC overlay" flow.

## Theme-settings delegate

- `ThemeSetting` (FormCZTV.cs:4422) — **[GOD]-ish** (24 branches) the settings-panel command bus; pauses timer, dispatches by `cmd`: `1/2/3 → UI mode bg/projection/screencast (set `myUIMode`+`myMode`, show/hide flags, start/stop player)`; `10 → VideoSelect`, `49 → ImageCut`, `50 → GifSelect`, `51 → web tab`, `52 → VideoCut`; `65-68 → projection JpX/Y/W/H`; `69 → hide-bg toggle`; `96 → mask-visible + SetDrawMengBan`; `97 → MengBanSelect`; `99 → mask tab`; `112 → show fullscreen eyedropper`; `128 → system-info visible + SetDrawXiTong`; `129 → flash-select a metric field (`shanPingTimer=14`)`. Restores timer unless in video-crop sub-mode. A clean port routes each `cmd` to a named Command.

## Directory / theme file helpers

- `CopyDireToDire` (FormCZTV.cs:4540) — recursive dir copy: enumerate files+dirs (via the two helpers), recreate the tree under `destDir`, `File.Copy(overwrite:true)` each file. Branch: create-if-missing per dir.
- `GetFileList` (recursive) (FormCZTV.cs:4564) — recursively accumulates all `FileInfo` under a dir into the passed list. No branch (recursion). **Name-collides** with the web `GetFileList()` — rename on port.
- `GetDirList` (FormCZTV.cs:4574) — recursively accumulates all `DirectoryInfo`. No branch.
- `ChangeFileTheme` (FormCZTV.cs:4584) — writes the theme-selection state file (`fileThemeVal`): magic `220`, `myTheme`, carousel on/timer/count/6-slot array, `myLddVal`; optional `Reset_Button`. Branch: `reset → Reset_Button`.
- `Theme_Click` (FormCZTV.cs:4611) — thin guard: `if bl → ChangeFileTheme(resetButton)`. Single branch.
- `ReadFileTheme` (FormCZTV.cs:994) — enumerates theme subdirs, **sorts Theme1..Theme5 first** (priority list) then alphabetical, fills `themeArray`, then `ReadFileThemeSub(bl)`. No branch (LINQ orderby).
- `ReadFileThemeSub` (FormCZTV.cs:1010) — reads the selection state file back: branch `!bl → return`; if `magic==220` restore `myTheme` + carousel fields + (try) `myLddVal`/`buttonLDD_Set`; clamp `myTheme` if ≥ count; catch → dynamic-island fallback `myLddValSub==2→myLddVal=3`.

## Image byte round-trip

- `ByteToBitmap` (FormCZTV.cs:4619) — `Image.FromStream(new MemoryStream(buffer))`. No branch. (Used everywhere to detach a bitmap from its source file handle.)
- `BitmapToByte` (FormCZTV.cs:4631) — save bitmap to a `MemoryStream` as PNG, return `GetBuffer()`. No branch. Note: `GetBuffer()` returns the **oversized backing array** (padded), a latent bug the callers tolerate because `Image.FromStream` stops at the PNG end marker.

## DC layout read / write (the core codec)

- `ReadSystemConfiguration` (FormCZTV.cs:4642) — **[GOD]** (52 branches) the `config1.dc` reader. Two format cases on the leading byte: `220` = legacy fixed-schema (reads a long fixed sequence of font/color records, each gated by a boolean flag `flag2..flag8` deciding whether that element — title/CPU-label/CPU-value/GPU/… — is emitted as a 9-field ArrayList descriptor `[type, mode, x, y, mainCount, subCount, Color, Font, text]`); `221` = new schema (count-prefixed list of `UCXiTongXianShiSub` records + trailing layout scalars `myBjxs/myTpxs/directionB/myUIMode/myMode/myYcbk/JpX..JpH/myMbxs/XvalMB/YvalMB`). `readMyMode` gates whether the trailing render-mode scalars are applied. The repeated 18-line "read Font(name,size,style,unit,charset)+Color(argb)+if(flag) emit descriptor" block is the copy-paste core — extract a `ReadElementRecord(flag, type, text)` helper.
- `Theme_Click_Event` (FormCZTV.cs:5382) — loads the active theme into the render state: clears bitmaps, optionally (`bl`) wipes `GifDirectory` and copies the selected theme dir into it, `ReadSystemConfiguration(config1.dc)`, loads `01.png`→`bitmapMB`, then **either** `00.png`→static `imagePicture` (`ImageCount=100`) **or** parse `Theme.zt`→`imageArray` animated frames (`isGifMode=true`); syncs rotation combo + `buttonMS_Mode`. Key branches: `bl→copy-in`; `00.png exists → static else animated`; `Theme.zt magic==220`.
- `textBoxCMM_KeyPress` (FormCZTV.cs:5488) — blocks invalid filename/path chars in the theme-name box; branch: `char is invalid && !control → Handled=true`.

## Theme save / export / import

- `buttonBCZT_Click` (FormCZTV.cs:5497) — **[GOD]** (15 branches) "save theme": validate name (non-empty, trim trailing space, reject collision with the 5 default themes), append to `themeArray` if new, wipe+recreate the theme dir, `CopyDireToDire(GifDirectory→themeDir)`, delete `Theme.png`, then write `config1.dc` in the **`221` schema** (magic 221, `myXtxx`, count + per-`UCXiTongXianShiSub` record {mode,modeSub,x,y,mainCount,subCount,Font{name,size,style,unit,charset},Color{argb},text}, then the layout scalars), reload, notify `Invoke(128)`. Branches: empty-name/trailing-space loop; default-collision reject; `myThemeT==0` new-vs-overwrite; `Language→messages`.
- `buttonDaoChu_Click` (FormCZTV.cs:5657) — export to `*.tr`: SaveFileDialog, write a 4-byte magic `{221,220,221,220}`, then the **same `221`-schema element/scalar block as save**, then **10240 bytes of `220` padding** (reserved region), then `01.png` (len-prefixed) and either `00.png` (len-prefixed, preceded by a `0` marker) or the `Theme.zt` frame stream (N delays + N len-prefixed blobs). Branches: dialog OK; `01.png` exists→bytes/`0`; `00.png` exists→static else `.zt` stream.
- `buttonDaoRu_Click` (FormCZTV.cs:5788) — **[GOD]** (66 branches) import mirror of export: OpenFileDialog (`*.lzt;*.tr`), validate the magic (`220→{220,220,220}` legacy or `221→{220,221,220}` new — bail on any mismatch), then read back the element records + layout scalars + reserved padding + `01.png`/`00.png`/`.zt`, reconstruct the theme into a new dir, and load it. Largest branch count in the file (magic validation × element flags × static/animated × resolution handling). Consolidate against the same `ReadElementRecord`/`ReadImageBlock` helpers as `ReadSystemConfiguration` — export/import/save currently triplicate the DC serialization.

## Device callback / dynamic-island

- `DeviceDataReceived` (FormCZTV.cs:6732) — inbound USB report handler; branch: `isFanLcd → fan-LCD RPM = data[5]*30`, pushed into both system-info panels. Only fan-LCD devices consume the reply; every other device ignores the packet. (Relevant to the fan-RPM work — the C# fan value comes from `data[5]×30` on the device's own report, not hwmon.)
- `buttonLDD_Set` (FormCZTV.cs:6753) — set dynamic-island (灵动岛) value: store `myLddVal`, mirror to the screen-image control, swap the LDD button image; branches `val 0/1/2/3 → PL0..PL3`.
- `buttonLDD_Click` (FormCZTV.cs:6774) — cycle `myLddVal = (v+1)%4`, skipping 0 (`0→1`), then `buttonLDD_Set` + persist via `ChangeFileTheme(reset:false)`. Branch: `==0→1`. (Confirms the 1→2→3 cycle in AUDIT_LCD_PIPELINE caveat #9.)

---

## Consolidation summary (drive the hexagonal rewrite)

| Rank | Target | Line | Why |
|---|---|---|---|
| 1 | `buttonSelectBackgroundImage` **[GOD]+[COPY-PASTE]** | 3099 | 51 branches; per-`(resolution,orientation,uiMode)` asset/bounds swap → one profile table |
| 2 | `buttonDaoRu_Click` **[GOD]** | 5788 | 66 branches; import triplicates the DC codec — share `ReadElementRecord`/`ReadImageBlock` |
| 3 | `ReadSystemConfiguration` **[GOD]** | 4642 | 52 branches; fixed-schema element parser, 18-line record block repeated per element |
| 4 | `GetWebBackgroundImageDirectory`/`GetWebBackgroundHttpDirectory`/`GetFileListMBDir` **[COPY-PASTE]** | 3749/3830/4255 | 3 identical resolution→path ladders → one `{local,http,zt}` triple per profile |
| 5 | `MengBanSelect_Open` **[COPY-PASTE]** | 4078 | 26 branches; per-resolution oversize guard + duplicated 3-language MessageBox |

Also: `FormCZTVLanguageSet` (457, 9 duplicated localized-asset blocks), `buttonBCZT_Click`/`buttonDaoChu_Click`/`buttonDaoRu_Click` share the `221`-schema serialization (extract a DC writer/reader pair), and `ClearMemoryMy`/`ClearMemorySelf`/`GifToJPG`/`UpDateUCComboBox3` are dead/empty (drop, do not port).
