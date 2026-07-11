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
    ``pmSub`` byte ONCE at onboarding (a hardware-mount default); ours is
    re-derived at RUNTIME from the user orientation via ``oriented_resolution``.
    They measure different things, so a difference here is *informational* — it
    flags where the C# defaults a panel to portrait (``pmSub>=5``) that our port
    defaults to landscape (orientation 0).  That gap is the likely #176/#203
    root cause (a missing initial-orientation seed), NOT a rotation-math bug —
    diffing it properly needs tracing our initial-orientation-on-connect, a
    separate follow-up.

  * **oracle-gap** rows — fingerprints our port handles that ``FormCZTVInit``
    does not branch on (the FBL 224/192 by-PM sub-splits live in a *different*
    C# function, ``FormCZTV.cs:682-821``).  Listed for honest coverage, excluded
    from pass/fail.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from formcztv_init import form_cztv_init, resolution_of

from trcc.core.models import oriented_resolution
from trcc.core.protocol import get_profile, pm_to_fbl


@dataclass(frozen=True, slots=True)
class Fingerprint:
    """A device handshake fingerprint as it reaches the two code paths.

    ``fbl`` is the handshake FBL byte the C# ``FormCZTVInit`` receives directly
    (it only rewrites it in the ``switch(pm)`` special cases).  ``pm_driven``
    marks the wires (BULK/HID/LY) whose FBL our port derives from the PM byte
    via ``pm_to_fbl``; SCSI and the fan-hub LCD report the FBL directly.
    ``oracle_models`` is False when ``FormCZTVInit`` has no branch for this
    fingerprint (its resolution is disambiguated elsewhere in the C#).
    """
    label: str
    fbl: int
    pm: int
    mode: int = 1
    sub: int = 0
    pm_driven: bool = True
    oracle_models: bool = True


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
    # --- FBL 224/192 by-PM sub-splits — NOT modelled by FormCZTVInit ---------
    Fingerprint("960x540 pm16 (224 by-PM)",  fbl=224, pm=16, oracle_models=False),
    Fingerprint("800x480 pm12 (224 by-PM)",  fbl=224, pm=12, mode=2,
                oracle_models=False),
    Fingerprint("960x320 pm13 (224 by-PM)",  fbl=224, pm=13, oracle_models=False),
    Fingerprint("640x172 pm15 (224 by-PM)",  fbl=224, pm=15, oracle_models=False),
    Fingerprint("1280x480 pm68 (192 by-PM)", fbl=192, pm=68, oracle_models=False),
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

    @property
    def fbl_ok(self) -> bool:
        return self.their_fbl == self.our_fbl

    @property
    def res_ok(self) -> bool:
        return self.their_res == self.our_res

    @property
    def wide_ok(self) -> bool:
        return self.their_wide == self.our_wide

    @property
    def diverges(self) -> bool:
        """True only for oracle-modelled rows with a verdict-axis mismatch."""
        if not self.fp.oracle_models:
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

    # ours — the shipping port for the same fingerprint.
    our_fbl = pm_to_fbl(fp.pm, fp.sub) if fp.pm_driven else fp.fbl
    profile = get_profile(our_fbl, fp.pm)
    our_res = profile.resolution
    our_wide = profile.widescreen
    # Our native catalog dir at the default orientation (0 = landscape); the
    # runtime model swaps it at 90/270.  This is the value compared, for info,
    # against the C#'s pmSub-seeded ThemeML default.
    ow, oh = oriented_resolution(our_res, 0)
    our_thememl = f"{ow}{oh}"

    return Row(fp, their_fbl, their_res, their_wide, their_thememl,
               our_fbl, our_res, our_wide, our_thememl)


def _res(r: tuple[int, int]) -> str:
    return f"{r[0]}x{r[1]}"


def _print(row: Row) -> None:
    fp = row.fp
    tag = "" if fp.oracle_models else "   [oracle-gap]"
    print(f"\n{fp.label}{tag}")
    print(f"  fingerprint: fbl={fp.fbl} pm={fp.pm} mode={fp.mode} "
          f"sub={fp.sub} pm_driven={fp.pm_driven}")
    if not fp.oracle_models:
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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pm", type=int, help="filter the corpus to this PM byte")
    ap.add_argument("--all", action="store_true", help="audit the whole corpus")
    args = ap.parse_args()

    if args.all:
        corpus = CORPUS
    elif args.pm is not None:
        corpus = tuple(fp for fp in CORPUS if fp.pm == args.pm)
        if not corpus:
            ap.error(f"no corpus device with pm={args.pm}")
    else:
        ap.error("pass --pm N for one device family, or --all")

    rows = [audit(fp) for fp in corpus]
    for row in rows:
        _print(row)

    modelled = [r for r in rows if r.fp.oracle_models]
    diffs = [r for r in modelled if r.diverges]
    gaps = [r for r in rows if not r.fp.oracle_models]
    info = [r for r in modelled if r.thememl_differs]

    print(f"\n{'=' * 60}")
    if info:
        print(f"ThemeML default differs (info, not a bug) on {len(info)} "
              "device(s) — C# seeds native orientation from pmSub, ours "
              "defaults to 0:")
        for r in info:
            print(f"  - {r.fp.label}: C#={r.their_thememl} ours={r.our_thememl}")
    if gaps:
        print(f"oracle-gap: {len(gaps)} device(s) not modelled by FormCZTVInit "
              "(224/192 by-PM — see FormCZTV.cs:682-821).")
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
