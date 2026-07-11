#!/usr/bin/env python3
"""FormCZTVInit branch tracer — run a device fingerprint through the C#.

A faithful, line-cited port of the C# device-onboarding function
``FormCZTVInit`` (TRCC.decompiled.cs:63298-63608).  Given a handshake
fingerprint ``(fbl, m/mode, count, ysl, pm, name, pmSub)`` it walks the SAME
branches the C# walks and prints every one it hits, then dumps the resulting
capability state (resolution flags, mode, SPI mode, ThemeML local-theme dir,
feature flags).  Data-extract only — no UI, no rendering; every `Control`
/ `FormScreenImage` side effect in the C# is reduced to the state bit it sets.

This is the device oracle from `project_decompile_miner_device_oracle`: our own
``DeviceProfile`` can't reveal its own gaps, so run the fingerprint through the
C# and diff.  Line citations point at the exact decompiled statement.

Run:  python3.12 dev/decompiler/formcztv_init.py --pm 5 --sub 1        # Mjolnir
      python3.12 dev/decompiler/formcztv_init.py --pm 11 --sub 5       # 854x480
      python3.12 dev/decompiler/formcztv_init.py --pm 1 --mode 2 --sub 48
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field


@dataclass
class CztvState:
    """Every field FormCZTVInit reads or writes, C# defaults preserved."""
    # inputs / mode
    fbl: int = 0
    myDeviceMode: int = 1
    myDeviceCount: int = 0
    myDeviceJpgYSL: int = 95
    myDevicePingMu: int = 1
    myDeviceSPIMode: int = 1          # C# default (only ever set to 2 here)
    pmSub: int = 0
    # resolution flags (all default False; C#: is480x480 = fbl==72, etc.)
    is240x240: bool = False
    is320x320: bool = False
    is360x360: bool = False
    is480x480: bool = False
    is640x480: bool = False
    is1600x720: bool = False
    is1280x480: bool = False
    is1920x462: bool = False
    is854x480: bool = False
    is960x540: bool = False
    is800x480: bool = False
    # feature flags
    isBiliPingmu: bool = False        # widescreen "split screen" mode
    isFanLcd: bool = False            # fan-hub LCD (fbl==54)
    # local-theme dir — C# field initialiser is PORTRAIT (line 62964)
    ThemeML: str = "240320\\"
    trace: list[str] = field(default_factory=list)

    def hit(self, line: int, msg: str) -> None:
        self.trace.append(f"  cs:{line:<5}  {msg}")


def form_cztv_init(
    fbl: int, m: int = 1, count: int = 0, ysl: int = 95,
    pm: int = 1, name: str | None = None, pmSub: int = 0,
) -> CztvState:
    st = CztvState(myDeviceMode=m, myDeviceCount=count, myDeviceJpgYSL=ysl,
                   myDevicePingMu=pm, pmSub=pmSub, fbl=fbl)
    st.hit(63298, f"ENTER FormCZTVInit(fbl={fbl}, m={m}, count={count}, "
                  f"ysl={ysl}, pm={pm}, name={name!r}, pmSub={pmSub})")
    st.hit(62964, f"ThemeML default = {st.ThemeML!r} (portrait, C# field init)")

    # ── switch (pm) ── :63304
    if pm == 5:
        st.fbl = 50
        st.hit(63306, "switch(pm) case 5  → fbl = 50   [Mjolnir]")
    elif pm == 7:
        st.fbl = 64
        st.hit(63309, "switch(pm) case 7  → fbl = 64")
    else:
        st.hit(63312, f"switch(pm) default (pm={pm}, mode={m})")
        if m == 2 and pm == 32:
            st.myDeviceMode = 4
            st.fbl = 50 and 100
            st.fbl = 100
            st.hit(63313, "mode==2 && pm==32 → mode=4, fbl=100")
        elif m == 2 and (pm == 64 or (pm == 1 and pmSub == 48)):
            st.isBiliPingmu = True
            st.is1600x720 = True
            st.fbl = 114
            st.hit(63318, "mode==2 && (pm==64 || pm==1&pmSub==48) → "
                          "isBiliPingmu, is1600x720, fbl=114")
        elif m == 2 and (pm == 65 or (pm == 1 and pmSub == 49)):
            st.isBiliPingmu = True
            st.is1920x462 = True
            st.fbl = 192
            st.hit(63332, "mode==2 && (pm==65 || pm==1&pmSub==49) → "
                          "isBiliPingmu, is1920x462, fbl=192")
        elif m == 2 and (pm == 9 or pm == 11):
            st.isBiliPingmu = True
            st.is854x480 = True
            st.fbl = 224
            st.hit(63346, "mode==2 && (pm==9||11) → isBiliPingmu, "
                          "is854x480, fbl=224")
        elif m == 2 and pm == 10:
            st.isBiliPingmu = True
            st.is960x540 = True
            st.fbl = 224
            st.hit(63360, "mode==2 && pm==10 → isBiliPingmu, is960x540, fbl=224")
        elif m == 2 and pm == 12:
            st.isBiliPingmu = True
            st.is800x480 = True
            st.fbl = 224
            st.hit(63374, "mode==2 && pm==12 → isBiliPingmu, is800x480, fbl=224")
        else:
            st.hit(63388, "  (no default sub-branch matched)")

    fbl = st.fbl  # subsequent tests use the (possibly-rewritten) fbl

    # ── post-switch mode/fbl gates ──
    if st.myDeviceMode == 3 and st.myDevicePingMu == 100 and fbl == 58:
        st.myDevicePingMu = 101
        st.hit(63390, "mode==3 && pm==100 && fbl==58 → pm=101")
    if st.myDeviceMode == 3 and st.myDevicePingMu == 100 and fbl == 54:
        st.isFanLcd = True
        st.myDeviceMode = 2
        st.hit(63394, "mode==3 && pm==100 && fbl==54 → isFanLcd, mode=2")
    if st.myDeviceMode == 3 and st.myDevicePingMu == 100 and fbl == 128:
        st.myDeviceMode = 2
        st.isBiliPingmu = True
        st.is1280x480 = True
        st.hit(63401, "mode==3 && pm==100 && fbl==128 → mode=2, "
                      "isBiliPingmu, is1280x480")
    if st.myDeviceMode == 1 and fbl == 51:
        st.myDeviceSPIMode = 2
        st.hit(63415, "mode==1 && fbl==51 → SPIMode=2")
    if st.myDeviceMode == 3 and fbl == 53:
        st.myDeviceSPIMode = 2
        st.hit(63419, "mode==3 && fbl==53 → SPIMode=2")

    # ── resolution flags from fbl ──
    st.is480x480 = fbl == 72
    if st.is480x480:
        st.hit(63427, "is480x480 = (fbl==72) → True")
    st.is640x480 = fbl == 64
    if st.is640x480:
        st.hit(63428, "is640x480 = (fbl==64) → True")
    if fbl in (100, 101, 102):
        st.is320x320 = True
        st.hit(63429, f"fbl=={fbl} → is320x320")
    if fbl in (36, 37):
        st.is240x240 = True
        st.hit(63433, f"fbl=={fbl} → is240x240")
    st.is360x360 = fbl == 54
    if st.is360x360:
        st.hit(63437, "is360x360 = (fbl==54) → True")

    # ── ThemeML local-theme dir (only reassigned when a flag is set) ──
    # Square / fixed-orientation panels: one dir keyed on the resolution flag.
    _SQUARE_THEME_ML: tuple[tuple[str, str, int], ...] = (
        ("is320x320", "320320", 63503), ("is360x360", "360360", 63507),
        ("is480x480", "480480", 63511), ("is240x240", "240240", 63515),
        ("is1600x720", "1600720", 63519), ("is1280x480", "1280480", 63523),
        ("is1920x462", "1920462", 63527), ("is640x480", "640480", 63531),
    )
    for flag, res_dir, line in _SQUARE_THEME_ML:
        if getattr(st, flag):
            st.ThemeML = f"{res_dir}\\"
            st.hit(line, f"ThemeML = {res_dir}")
    if st.is854x480:
        st.ThemeML = "854480\\" if pmSub < 5 else "480854\\"
        st.hit(63533, f"is854x480: pmSub{'<' if pmSub < 5 else '>='}5 "
                      f"→ ThemeML = {st.ThemeML.strip(chr(92))}")
    if st.is960x540:
        st.ThemeML = "960540\\" if pmSub < 5 else "540960\\"
        st.hit(63544, f"is960x540: pmSub → ThemeML = {st.ThemeML.strip(chr(92))}")
    if st.is800x480:
        st.ThemeML = "800480\\" if pmSub < 5 else "480800\\"
        st.hit(63555, f"is800x480: pmSub → ThemeML = {st.ThemeML.strip(chr(92))}")

    st.hit(63568, f"fileThemeDir = ...\\Data\\USBLCD\\Theme\\{st.ThemeML}")
    return st


_FLAG_NAMES = (
    "is240x240", "is320x320", "is360x360", "is480x480", "is640x480",
    "is1600x720", "is1280x480", "is1920x462", "is854x480", "is960x540",
    "is800x480", "isBiliPingmu", "isFanLcd",
)


# Resolution flag → (width, height).  When FormCZTVInit sets no resolution flag
# the C# base canvas is 320×240 (the Mjolnir / small-panel default) — see the
# pm=5 trace in project_decompile_miner_device_oracle.
_FLAG_RESOLUTION: dict[str, tuple[int, int]] = {
    "is240x240": (240, 240), "is320x320": (320, 320), "is360x360": (360, 360),
    "is480x480": (480, 480), "is640x480": (640, 480), "is1600x720": (1600, 720),
    "is1280x480": (1280, 480), "is1920x462": (1920, 462),
    "is854x480": (854, 480), "is960x540": (960, 540), "is800x480": (800, 480),
}
_DEFAULT_RESOLUTION: tuple[int, int] = (320, 240)


def resolution_of(st: CztvState) -> tuple[int, int]:
    """The (w, h) FormCZTVInit resolves for this device from its res flags.

    Faithful to the C# base canvas: exactly one resolution flag is set for the
    special panels; a device with no flag falls back to the 320×240 default.
    """
    for flag, res in _FLAG_RESOLUTION.items():
        if getattr(st, flag):
            return res
    return _DEFAULT_RESOLUTION


def _fmt_flags(st: CztvState) -> str:
    return ", ".join(n for n in _FLAG_NAMES if getattr(st, n)) or "(none)"


def main() -> int:
    ap = argparse.ArgumentParser(description="Trace FormCZTVInit branches.")
    ap.add_argument("--fbl", type=int, default=0)
    ap.add_argument("--pm", type=int, default=1)
    ap.add_argument("--mode", type=int, default=1)
    ap.add_argument("--sub", type=int, default=0, help="pmSub")
    ap.add_argument("--count", type=int, default=0)
    ap.add_argument("--ysl", type=int, default=95)
    ap.add_argument("--name", type=str, default=None)
    a = ap.parse_args()

    st = form_cztv_init(fbl=a.fbl, m=a.mode, count=a.count, ysl=a.ysl,
                        pm=a.pm, name=a.name, pmSub=a.sub)
    print(f"\nFingerprint: fbl={a.fbl} pm={a.pm} mode={a.mode} pmSub={a.sub}")
    print("─" * 66)
    print("BRANCHES HIT (in order):")
    for line in st.trace:
        print(line)
    print("─" * 66)
    print("RESULT STATE:")
    print(f"  fbl (final)      = {st.fbl}")
    print(f"  myDeviceMode     = {st.myDeviceMode}")
    print(f"  myDevicePingMu   = {st.myDevicePingMu}")
    print(f"  myDeviceSPIMode  = {st.myDeviceSPIMode}")
    print(f"  ThemeML (local)  = {st.ThemeML.strip(chr(92))!r}")
    print(f"  resolution/feat  = {_fmt_flags(st)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
