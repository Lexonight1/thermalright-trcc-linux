#!/usr/bin/env python3
"""Rotation-method tracer — run a device through the C# rotation switch.

``formcztv_init.py`` traces device onboarding (``FormCZTVInit``) and stops at
the resolved capability state.  This picks up where it leaves off: given the
same handshake fingerprint it walks the ACTUAL C# rotation dispatch —

    FormCZTV.cs:2178   myDeviceMode == 2 ? ImageToJpg(...) : ImageTo565(...)
    FormCZTV.cs:2655   ImageToJpg  directionB switch  (per resolution / pm)
    FormCZTV.cs:2976   ImageTo565  directionB switch  (square vs default)

and reports, for every user display angle (directionB ∈ {0,90,180,270}), the
exact ``RotateImg`` / ``RotateImgHei`` / ``RotateImgBu`` call the C# makes and
the angle it rotates by.  Data-extract only, line-cited, no rendering.

This answers "what method does device X utilize during a rotation change?"
without hardware — the device oracle from
``project_decompile_miner_device_oracle``.  The three primitives are identical
in rotation MATH (UCScreenImage.cs:436/473/520); Hei/Bu only add a 480×480
round-panel edge-fill (Hei → 1px black border; Bu → edge-replicate band).

Run:  python3.12 dev/decompiler/rotation_trace.py              # full fleet
      python3.12 dev/decompiler/rotation_trace.py --pm 5 --mode 2   # one device
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass

from formcztv_init import CztvState, form_cztv_init


@dataclass(frozen=True, slots=True)
class RotCall:
    """One directionB → the C# rotation call it selects."""
    method: str          # RotateImg | RotateImgHei | RotateImgBu
    angle: int           # degrees the frame is rotated by
    line: int            # decompiled statement


# ── ImageToJpg — FormCZTV.cs:2655-2711 (myDeviceMode == 2) ──────────────────
def _image_to_jpg(st: CztvState, d: int) -> tuple[RotCall, str]:
    pm = st.myDevicePingMu
    if st.is320x320 or st.is480x480:
        if pm == 6:                                          # :2657-2661
            tbl = {0: RotCall("RotateImgHei", 180, 2657),
                   90: RotCall("RotateImgHei", 90, 2658),
                   180: RotCall("RotateImg", 0, 2659),
                   270: RotCall("RotateImgHei", 270, 2660)}
            return tbl.get(d, tbl[0]), "square(320/480) pm==6  BASE 180"
        var = "RotateImgBu" if pm == 3 else "RotateImgHei"  # :2664-2668
        tbl = {0: RotCall("RotateImg", 0, 2664),
               90: RotCall(var, 270, 2665),
               180: RotCall(var, 180, 2666),
               270: RotCall(var, 90, 2667)}
        return tbl.get(d, tbl[0]), f"square(320/480) pm!=6 ({var})  BASE 0"
    if pm == 5:                                              # :2671-2675
        tbl = {0: RotCall("RotateImg", 0, 2671),
               90: RotCall("RotateImg", 270, 2672),
               180: RotCall("RotateImg", 180, 2673),
               270: RotCall("RotateImg", 90, 2674)}
        return tbl.get(d, tbl[0]), "pm==5 Mjolnir(320x240)  BASE 0"
    if st.is1600x720:                                        # :2678-2682
        tbl = {0: RotCall("RotateImg", 180, 2678),
               90: RotCall("RotateImg", 90, 2679),
               180: RotCall("RotateImg", 0, 2680),
               270: RotCall("RotateImg", 270, 2681)}
        return tbl.get(d, tbl[0]), "is1600x720  BASE 180"
    if st.is1280x480 or st.is800x480 or st.is854x480 or st.is960x540:  # :2685-2689
        tbl = {0: RotCall("RotateImg", 0, 2685),
               90: RotCall("RotateImg", 270, 2686),
               180: RotCall("RotateImg", 180, 2687),
               270: RotCall("RotateImg", 90, 2688)}
        return tbl.get(d, tbl[0]), "is1280/800/854/960  BASE 0"
    if st.is1920x462:                                        # :2692-2696
        tbl = {0: RotCall("RotateImg", 180, 2692),
               90: RotCall("RotateImg", 90, 2693),
               180: RotCall("RotateImg", 0, 2694),
               270: RotCall("RotateImg", 270, 2695)}
        return tbl.get(d, tbl[0]), "is1920x462  BASE 180"
    if st.is640x480:                                         # :2699-2703
        tbl = {0: RotCall("RotateImg", 0, 2699),
               90: RotCall("RotateImg", 270, 2700),
               180: RotCall("RotateImg", 180, 2701),
               270: RotCall("RotateImg", 90, 2702)}
        return tbl.get(d, tbl[0]), "is640x480  BASE 0"
    tbl = {0: RotCall("RotateImg", 90, 2706),                # :2706-2710 DEFAULT
           90: RotCall("RotateImg", 0, 2707),
           180: RotCall("RotateImg", 270, 2708),
           270: RotCall("RotateImg", 180, 2709)}
    return tbl.get(d, tbl[0]), "DEFAULT (360x360 / 320x240 JPEG)  BASE 90"


# ── ImageTo565 — FormCZTV.cs:2976-2990 (myDeviceMode != 2) ──────────────────
def _image_to_565(st: CztvState, d: int) -> tuple[RotCall, str]:
    if st.is240x240 or st.is320x320 or st.is480x480:        # :2978-2982
        tbl = {0: RotCall("RotateImg", 0, 2978),
               90: RotCall("RotateImg", 270, 2979),
               180: RotCall("RotateImg", 180, 2980),
               270: RotCall("RotateImg", 90, 2981)}
        return tbl.get(d, tbl[0]), "square(240/320/480)  BASE 0"
    tbl = {0: RotCall("RotateImg", 90, 2985),                # :2985-2989 DEFAULT
           90: RotCall("RotateImg", 0, 2986),
           180: RotCall("RotateImg", 270, 2987),
           270: RotCall("RotateImg", 180, 2988)}
    return tbl.get(d, tbl[0]), "DEFAULT (360x360 / 320x240 RGB565)  BASE 90"


def trace_rotation(st: CztvState) -> tuple[str, str, dict[int, RotCall]]:
    """(encoder_method, branch_desc, {directionB: RotCall}) for a device."""
    if st.myDeviceMode == 2:
        encoder, fn = "ImageToJpg", _image_to_jpg
    else:
        encoder, fn = "ImageTo565", _image_to_565
    calls: dict[int, RotCall] = {}
    branch = ""
    for d in (0, 90, 180, 270):
        calls[d], branch = fn(st, d)
    return encoder, branch, calls


# ── The fleet: real handshake fingerprints (fbl, mode, pm, sub, label) ──────
# mode is the `m` the handshake delivers; it selects the encoder (mode==2 →
# ImageToJpg/JPEG, else ImageTo565/RGB565).  JPEG panels report mode 2; the
# small RGB565 SCSI panels report mode 1; the fan-hub + 1280 report mode 3
# (FormCZTVInit promotes them to 2).  320x320 pm=32 is promoted to mode 4.
_FLEET: tuple[tuple[int, int, int, int, str], ...] = (
    # fbl, mode, pm, sub, label
    (50,  2, 5,   1,  "Mjolnir 320x240 (bulk JPEG)"),
    (51,  1, 51,  0,  "Frozen Warframe 320x240 (SCSI RGB565)"),
    (58,  1, 58,  0,  "Frozen Warframe SE 320x240 (SCSI RGB565)"),
    (72,  2, 6,   0,  "FW360 Ultra 480x480 pm=6 (bulk JPEG)"),
    (72,  1, 72,  0,  "480x480 square (SCSI RGB565)"),
    (72,  2, 50,  0,  "GrandVision/Elite 480x480 pm=50 (bulk)"),
    (100, 2, 32,  0,  "320x320 pm=32 (bulk → mode 4)"),
    (100, 1, 100, 0,  "320x320 (SCSI RGB565)"),
    (36,  1, 36,  0,  "240x240 (SCSI RGB565)"),
    (54,  3, 100, 0,  "360x360 FAN-HUB LCD (fbl 54 → mode 2)"),
    (64,  2, 7,   0,  "640x480 pm=7 (bulk JPEG)"),
    (114, 2, 64,  0,  "1600x720 Wonder/Levita pm=64 (JPEG)"),
    (114, 2, 1,   48, "1600x720 pm=1 sub=48 (JPEG)"),
    (192, 2, 65,  0,  "1920x462 pm=65 (JPEG)"),
    (224, 2, 9,   0,  "854x480 pm=9 (JPEG)"),
    (224, 2, 11,  5,  "854x480 pm=11 sub=5 (JPEG)"),
    (224, 2, 10,  0,  "960x540 pm=10 (JPEG)"),
    (224, 2, 12,  0,  "800x480 pm=12 (JPEG)"),
    (128, 3, 100, 0,  "1280x480 pm=100 fbl=128 (→ mode 2 JPEG)"),
)


def _fmt_calls(calls: dict[int, RotCall]) -> str:
    return "  ".join(
        f"{d}°→{c.method}({c.angle}°)@{c.line}" for d, c in calls.items()
    )


def _our_angles(fbl: int, pm: int, sub: int, mode: int) -> dict[int, int] | None:
    """Our shipping wire angle per orientation, for a side-by-side diff.

    Imports the real ``core.protocol`` (needs PYTHONPATH=src); returns None if
    unavailable so the C# trace still prints standalone.  Mirrors the sum the
    parity test checks: ``wire_angle + encode_baseline`` (display.py:384/1321).

    ``jpeg`` is forced to the C#'s encoder choice (``mode == 2`` → ImageToJpg),
    exactly what ``bulk_profile`` does with ``jpeg=(pm not in _RGB565_PMS)``.
    This isolates the ROTATION table from encoder resolution (covered by the
    parity test), so a device that resolves its encoder via a wire adapter
    isn't falsely flagged when only ``get_profile`` is consulted here.
    """
    try:
        import dataclasses

        from trcc.core.protocol import (  # type: ignore[import-not-found]
            get_profile,
            resolve_encode_base,
            wire_angle,
        )
    except Exception:
        return None
    profile = dataclasses.replace(get_profile(fbl, pm), jpeg=(mode == 2))
    baseline = resolve_encode_base(profile, pm)
    return {
        d: (wire_angle(profile, d, portrait_content=False) + baseline) % 360
        for d in (0, 90, 180, 270)
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Trace C# rotation dispatch.")
    ap.add_argument("--fbl", type=int)
    ap.add_argument("--pm", type=int)
    ap.add_argument("--mode", type=int, default=1)
    ap.add_argument("--sub", type=int, default=0)
    a = ap.parse_args()

    if a.pm is not None or a.fbl is not None:
        fleet = [(a.fbl or 0, a.mode, a.pm or 1, a.sub, "(cli)")]
    else:
        fleet = list(_FLEET)

    print("\nC# rotation dispatch per device (FormCZTV.cs)")
    print("=" * 78)
    for fbl, mode, pm, sub, label in fleet:
        st = form_cztv_init(fbl=fbl, m=mode, pm=pm, pmSub=sub)
        encoder, branch, calls = trace_rotation(st)
        w = next((r for f, r in _RES.items() if getattr(st, f)), (320, 240))
        print(f"\n{label}")
        print(f"  in: fbl={fbl} mode={mode} pm={pm} sub={sub}  →  "
              f"final fbl={st.fbl} mode={st.myDeviceMode} "
              f"res={w[0]}x{w[1]}")
        print(f"  method: {encoder}  ·  branch: {branch}")
        print(f"  C# angles : {_fmt_calls(calls)}")
        ours = _our_angles(st.fbl, pm, sub, st.myDeviceMode)
        if ours is not None:
            cs = {d: c.angle for d, c in calls.items()}
            agree = ours == cs
            flag = "AGREE" if agree else "DIVERGE"
            print("  our angles: "
                  + "  ".join(f"{d}°→{a}°" for d, a in ours.items())
                  + f"   [{flag}]")
            if not agree:
                diff = {d: (cs[d], ours[d]) for d in cs if cs[d] != ours[d]}
                print(f"  >>> DIVERGENCE  C#/ours: {diff}")
    print("\n" + "=" * 78)
    return 0


_RES: dict[str, tuple[int, int]] = {
    "is240x240": (240, 240), "is320x320": (320, 320), "is360x360": (360, 360),
    "is480x480": (480, 480), "is640x480": (640, 480), "is1600x720": (1600, 720),
    "is1280x480": (1280, 480), "is1920x462": (1920, 462),
    "is854x480": (854, 480), "is960x540": (960, 540), "is800x480": (800, 480),
}


if __name__ == "__main__":
    raise SystemExit(main())
