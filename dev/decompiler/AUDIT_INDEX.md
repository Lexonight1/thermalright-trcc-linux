# C# Decompile Audit — master index

Line-cited audit of the Windows app, the protocol/behavior oracle for the
hexagonal consolidation. Each subsystem doc was produced by a focused per-file
agent (read end-to-end, every claim quoted + line-cited) and **spot-verified
against the source** — one load-bearing claim per doc re-read by hand. Method +
rationale: `project_cs_full_audit` (memory).

**These docs were written against TRCC 2.0.3. Their citations now address TRCC
2.1.6, which is what we build against.** 135 of the methods they document
changed between the two and are flagged, per doc, in each file's state block.
Read [Provenance](#provenance) before relying on any of them.

## Provenance

Every audit below used to open with "TRCC 2.1.6". None were written against it.
They describe `~/Downloads/TRCCCAPEN/TRCC_decompiled/`, whose `AssemblyVersion`
is **2.0.3.0** and whose `TRCC.exe` predates the 2.1.6 installer by four months.
The label was prose: it cost nothing to write and nothing checked it.

It is checked now. `audit_release.py` measures each doc's release from its own
evidence, records the answer in the doc, and fails CI when doc and evidence
disagree:

    python3.12 dev/decompiler/audit_release.py             # status table
    python3.12 dev/decompiler/audit_release.py --rebase    # re-anchor onto the current tree
    python3.12 dev/decompiler/audit_release.py --check     # CI gate
    python3.12 dev/decompiler/audit_release.py --worklist worklist.json

Each doc carries a generated block recording two different facts that are easy
to conflate:

- **origin** — the release it was *read from*. History. Measured once from the
  file sizes it states about its sources (exact: `UCScreenLED` is 10,141 lines in
  2.0.3, 10,143 in 2.1.6) and then frozen, because the other evidence for it —
  citation line numbers — is destroyed the moment those citations are re-anchored.
- **addresses** — the release its line numbers *point at*. Changed by `--rebase`.

Verify the trees yourself; never trust a folder name, the folder name is what lied:

    grep -rh AssemblyVersion ~/Downloads/TRCCCAPEN/TRCC_decompiled/Properties/*.cs
    grep -rh AssemblyVersion ~/Downloads/TRCC_2.1.6_decompiled/Properties/*.cs

| | these docs' origin | what we build against |
|---|---|---|
| path | `TRCCCAPEN/TRCC_decompiled/` | `TRCC_2.1.6_decompiled/` |
| `AssemblyVersion` | **2.0.3.0** | **2.1.6.0** |
| C# files / lines | 56 / 70,662 | 62 / 87,011 |
| `UCDevice` | 1,438 lines | 1,747 |
| `FormCZTV` | 7,218 | 8,829 |
| `TRCC.LCD` namespace | absent | 5,156 lines, new |
| device models | 44 | ~65 (matches ours) |

### What was re-anchored, and what was not

**1,237 citations moved** onto 2.1.6. Only where the method's body is
byte-identical across the two releases — 828 of the documented methods are, so
their prose is still true and only the pointer was wrong.

**135 documented methods changed** and were deliberately *not* moved. Re-pointing
those would leave a citation that looks current beside prose describing code that
no longer exists — worse than an obviously stale number, because nothing signals
it. Each doc's state block names its own; the heaviest are `BEHAVIOR_DISCOVERY`
(36), `BEHAVIOR_FORMCZTV` (21), `BEHAVIOR_THEME` (19), `BEHAVIOR_VIDEO` (13),
`BEHAVIOR_FORMLED` (12).

**4 citations were already mis-anchored** before any of this ran — e.g.
`AUDIT_LCD_PIPELINE` labels a `RotateImgHei` line range `RotateImgBu`. They are
recorded as `known-bad` in the state blocks so a real regression cannot hide
behind them.

### What 2.1.6 adds that no doc covers

**7 files, 8,154 lines, entirely unaudited** — absent from the tree these docs
read: `TRCC.LCD/FormLCD` (5,082 — a second LCD host with its own `FormCZTVInit`),
`UCVideoCutF` (1,651), `UCImageCutF` (726), `UCShortcut` (275), `UCScrollB`/`C`
(218/128), `FormLCDImageCut` (74).

Plus rewrites inside files that do exist: `FormCZTVInit` (`case 5:`/`case 7:` →
else-if chain; new pm 50/63/66/68/69; `is1920x440`), and `mySubMode` — which
branches *before* the rotation switch in six resolution families, contradicting
this audit's "`pmSub` never touches rotation".

`audit_release.py --worklist` writes the full list: **148 changed, 38 new, 149
unaudited** behaviour-bearing methods across 28 files.

## Source policy — facts, not source text

**The decompile stays on the machine that made it. It is never committed, and
neither is anything pasted out of it.**

These docs describe behaviour and cite where to find it. They do not reproduce
the vendor's source:

- **Keep — functional facts a port depends on**: byte sequences and magic numbers
  (`0xDA DB DC DD`, `{170,187,204,221}`), header layouts and offsets, resolutions,
  angles, PM/SUB values, thresholds, timer intervals, resource and file names,
  and the identifier names of the methods being described.
- **Drop — expression**: statements, declarations and control flow as source text.
  A line citation replaces it. `FormCZTV.cs:2646` tells a reader exactly where to
  look **in their own extraction** and carries none of the source with it.

Two reasons, in order of weight. A takedown against the repository would take the
releases, the issue tracker and the distro packages down with it, and the cost of
not being worth arguing about is an afternoon of editing. And it reads better:
prose describing what a method does is usable by a contributor who has not
extracted anything, which pasted C# never was.

`csharp_oracle/Oracle.cs` follows the same rule. Its rotation step is written from
the described behaviour rather than copied, and it is checked: across all 416
combinations of the 13 supported geometries × both encoders × pm × angle it
returns byte-identical quadrant mappings to the version it replaced. An
independent implementation that agrees with our Python is evidence; a copy
agreeing with itself was not.

Extraction is reproducible from the vendor installer — the recipe lives in the
decompile's own `README.md`, outside this repository.

## Coverage

Measured by `audit_coverage.py` against the 2.1.6 control-flow map:

**787 / 1,033 behaviour-bearing methods cited = 76%.**

It read 54% before the re-anchor. That rise is **not new understanding** — it is
the same understanding, correctly addressed: citations that had been pointing
into the wrong release now land on the methods their prose describes. Real gains
come only from re-auditing the worklist above.

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
