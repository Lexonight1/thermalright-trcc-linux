"""audit_devices — run a device handshake fingerprint through the C# device
oracle (``FormCZTVInit``) and diff the capability set it resolves against what
OUR port resolves for the same fingerprint.

The second extractor of the decompile-miner, and the one the north-star audit
is built on.  ``audit_rotation`` already covers the *wire-angle* axis; this
covers the axis it doesn't — the capability set ``FormCZTVInit`` decides for a
device at onboarding:

    final FBL  ·  resolution  ·  widescreen (isBiliPingmu)  ·  ThemeML dir

"theirs" comes from ``formcztv_init.form_cztv_init`` (the line-cited C#
transcription); "ours" comes from the real shipping functions
``trcc.core.protocol.pm_to_fbl`` / ``get_profile`` (no reimplementation).

    # one device by its handshake fingerprint
    PYTHONPATH=src python3 dev/decompiler/audit_devices.py --pm 5

    # the whole known device corpus (registry + every FormCZTVInit branch)
    PYTHONPATH=src python3 dev/decompiler/audit_devices.py --all

Exit code is non-zero if any device *the oracle models* diverges from the C#,
so it doubles as a CI/regression guard on the FBL/resolution/widescreen port.

Two things are reported but NOT counted toward the verdict:

  * **ThemeML** (native theme-catalog dir) — the C# seeds this from the
    ``pmSub`` byte ONCE at onboarding (a hardware-mount default).

    **This used to be incomparable, and is no longer.**  The note here said
    diffing it properly "needs tracing our initial-orientation-on-connect, a
    separate follow-up".  Done 2026-09-20: ``ConnectDevice.execute`` calls
    ``Settings.seed_mount_orientation(key, profile.portrait_mounted)`` on first
    connect, so a portrait-mounted panel starts at 90 and reads the transposed
    catalog — the same thing the C# seeds from ``pmSub``.  Both sides are now
    the FIRST-BOOT seeded value, so a row here is a REAL difference in the
    mount rule.

    Until that landed this compared our orientation-0 default against their
    seeded value and reported **five** divergences, **two of which were this
    auditor accusing correct code** (854x480 SUB=5 and 960x540 SUB=5) — the
    same failure as the widescreen/``isBiliPingmu`` semantic split below.  It
    is three now, and each carries its cause.

    Still reported but NOT counted toward the verdict: one of the remaining
    rows is not an orientation question at all.  The old ``MOUNT_3_OF_9`` gap
    is PAID — ``is_portrait_mounted`` now models all nine of the C#'s mount
    families, each with its own pmSub threshold.

  * **oracle-gap** rows — fingerprints our port handles that ``FormCZTVInit``
    does not branch on (the FBL 224/192 by-PM sub-splits live in a *different*
    C# function, ``FormCZTV.cs:682-821``).  Listed for honest coverage, excluded
    from pass/fail.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from typing import ClassVar
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from formcztv_init import form_cztv_init, resolution_of

from trcc.adapters.device.bulk_lcd import _BULK_BASE_FBL, _BULK_KNOWN_PMS
from trcc.core.models import oriented_resolution
from trcc.core.protocol import get_profile, pm_to_fbl


@dataclass(frozen=True, slots=True)
class Fingerprint:
    """A device handshake fingerprint as it reaches the two code paths.

    ``fbl`` is the handshake FBL byte the C# ``FormCZTVInit`` receives directly
    (it only rewrites it in the ``switch(pm)`` special cases).  ``pm_driven``
    marks the wires (BULK/HID/LY) whose FBL our port derives from the PM byte
    via ``pm_to_fbl``; SCSI and the fan-hub LCD report the FBL directly.
    Whether the C# models a fingerprint is DERIVED (see ``Row.oracle_models``),
    never declared.  It used to be a hand-set flag, and five rows carried
    ``oracle_models=False`` long after the tracer grew branches for all five —
    so the tool printed "FormCZTVInit has no branch for this PM" about branches
    that were sitting in the file it imports.  A claim a tool makes about
    itself has to be recomputed, or it rots exactly like the citations did.
    """
    label: str
    fbl: int
    pm: int
    mode: int = 1
    sub: int = 0
    pm_driven: bool = True


# The corpus — every shipping registry device plus each distinct FormCZTVInit
# branch.  Labelled by fingerprint only; the resolution is derived and printed
# per row so a label never asserts a resolution the code might contradict.
CORPUS: tuple[Fingerprint, ...] = (
    # --- SCSI / HID square panels (FBL reported directly; pm==fbl poll) -------
    Fingerprint("SCSI 320x320 (87CD:70DB, 0402:3922)", fbl=100, pm=100,
                pm_driven=False),
    Fingerprint("HID Type3 320x320 (0418:5303/5304)",  fbl=100, pm=100,
                pm_driven=False),
    # --- BULK square (Elite Vision 360, RGB565 pm=32) ------------------------
    Fingerprint("BULK 320x320 pm32 (0416:5406)",       fbl=100, pm=32, mode=2),
    # --- BULK Grand Vision 480x480 (#186 — correct fingerprint) --------------
    Fingerprint("BULK 480x480 (87AD:70DB GrandVision)", fbl=72, pm=72,
                pm_driven=False),
    # --- LY widescreen 1920x462 (Trofeo 9.16, 0416:5408/5409) ----------------
    Fingerprint("LY 1920x462 pm65 (0416:5408/5409)",   fbl=192, pm=65, mode=2),
    # --- Mjolnir 320x240 JPEG (pm=5, #176) -----------------------------------
    Fingerprint("Mjolnir 320x240 pm5 (#176)",          fbl=0,  pm=5),
    # --- 640x480 (pm=7) ------------------------------------------------------
    Fingerprint("640x480 pm7",                         fbl=0,  pm=7),
    # --- widescreen 854x480 portrait-mount (pm=11 sub=5, #203) ---------------
    Fingerprint("854x480 pm11 sub5 (#203)",            fbl=0,  pm=11, mode=2,
                sub=5),
    # --- widescreen 1600x720 via (pm=1, sub=48) ------------------------------
    Fingerprint("1600x720 pm1 sub48",                  fbl=0,  pm=1, mode=2,
                sub=48),
    # --- widescreen 1920x462 via (pm=1, sub=49) ------------------------------
    Fingerprint("1920x462 pm1 sub49",                  fbl=0,  pm=1, mode=2,
                sub=49),
    # --- fan-hub LCD 360x360 (fbl=54, mode3 pm100) ---------------------------
    Fingerprint("fan LCD 360x360 (fbl54)",             fbl=54, pm=100, mode=3,
                pm_driven=False),
    # --- 1280x480 (fbl=128, mode3 pm100) -------------------------------------
    Fingerprint("1280x480 (fbl128)",                   fbl=128, pm=100, mode=3,
                pm_driven=False),
    # --- FBL 224/192 by-PM sub-splits ---------------------------------------
    # 2.1.6 DOES model every one of these; the 2.0.3 tracer did not, which is
    # why they were once marked oracle-gap.  All five branches are guarded by
    # `myDeviceMode == 2`, so a mode-1 fingerprint misses them and the tool
    # reports a gap that is really a wrong input.
    Fingerprint("960x540 pm16 (224 by-PM)",  fbl=224, pm=16, mode=2),
    Fingerprint("800x480 pm12 (224 by-PM)",  fbl=224, pm=12, mode=2),
    Fingerprint("960x320 pm13 (224 by-PM)",  fbl=224, pm=13, mode=2),
    Fingerprint("640x172 pm15 (224 by-PM)",  fbl=224, pm=15, mode=2),
    Fingerprint("1280x480 pm68 (192 by-PM)", fbl=192, pm=68, mode=2),
    # --- 1600x720 pm64 sub3 — the Levita.  The per-SKU mount lives here: -----
    # mySubMode is assigned by SetThemeInfo_ThemeML for pmSub 2/3/4 only, and
    # sub 3 is the one the rotation switch tests.
    Fingerprint("1600x720 pm64 sub3 (Levita)", fbl=0, pm=64, mode=2, sub=3),
    Fingerprint("960x540 pm10 sub5 (PS140)",   fbl=0, pm=10, mode=2, sub=5),
)


@dataclass(frozen=True, slots=True)
class Row:
    fp: Fingerprint
    their_fbl: int
    their_res: tuple[int, int]
    their_wide: bool
    their_thememl: str
    our_fbl: int
    our_res: tuple[int, int]
    our_wide: bool
    our_thememl: str
    # The DERIVED per-SKU mount the C# resolved.  Printed on every row because
    # it is the term that decides the wire rotation for six families and is
    # invisible in the handshake bytes themselves.
    their_sub_mode: int = 0

    @property
    def fbl_ok(self) -> bool:
        return self.their_fbl == self.our_fbl

    @property
    def res_ok(self) -> bool:
        return self.their_res == self.our_res

    #: Resolutions where ``isBiliPingmu`` and OUR ``widescreen`` provably mean
    #: different things, with the evidence.  NOT a general escape hatch -- each
    #: entry is one verified case, and everything else still compares directly.
    #:
    #: **640x172 (pm 15)** -- VERIFIED 2026-09-15 in TRCC 2.1.6:
    #:   * theirs: ``FormCZTV.cs:986`` (pm 13/17/18) sets ``isBiliPingmu`` AND
    #:     builds a ``FormScreenImage`` preview popup; ``FormCZTV.cs:1001``
    #:     (pm == 15) sets ``is640x172`` + ``fbl`` and builds neither.  The flag
    #:     tracks the popup, not the rotation model.
    #:   * ours: ``DeviceProfile.widescreen`` selects "the WIRE owns rotation"
    #:     over "spin the whole composite", which ``geometry.plan_orientation``
    #:     ties to legacy's ``has_portrait_themes``.  The C# ships a full
    #:     PORTRAIT catalog for this panel -- ``ThemeML172640`` (:456),
    #:     ``GifDirectoryWebMB172640`` (:264), assigned at :1455 and :1465 --
    #:     so portrait variants exist and ``True`` is correct for us.
    #:
    #: Two correct flags, two meanings, one axis.  Comparing them directly
    #: reported our shipping code as MISMATCH 1/19 for however long.
    _SEMANTIC_SPLIT: ClassVar[frozenset[tuple[int, int]]] = frozenset({
        (640, 172),
    })

    @property
    def wide_ok(self) -> bool:
        """Does ``isBiliPingmu`` agree with our ``widescreen``?

        Direct comparison, except where the two flags are known to mean
        different things -- see ``_SEMANTIC_SPLIT``.  Do NOT widen that set to
        silence a new disagreement: an unexplained one is the signal this axis
        exists for.
        """
        if self.our_res in self._SEMANTIC_SPLIT:
            return True
        return self.their_wide == self.our_wide

    @property
    def wide_semantics_differ(self) -> bool:
        """The axis was excused by a verified semantic split, not by agreement."""
        return (self.our_res in self._SEMANTIC_SPLIT
                and self.their_wide != self.our_wide)

    @property
    def oracle_models(self) -> bool:
        """Did ``FormCZTVInit`` actually resolve geometry for this fingerprint?

        Derived, not declared: the tracer resolves a geometry for every
        fingerprint it has a branch for, and falls through to the bare 240x320
        default for one it does not.  So "the C# does not model this" is
        exactly "no resolution flag was set and it is not the pm==5 panel".
        """
        return self.their_res != (240, 320) or self.fp.pm == 5

    @property
    def diverges(self) -> bool:
        """True only for oracle-modelled rows with a verdict-axis mismatch."""
        if not self.oracle_models:
            return False
        return not (self.fbl_ok and self.res_ok and self.wide_ok)

    @property
    def thememl_differs(self) -> bool:
        return self.their_thememl != self.our_thememl


def audit(fp: Fingerprint) -> Row:
    # theirs — the C# FormCZTVInit capability set for this fingerprint.
    st = form_cztv_init(fbl=fp.fbl, m=fp.mode, pm=fp.pm, pmSub=fp.sub)
    their_fbl = st.fbl
    their_res = resolution_of(st)
    their_wide = st.isBiliPingmu
    their_thememl = st.ThemeML.strip("\\")
    their_sub_mode = st.mySubMode

    # ours — the shipping port for the same fingerprint.
    our_fbl = pm_to_fbl(fp.pm, fp.sub) if fp.pm_driven else fp.fbl
    # The SUB byte is passed.  It was not, and this auditor therefore resolved
    # a profile the shipping code never builds -- the same defect it exists to
    # catch, in the instrument itself.  (`get_profile` spends SUB on the encode
    # rotation, which this audit does not compare, and on `portrait_mounted`,
    # which it now does.)
    profile = get_profile(our_fbl, fp.pm, fp.sub)
    our_res = profile.resolution
    our_wide = profile.widescreen
    # Our catalog dir at the orientation a FIRST BOOT actually lands on, not at
    # a hardcoded 0.
    #
    # This function's own docstring said comparing ThemeML "needs tracing our
    # initial-orientation-on-connect, a separate follow-up".  That follow-up is
    # done: `ConnectDevice.execute` seeds `Settings.seed_mount_orientation(key,
    # profile.portrait_mounted)` on first connect, so a portrait-mounted panel
    # starts at 90 and reads the transposed catalog -- which is precisely what
    # the C# seeds from `pmSub`.  The two ARE comparable now.
    #
    # Compared at 0, this reported five divergences.  Two of them -- 854x480
    # SUB=5 and 960x540 SUB=5 -- were the auditor accusing CORRECT code, the
    # same failure as the widescreen/isBiliPingmu split recorded above.
    ow, oh = oriented_resolution(our_res, 90 if profile.portrait_mounted else 0)
    our_thememl = f"{ow}{oh}"

    return Row(fp, their_fbl, their_res, their_wide, their_thememl,
               our_fbl, our_res, our_wide, our_thememl, their_sub_mode)


def _res(r: tuple[int, int]) -> str:
    return f"{r[0]}x{r[1]}"


def _print(row: Row) -> None:
    fp = row.fp
    tag = "" if row.oracle_models else "   [oracle-gap]"
    print(f"\n{fp.label}{tag}")
    print(f"  fingerprint: fbl={fp.fbl} pm={fp.pm} mode={fp.mode} "
          f"sub={fp.sub} pm_driven={fp.pm_driven} "
          f"-> mySubMode={row.their_sub_mode}")
    if not row.oracle_models:
        print("  FormCZTVInit has no branch for this PM — resolution is "
              "disambiguated in FormCZTV.cs:682-821, not ported here.")
        print(f"  ours: fbl={row.our_fbl} res={_res(row.our_res)} "
              f"widescreen={row.our_wide}")
        return
    print(f"  {'axis':<12}{'C#':<14}{'ours':<14}verdict")
    print(f"  {'fbl':<12}{row.their_fbl:<14}{row.our_fbl:<14}"
          f"{'match' if row.fbl_ok else '**DIFF**'}")
    print(f"  {'resolution':<12}{_res(row.their_res):<14}"
          f"{_res(row.our_res):<14}{'match' if row.res_ok else '**DIFF**'}")
    print(f"  {'widescreen':<12}{row.their_wide!s:<14}"
          f"{row.our_wide!s:<14}{'match' if row.wide_ok else '**DIFF**'}")
    thememl_note = "differs (info)" if row.thememl_differs else "same"
    print(f"  {'ThemeML':<12}{row.their_thememl:<14}{row.our_thememl:<14}"
          f"{thememl_note}  [not a verdict axis]")


def _bulk_resolution(pm: int, sub: int) -> tuple[int, int]:
    """The resolution ``BulkLcd.connect()`` resolves for a (pm, sub).

    Mirrors the adapter's branch exactly and imports the SHIPPING
    ``_BULK_KNOWN_PMS`` — so this checks the real code path, never a copy of it.
    """
    if pm in _BULK_KNOWN_PMS or (pm == 1 and sub in (48, 49)):
        fbl = pm_to_fbl(pm, sub)
    else:
        fbl = _BULK_BASE_FBL
    return get_profile(fbl, pm).resolution


def exhaustive_bulk() -> int:
    """Sweep the ENTIRE bulk PM space against the C# ``FormCZTVInit(72, 2, …)``.

    The bulk path is the one wire whose resolution is 100% ``FormCZTVInit``
    (``FormCZTV.cs:858``, reached from ``Form1.cs:1071``, which passes the PM
    straight in), so it is fully bench-decidable.

    It read ZERO on 2026-07-11 — against the 2.0.3 decompile, five weeks before
    the real 2.1.6 was extracted.  Against 2.1.6 it reads **11**: the PMs
    2.1.6's ladder added (13, 14, 15, 16, 17, 18, 50, 63, 66, 68, 69) that
    ``_BULK_KNOWN_PMS`` does not list.  That is a known, latent divergence
    owned by ``BULK_PM_GAP`` in ``tests/test_csharp_conformance.py`` — this
    tool measures it, it does not adjudicate it.  Read a CHANGE in the count as
    the signal, not the count itself.
    """
    cases = [(pm, 0) for pm in range(256)] + [(1, 48), (1, 49)]
    diverge = [
        (pm, sub, _bulk_resolution(pm, sub), theirs)
        for pm, sub in cases
        if (theirs := resolution_of(form_cztv_init(fbl=72, m=2, pm=pm, pmSub=sub)))
        != _bulk_resolution(pm, sub)
    ]
    print(f"Exhaustive bulk sweep: {len(cases)} fingerprints "
          "(pm 0-255 + 1/48, 1/49) vs C# FormCZTVInit(72, 2, pm, sub)")
    if diverge:
        print(f"  {len(diverge)} DIVERGE from the C#:")
        for pm, sub, ours, theirs in diverge:
            print(f"    pm={pm} sub={sub}: ours={_res(ours)}  C#={_res(theirs)}")
        return 1
    print("  OK — every bulk PM matches the C#. The bulk device axis is a wall.")
    return 0


#: The wire "mode" -- ``FormCZTVInit``'s SECOND argument.  ``AUDIT_DISCOVERY.md``
#: cites it as ``1``=SPI, ``2``, ``3``=HID, ``10``, read off the shared-memory
#: synthesis table (``Form1.cs:679-805``).
#:
#: It is NOT derivable from a ``trcc report``, and this is the whole reason the
#: walk below refuses to pick one.  Measured against the hand-verified corpus:
#: our registry declares ``fbl`` for ``0416:5408`` (LY) and ``0416:5406``, yet
#: both are ``pm_driven=True, mode=2`` there -- so "the registry knows the fbl"
#: does NOT imply the fbl-direct mode.  And mode decides the answer: pm=11
#: sub=5 resolves 854x480 at mode 2 and falls through to the bare 240x320 at
#: modes 1 and 3.  Guessing it manufactures a false "the C# has no branch".
_MODES: tuple[int, ...] = (1, 2, 3)


def walk(pm: int, sub: int, fbl: int, mode: int | None,
         trace: bool) -> int:
    """Walk ONE fingerprint through the C#, without inventing what we lack.

    Three outcomes, and the difference between them is the point:

    * the corpus carries this ``(pm, sub)`` -- walk it with the VERIFIED mode
      and diff it; authoritative.
    * it does not, and no ``--mode`` was given -- walk every mode and label the
      rows CANDIDATES.  ``mode``/``fbl`` are per-SKU facts a report does not
      carry, so a single answer here would be a guess wearing a verdict's
      clothes.
    * no mode resolves a geometry -- say so.  That is a real, citable answer
      (``FormCZTVInit`` genuinely has no branch for this PM), not a failure.
    """
    match = [fp for fp in CORPUS if fp.pm == pm and fp.sub == sub]
    if match:
        print(f"\n{'=' * 60}\nCORPUS MATCH — verified mode, authoritative")
        for fp in match:
            row = audit(fp)
            _print(row)
            if trace:
                _trace(fp)
        return 0

    modes = (mode,) if mode is not None else _MODES
    label = ("the mode you passed — UNVERIFIED for this SKU"
             if mode is not None else
             "CANDIDATES — no corpus row carries this fingerprint, and a "
             "report does not\n  carry mode/fbl.  These are what the C# WOULD "
             "resolve per mode, not a verdict.")
    print(f"\n{'=' * 60}\nNO CORPUS ROW for pm={pm} sub={sub}\n  {label}")

    modelled = 0
    for m in modes:
        fp = Fingerprint(f"pm={pm} sub={sub} @ mode {m}",
                         fbl=fbl, pm=pm, mode=m, sub=sub,
                         pm_driven=(fbl == 0))
        row = audit(fp)
        _print(row)
        if trace:
            _trace(fp)
        modelled += row.oracle_models

    if not modelled:
        print(f"\n  FormCZTVInit has NO branch for pm={pm} at any mode "
              f"({', '.join(map(str, modes))}).")
        print("  The C# falls through to its bare 240x320 default, so the "
              "vendor app\n  resolves nothing for this panel here either — a "
              "finding, not a gap in\n  this tool.  Where it DOES get its "
              "geometry is FormCZTV.cs:682-821.")
    return 0


def summarise(pm: int, sub: int, fbl: int = 0) -> list[str]:
    """Compact ours-vs-theirs lines for ONE fingerprint, for ``triage.py``.

    Same decisions as :func:`walk` -- both go through ``CORPUS`` and
    ``audit`` -- at the verbosity a reporter thread needs rather than the
    auditor's full table.
    """
    match = [fp for fp in CORPUS if fp.pm == pm and fp.sub == sub]
    if match:
        row = audit(match[0])
        if not row.oracle_models:
            return [f"C# oracle: no FormCZTVInit branch (corpus row "
                    f"{match[0].label!r})"]
        verdict = "**DIFF — go read control-flow.json**" if row.diverges else "match"
        return [
            f"C# oracle [{match[0].label}, verified mode {match[0].mode}]",
            f"    fbl  {row.their_fbl} vs ours {row.our_fbl}   "
            f"res {_res(row.their_res)} vs ours {_res(row.our_res)}   "
            f"wide {row.their_wide} vs ours {row.our_wide}   -> {verdict}",
        ]
    rows = [audit(Fingerprint(f"mode {m}", fbl=fbl, pm=pm, mode=m, sub=sub,
                              pm_driven=(fbl == 0)))
            for m in _MODES]
    modelled = [r for r in rows if r.oracle_models]
    if not modelled:
        return [f"C# oracle: FormCZTVInit has NO branch for pm={pm} at any "
                f"mode — the vendor app resolves this panel's geometry at "
                f"FormCZTV.cs:682-821, which is NOT ported.",
                f"    ours: fbl={rows[0].our_fbl} "
                f"res={_res(rows[0].our_res)}"]
    out = [f"C# oracle: pm={pm} sub={sub} is not in the corpus — CANDIDATES "
           f"per mode (a report carries no mode/fbl):"]
    out += [f"    mode {r.fp.mode}: C# {_res(r.their_res)} fbl={r.their_fbl}"
            f"   vs ours {_res(r.our_res)} fbl={r.our_fbl}" for r in modelled]
    return out


def _trace(fp: Fingerprint) -> None:
    """The line-cited walk through the C#, which the verdict table hides."""
    st = form_cztv_init(fbl=fp.fbl, m=fp.mode, pm=fp.pm, pmSub=fp.sub)
    print("  --- C# walk ---")
    for line in st.trace:
        print(f"  {line}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pm", type=int, help="handshake PM byte")
    ap.add_argument("--sub", type=int, default=0, help="handshake SUB byte")
    ap.add_argument("--fbl", type=int, default=0,
                    help="FBL the device reported, if it reports one at all "
                         "(0 = the pm ladder derives it)")
    ap.add_argument("--mode", type=int, choices=_MODES,
                    help="FormCZTVInit's wire mode.  Omit it — a report does "
                         "not carry one and every mode is walked instead")
    ap.add_argument("--trace", action="store_true",
                    help="print the line-cited walk through the C#")
    ap.add_argument("--all", action="store_true", help="audit the whole corpus")
    ap.add_argument("--exhaustive-bulk", action="store_true",
                    help="sweep the whole bulk PM space vs the C# (the wall)")
    args = ap.parse_args()

    if args.exhaustive_bulk:
        return exhaustive_bulk()

    if args.all:
        corpus = CORPUS
    elif args.pm is not None:
        # A fingerprint the corpus does not carry used to be an ``ap.error``,
        # which is how a REPORTER's device -- the only fingerprint that matters
        # -- was the one the oracle refused.  #267 posts pm=58; the corpus has
        # 19 hand-curated rows and 15 PMs, and 58 is not among them.
        corpus = tuple(fp for fp in CORPUS
                       if fp.pm == args.pm
                       and (args.sub == 0 or fp.sub == args.sub))
        if not corpus:
            return walk(args.pm, args.sub, args.fbl, args.mode, args.trace)
    else:
        ap.error("pass --pm N (with --sub M) for one device, or --all")

    rows = [audit(fp) for fp in corpus]
    for row in rows:
        _print(row)
        if args.trace:
            _trace(row.fp)

    modelled = [r for r in rows if r.oracle_models]
    diffs = [r for r in modelled if r.diverges]
    gaps = [r for r in rows if not r.oracle_models]
    info = [r for r in modelled if r.thememl_differs]

    print(f"\n{'=' * 60}")
    if info:
        # Both sides are now the SEEDED catalog, so a row here is a real
        # difference in the mount rule -- not the old artifact of comparing our
        # orientation-0 default against their pmSub-seeded value.
        print(f"ThemeML differs on {len(info)} device(s) — both sides are the "
              "FIRST-BOOT seeded catalog, so these are real:")
        for r in info:
            # A trailing letter is a per-SKU artwork LIBRARY suffix (Levita's
            # `1600720l`), not a transposition -- a different phenomenon that
            # widening the mount rule will not touch.
            kind = ("variant suffix, not orientation"
                    if r.their_thememl.rstrip("0123456789")
                    else "mount rule")
            print(f"  - {r.fp.label}: C#={r.their_thememl} "
                  f"ours={r.our_thememl}  [{kind}]")
        print("  A mount-rule row now means a RESOLUTION gap, not a mount "
              "gap: `is_portrait_mounted` models all nine of the C#'s "
              "families, so a mismatch means the profile resolved to a "
              "resolution the panel does not have (see BULK_PM_GAP in "
              "tests/test_csharp_conformance.py).")
    if gaps:
        print(f"oracle-gap: {len(gaps)} device(s) FormCZTVInit resolves no "
              "geometry for — they fell through to the 240x320 default.")
    if diffs:
        print(f"\nMISMATCH: {len(diffs)}/{len(modelled)} modelled device(s) "
              "diverge from the C#:")
        for r in diffs:
            axes = [a for a, ok in (("fbl", r.fbl_ok), ("resolution", r.res_ok),
                                    ("widescreen", r.wide_ok)) if not ok]
            print(f"  - {r.fp.label}: {', '.join(axes)}")
        return 1
    print(f"\nOK: all {len(modelled)} modelled device(s) match the C# on "
          "fbl/resolution/widescreen.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
