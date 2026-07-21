# C# Decompile Audit — master index

Line-cited audit of TRCC 2.1.6 (`~/Downloads/TRCCCAPEN/TRCC_decompiled/`), the
protocol/behavior oracle for the hexagonal consolidation. Each subsystem doc was
produced by a focused per-file agent (read end-to-end, every claim quoted +
line-cited) and **spot-verified against the source** — one load-bearing claim per
doc re-read by hand. Method + rationale: `project_cs_full_audit` (memory).

## Coverage

**Audited: ~53,000 of 70,662 lines (≈75% total; ≈84% of behavior-bearing code).**
The unaudited ~17.5K is mostly `Resources.cs` (7,420 — generated accessors, no
logic) + small UI controls (color pickers, comboboxes, options panels, About).

| Doc | Subsystem | Files (lines) | Verified |
|---|---|---|---|
| `AUDIT_LCD_PIPELINE.md` | LCD compose/rotate/wire, 5 families | `FormCZTV`, `UCScreenImage` (8.6K) | ✅ me + `rotation_trace.py` |
| `AUDIT_DISCOVERY.md` | Enumeration, handshake dispatch | `Form1`, `UCDevice` (3.2K) | ✅ pm/sub offsets + VID/PID |
| `AUDIT_METRICS_CLOCK.md` | Metrics, overlay elements, clock | `UCSystemInfo`,`UCXiTongXinXi`,`UCXiTongXianShiSub`,`UCShiJianXianShi`,`FormSystemInfo` (3.7K) | ✅ GPU→CPU power fallback |
| `AUDIT_THEME_MASK.md` | Local/cloud themes, masks, crop | `UCThemeLocal/Web/Mask`,`UCImageCut` (3.8K) | ✅ hard-coded aspect thresholds |
| `AUDIT_VIDEO.md` | Video/playback/animation | `UCVideoCut`,`UCBoFangQiKongZhi`,`UCDongHuaLianDong` (4.8K) | ✅ 300000 cap + ffmpeg supersample |
| `AUDIT_LED_CORE.md` | LED protocol, effects, PM table | `FormLED` (16.8K) | ✅ `*0.4f` brightness + orange (255,110,0) + header |
| `AUDIT_LED_SEGMENT.md` | Segment-display compose/masks | `UCScreenLED`,`FormKVMALED6` (12.2K) | ✅ SetMyNumeral overloads + LF13 460×460 |

## Cross-subsystem findings that shape the consolidation

- **Fingerprint is 2 bytes at connect**: `pm = receive[6]`, `sub = receive[5]` (`UCDevice.cs:833`); `fbl/mode/count/ysl` are parsed downstream in `FormCZTV`/`FormLED`. → the `DeviceProfile` resolve-once seam.
- **Two discovery worlds**: HID in-process (`UCDevice`) vs SCSI/SPI LCD via an external `USBLCD*.exe` + shared memory polled by `Form1` (15 ms). → our `Platform` discovery must mirror both.
- **Everything is table-driven**: per-product LED reorder tables, per-resolution aspect thresholds (verbatim magic doubles — do NOT recompute), rotation tables, header formats. → one `DeviceProfile`/spec, branch-free render (SOLID/DRY/KISS).
- **Silent fallbacks that a naive port breaks**: GPU power → CPU power (`UCSystemInfo.cs:944`); on-device values decimal-truncated to integers; bg-fit falls to a solid-black default when the theme bg overflows the canvas.
- **`0xDA DB DC DD` (218-221) is the universal magic** — LED header, LCD 20-byte header, `Theme.zt` first byte, shutdown sentinel `AA BB CC DD`.

## Gap list — our code vs the C# (the consolidation work plan)

From `AUDIT_LCD_PIPELINE.md` (device render):
- **G1** 360×360 → base 90 (C# default branch), we send base 0. xfail test landed. No reporter.
- **G2** Mjolnir 320×240 letterboxes at 90°; C# draws native-bg-or-black + **upright** text (post_rotate path is WRONG — it rotates text).
- **G3** fbl 51/53 RGB565 byte order — C# SPIMode=2 → big-endian; our profile little-endian. VERIFY, don't flip blind.
- **G4** round-480 Hei/Bu edge fill unimplemented (cosmetic).
- **G5** widescreen animated `.zt` themes squeezed into 320×240 (no widescreen branch in prep) — confirmed by 2 agents.

New from this pass (candidates — verify before acting):
- **G6** GPU-power silent fallback to CPU power — does our sensor port replicate it, or return 0?
- **G7** LED global `*0.4f` brightness cap — do we apply it? (color mismatch if not.)
- **G8** per-resolution crop aspect thresholds — are ours the verbatim C# literals?

## NOT covered (honest)
- `Resources.cs` (7,420, generated), and small UI controls: `UCXiTongXianShiColor`,
  `UCAbout`, `UCTouPingXianShi`, `UCSystemInfoOptions*`, `UCComboBox{A,B,C}`,
  `UCThemeSetting`, `UCColorA`, `UCLEDMemoryInfo`, `UCXiTongXianShiTable`.
- Cross-file boundaries each agent flagged EXTERNAL (e.g. segment digit→wire packing
  lives in `FormLED`, which IS covered; the actual SCSI/SPI USB handshake lives in an
  external `.exe` absent from this decompile).
- Each doc's claims are spot-verified (one load-bearing claim/doc), NOT line-by-line.
