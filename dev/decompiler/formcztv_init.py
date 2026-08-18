#!/usr/bin/env python3
"""FormCZTVInit branch tracer — run a device fingerprint through the C#.

A line-cited transcription of the C# device-onboarding function
``FormCZTVInit`` (**TRCC 2.1.6**, ``TRCC.CZTV/FormCZTV.cs:858``) plus the
catalog selector it calls at the end, ``SetThemeInfo_ThemeML`` (``:1247``).
Given a handshake fingerprint ``(fbl, mode, pm, pmSub)`` it walks the SAME
branches the C# walks, prints every one it hits, and returns the resulting
capability state: resolution flags, device mode, SPI mode, feature flags,
``mySubMode`` and the local-theme directory.

Data-extract only — every ``Control`` / ``FormScreenImage`` side effect in the
C# is reduced to the state bit it sets.

**Re-anchored onto 2.1.6 on 2026-08-17.**  The previous revision was
transcribed from the 2.0.3 tree and cited ``TRCC.decompiled.cs:63298``.  It was
not merely stale, it was *load-bearingly wrong*: it had **no `mySubMode` at
all**, so it could not express the per-SKU mount that six families key their
wire rotation on, and it reported AGREE on a device we now know we ship 180
degrees out ([[project_mysubmode_is_the_per_sku_mount]]).  It was also missing
the pm 13/15/16/17/18/50/63/66/68/69 branches, the mode-3 fbl 59/60/129
branches, the ``is960x320`` / ``is1920x440`` / ``is640x172`` / ``is176x320``
flags, and it tested ``fbl == 53`` for the mode-3 SPI override where 2.1.6
tests ``fbl == 49``.

A stale oracle is worse than no oracle: it manufactures agreement.  Verify the
tree before trusting a citation::

    grep -rh AssemblyVersion ~/Downloads/TRCC_2.1.6_decompiled/Properties/*.cs
    # -> [assembly: AssemblyVersion("2.1.6.0")]

Run::

    python3.12 dev/decompiler/formcztv_init.py --pm 5             # Mjolnir
    python3.12 dev/decompiler/formcztv_init.py --pm 64 --sub 3    # Levita
    python3.12 dev/decompiler/formcztv_init.py --pm 1 --mode 2 --sub 48
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field

# The display angle FormCZTVInit passes down when the device has no saved one.
# `themeDirection == -1` is the C#'s "first boot" sentinel, and it is the only
# path on which pmSub picks the catalog ORIENTATION (`pmSub < 5` -> landscape).
FIRST_BOOT = -1


@dataclass
class CztvState:
    """Every field FormCZTVInit / SetThemeInfo_ThemeML reads or writes."""

    # ── inputs / mode ────────────────────────────────────────────────
    fbl: int = 0
    myDeviceMode: int = 1
    myDeviceCount: int = 0
    myDeviceJpgYSL: int = 95
    myDevicePingMu: int = 1
    myDeviceSPIMode: int = 1           # C# default; only ever set to 2 here
    pmSub: int = 0
    # mySubMode is the PER-SKU MOUNT discriminator six rotation families key
    # on.  It is DERIVED: FormCZTVInit assigns it on some branches and not
    # others, and 1600x720 gets its value later, from SetThemeInfo_ThemeML.
    # A resolution the C# never assigns it for keeps 0 forever, which is what
    # makes 854x480's and 800x480's `mySubMode == 2` rotation arm dead code.
    mySubMode: int = 0
    myLddValSub: int = 0               # 1600x720 only; NOT mySubMode (:890)
    themeDirection: int = FIRST_BOOT

    # ── resolution flags ─────────────────────────────────────────────
    is176x320: bool = False
    is240x240: bool = False
    is320x320: bool = False
    is360x360: bool = False
    is480x480: bool = False
    is640x172: bool = False
    is640x480: bool = False
    is800x480: bool = False
    is854x480: bool = False
    is960x320: bool = False
    is960x540: bool = False
    is1280x480: bool = False
    is1600x720: bool = False
    is1920x440: bool = False
    is1920x462: bool = False

    # ── feature flags ────────────────────────────────────────────────
    isBiliPingmu: bool = False         # widescreen "split screen" mode
    isFanLcd: bool = False             # fan-hub LCD (fbl == 54)

    ThemeML: str = "240320\\"          # C# field initialiser: PORTRAIT
    trace: list[str] = field(default_factory=list)

    def hit(self, line: int, msg: str) -> None:
        self.trace.append(f"  FormCZTV.cs:{line:<5} {msg}")

    @property
    def resolution(self) -> tuple[int, int]:
        """The (w, h) this device encodes at.

        Flag-derived, with one exception that is NOT a flag: the C# has no
        ``is320x240``.  The Mjolnir reaches that geometry through the header
        table's ``myDevicePingMu == 5`` branch, so a purely flag-driven answer
        reports it as the 240x320 default and is wrong by a transpose.
        """
        for name, wh in _FLAG_RESOLUTIONS:
            if getattr(self, name):
                return wh
        if self.myDevicePingMu == 5:
            return (320, 240)
        return (240, 320)


# Flag -> geometry, in the order the C# header table tests them.
_FLAG_RESOLUTIONS: tuple[tuple[str, tuple[int, int]], ...] = (
    ("is480x480", (480, 480)), ("is320x320", (320, 320)),
    ("is240x240", (240, 240)), ("is360x360", (360, 360)),
    ("is176x320", (176, 320)), ("is640x172", (640, 172)),
    ("is640x480", (640, 480)), ("is800x480", (800, 480)),
    ("is854x480", (854, 480)), ("is960x320", (960, 320)),
    ("is960x540", (960, 540)), ("is1280x480", (1280, 480)),
    ("is1600x720", (1600, 720)), ("is1920x440", (1920, 440)),
    ("is1920x462", (1920, 462)),
)


def form_cztv_init(
    fbl: int, m: int = 1, count: int = 0, ysl: int = 95,
    pm: int = 1, name: str | None = None, pmSub: int = 0,
    themeDirection: int = FIRST_BOOT,
) -> CztvState:
    """Walk ``FormCZTVInit`` (:858) for one handshake fingerprint."""
    st = CztvState(fbl=fbl, myDeviceMode=m, myDeviceCount=count,
                   myDeviceJpgYSL=ysl, myDevicePingMu=pm, pmSub=pmSub,
                   themeDirection=themeDirection)
    st.hit(858, f"ENTER FormCZTVInit(fbl={fbl}, m={m}, count={count}, "
                f"ysl={ysl}, pm={pm}, name={name!r}, pmSub={pmSub})")

    # ── the pm ladder — pm OVERRIDES the fbl the caller passed (:865) ──
    if pm == 5:
        st.fbl = 50
        st.hit(865, "pm == 5 -> fbl = 50   [Mjolnir]")
    elif pm in (7, 14):
        st.fbl = 64
        st.hit(869, f"pm == {pm} (7||14) -> fbl = 64")
    elif m == 2 and pm == 32:
        st.myDeviceMode, st.fbl = 4, 100
        st.hit(873, "mode==2 && pm==32 -> mode = 4, fbl = 100")
    elif m == 2 and pm == 50:
        st.myDeviceSPIMode, st.myDeviceMode, st.fbl = 2, 3, 50
        st.hit(878, "mode==2 && pm==50 -> SPIMode = 2, mode = 3, fbl = 50")
    elif m == 2 and (pm in (63, 64) or (pm == 1 and pmSub == 48)):
        st.isBiliPingmu = st.is1600x720 = True
        st.fbl = 114
        st.myLddValSub = pmSub
        st.hit(884, "mode==2 && (pm==63||64 || pm==1&&pmSub==48) -> "
                    "isBiliPingmu, is1600x720, fbl = 114")
        st.hit(890, f"  myLddValSub = pmSub = {pmSub}   "
                    f"(NOT mySubMode — 1600x720 gets that from "
                    f"SetThemeInfo_ThemeML)")
    elif m == 2 and (pm in (65, 66) or (pm == 1 and pmSub == 49)):
        st.isBiliPingmu = st.is1920x462 = True
        st.fbl = 192
        st.mySubMode = pmSub
        st.hit(898, "mode==2 && (pm==65||66 || pm==1&&pmSub==49) -> "
                    "isBiliPingmu, is1920x462, fbl = 192")
        st.hit(903, f"  mySubMode = pmSub = {pmSub}")
    elif m == 2 and pm == 69:
        st.isBiliPingmu = st.is1920x440 = True
        st.fbl = 192
        st.mySubMode = pmSub
        st.hit(913, "mode==2 && pm==69 -> isBiliPingmu, is1920x440, fbl = 192")
        st.hit(918, f"  mySubMode = pmSub = {pmSub}")
    elif m == 2 and pm == 68:
        st.isBiliPingmu = st.is1280x480 = True
        st.fbl = 192
        st.mySubMode = pmSub
        st.hit(928, "mode==2 && pm==68 -> isBiliPingmu, is1280x480, fbl = 192")
        st.hit(933, f"  mySubMode = pmSub = {pmSub}")
    elif m == 2 and pm in (9, 11):
        st.isBiliPingmu = st.is854x480 = True
        st.fbl = 224
        st.hit(943, "mode==2 && (pm==9||11) -> isBiliPingmu, is854x480, "
                    "fbl = 224")
        st.hit(943, "  NO mySubMode assignment — every sibling branch has one, "
                    "this one does not, so it stays 0 and 854x480's "
                    "`mySubMode == 2` rotation arm is UNREACHABLE")
    elif m == 2 and pm in (10, 16):
        st.isBiliPingmu = st.is960x540 = True
        st.fbl = 224
        st.mySubMode = pmSub
        st.hit(957, "mode==2 && (pm==10||16) -> isBiliPingmu, is960x540, "
                    "fbl = 224")
        st.hit(962, f"  mySubMode = pmSub = {pmSub}")
    elif m == 2 and pm == 12:
        st.isBiliPingmu = st.is800x480 = True
        st.fbl = 224
        st.hit(972, "mode==2 && pm==12 -> isBiliPingmu, is800x480, fbl = 224")
        st.hit(972, "  NO mySubMode assignment — same dead arm as 854x480")
    elif m == 2 and pm in (13, 17, 18):
        st.isBiliPingmu = st.is960x320 = True
        st.fbl = 224
        st.mySubMode = pmSub
        st.hit(986, "mode==2 && (pm==13||17||18) -> isBiliPingmu, is960x320, "
                    "fbl = 224")
        st.hit(991, f"  mySubMode = pmSub = {pmSub}")
    elif m == 2 and pm == 15:
        st.is640x172 = True
        st.fbl = 224
        st.hit(1001, "mode==2 && pm==15 -> is640x172, fbl = 224 "
                     "(no preview window, no isBiliPingmu)")
    else:
        st.hit(865, f"  (no pm-ladder branch matched: pm={pm}, mode={m})")

    fbl = st.fbl   # every test below uses the possibly-rewritten fbl

    # ── the mode-3 / pm-100 ladder (:1006) ────────────────────────────
    if st.myDeviceMode == 3 and st.myDevicePingMu == 100 and fbl == 58:
        st.myDevicePingMu = 101
        st.hit(1006, "mode==3 && pm==100 && fbl==58 -> pm = 101 "
                     "(gates the frame-skip logic)")
    elif st.myDeviceMode == 3 and st.myDevicePingMu == 100 and fbl == 54:
        st.isFanLcd = True
        st.myDeviceMode = 2
        st.hit(1010, "mode==3 && pm==100 && fbl==54 -> isFanLcd, mode = 2 "
                     "(so the fan hub encodes JPEG)")
    elif st.myDeviceMode == 3 and st.myDevicePingMu == 100 and fbl == 128:
        st.myDeviceMode = 2
        st.isBiliPingmu = st.is1280x480 = True
        st.mySubMode = st.pmSub
        st.hit(1017, "mode==3 && pm==100 && fbl==128 -> mode = 2, "
                     "isBiliPingmu, is1280x480")
        st.hit(1022, f"  mySubMode = pmSub = {st.pmSub}")
    elif st.myDeviceMode == 3 and st.myDevicePingMu == 100 and fbl == 129:
        st.fbl = fbl = 72
        st.hit(1032, "mode==3 && pm==100 && fbl==129 -> fbl = 72")
    elif st.myDeviceMode == 3 and st.myDevicePingMu == 100 and fbl == 59:
        st.is640x172 = True
        st.fbl = fbl = 224
        st.hit(1036, "mode==3 && pm==100 && fbl==59 -> is640x172, fbl = 224")
    elif st.myDeviceMode == 3 and st.myDevicePingMu == 100 and fbl == 60:
        st.is176x320 = True
        st.fbl = fbl = 224
        st.hit(1041, "mode==3 && pm==100 && fbl==60 -> is176x320, fbl = 224")

    # ── SPI override (:1046) ──────────────────────────────────────────
    if st.myDeviceMode == 1 and fbl == 51:
        st.myDeviceSPIMode = 2
        st.hit(1046, "mode==1 && fbl==51 -> SPIMode = 2")
    elif st.myDeviceMode == 3 and fbl == 49:
        st.myDeviceSPIMode = 2
        st.hit(1050, "mode==3 && fbl==49 -> SPIMode = 2  "
                     "(2.1.6 tests 49; the 2.0.3-era tracer tested 53)")

    # ── resolution flags derived from fbl (:1057) ─────────────────────
    st.is480x480 = fbl == 72
    st.is640x480 = fbl == 64
    if fbl in (100, 101, 102):
        st.is320x320 = True
    if fbl in (36, 37):
        st.is240x240 = True
    st.is360x360 = fbl == 54
    st.hit(1057, f"fbl -> flags: is480x480={st.is480x480} "
                 f"is640x480={st.is640x480} is320x320={st.is320x320} "
                 f"is240x240={st.is240x240} is360x360={st.is360x360} "
                 f"(widescreen flags come only from the pm ladder)")

    set_theme_info_theme_ml(st)
    return st


# Resolution -> (landscape token, portrait token).  Every one of these blocks
# has the identical shape in the C#: on first boot `pmSub < 5` picks landscape
# (and sets the angle to 0) or portrait (angle 90); afterwards the saved angle
# decides, `% 180 == 0` being landscape.  1600x720 is the exception and is
# handled separately because it is the only one that assigns mySubMode.
_THEME_TOKENS: tuple[tuple[str, str, str], ...] = (
    ("is176x320",  "320176",  "176320"),
    ("is1280x480", "1280480", "4801280"),
    ("is1920x462", "1920462", "4621920"),
    ("is1920x440", "1920440", "4401920"),
    ("is640x480",  "640480",  "480640"),
    ("is640x172",  "640172",  "172640"),
    ("is854x480",  "854480",  "480854"),
    ("is960x320",  "960320",  "320960"),
    ("is960x540",  "960540",  "540960"),
    ("is800x480",  "800480",  "480800"),
)

# The squares, which ignore orientation entirely (:1249-1264).
_SQUARE_TOKENS: tuple[tuple[str, str], ...] = (
    ("is320x320", "320320"), ("is360x360", "360360"),
    ("is480x480", "480480"), ("is240x240", "240240"),
)

# 1600x720 only: pmSub -> (token suffix, mySubMode).  Any other pmSub leaves
# mySubMode at 0 and takes the bare token — which is why the rotation arm
# tests `mySubMode == 3` and not `pmSub == 3`.
_LDD_SUFFIX: dict[int, tuple[str, int]] = {2: ("u", 2), 3: ("l", 3),
                                           4: ("u", 4)}


def set_theme_info_theme_ml(st: CztvState) -> None:
    """Walk ``SetThemeInfo_ThemeML(pmSub, fbl)`` (:1247).

    Picks the on-disk theme catalog directory, and — for 1600x720 ONLY —
    assigns ``mySubMode``.  That late assignment is why a Levita's rotation
    depends on a value ``FormCZTVInit`` never set.
    """
    st.hit(1247, f"ENTER SetThemeInfo_ThemeML(pmSub={st.pmSub}, fbl={st.fbl})")

    for flag, token in _SQUARE_TOKENS:
        if getattr(st, flag):
            st.ThemeML = f"{token}\\"
            st.hit(1249, f"{flag} -> ThemeML = {st.ThemeML!r} "
                         f"(squares ignore orientation)")
            return

    if st.is1600x720:
        suffix, sub_mode = _LDD_SUFFIX.get(st.pmSub, ("", 0))
        if sub_mode:
            st.mySubMode = sub_mode
            st.hit(1293, f"is1600x720 pmSub={st.pmSub} -> mySubMode = "
                         f"{sub_mode}   <-- the per-SKU mount; the rotation "
                         f"switch tests `mySubMode == 3`")
        else:
            st.hit(1293, f"is1600x720 pmSub={st.pmSub} -> no mySubMode "
                         f"(only 2, 3 and 4 assign it) — stays 0")
        landscape = st.themeDirection in (FIRST_BOOT, 0) or \
            st.themeDirection % 180 == 0
        token = f"1600720{suffix}" if landscape else f"7201600{suffix}"
        if st.themeDirection == FIRST_BOOT:
            st.themeDirection = 0
        st.ThemeML = f"{token}\\"
        st.hit(1289, f"is1600x720 -> ThemeML = {st.ThemeML!r}")
        return

    for flag, land, port in _THEME_TOKENS:
        if not getattr(st, flag):
            continue
        if st.themeDirection == FIRST_BOOT:
            # FIRST BOOT: the SUB byte picks the mount orientation.  This is
            # the same `pmSub < 5` test our port calls `portrait_mounted`.
            if st.pmSub < 5:
                st.ThemeML, st.themeDirection = f"{land}\\", 0
                st.hit(1471, f"{flag} first boot, pmSub<5 -> ThemeML = "
                             f"{st.ThemeML!r}, themeDirection = 0 (landscape)")
            else:
                st.ThemeML, st.themeDirection = f"{port}\\", 90
                st.hit(1477, f"{flag} first boot, pmSub>=5 -> ThemeML = "
                             f"{st.ThemeML!r}, themeDirection = 90 "
                             f"(PORTRAIT MOUNT)")
        else:
            landscape = st.themeDirection % 180 == 0
            st.ThemeML = f"{land if landscape else port}\\"
            st.hit(1483, f"{flag} angle={st.themeDirection} -> ThemeML = "
                         f"{st.ThemeML!r}")
        return

    st.hit(1247, f"no resolution flag set -> ThemeML stays {st.ThemeML!r} "
                 f"(the 240x320 default panel)")


def resolution_of(st: CztvState) -> tuple[int, int]:
    """The (w, h) this device encodes at — the ``CztvState.resolution`` rule.

    Kept as a module-level function because ``audit_devices`` imports it.  The
    2.0.3-era version returned 320x240 for a device with no resolution flag;
    2.1.6's header table declares **240x320** for that case (the 20-byte
    no-match branch) and reaches 320x240 only through ``myDevicePingMu == 5``.
    """
    return st.resolution


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fbl", type=int, default=0)
    ap.add_argument("--mode", type=int, default=2, help="myDeviceMode (m)")
    ap.add_argument("--pm", type=int, required=True)
    ap.add_argument("--sub", type=int, default=0, help="pmSub")
    ap.add_argument("--angle", type=int, default=FIRST_BOOT,
                    help="themeDirection; -1 = first boot (default)")
    args = ap.parse_args()

    st = form_cztv_init(args.fbl, m=args.mode, pm=args.pm, pmSub=args.sub,
                        themeDirection=args.angle)
    print(f"\nFormCZTVInit(fbl={args.fbl}, mode={args.mode}, pm={args.pm}, "
          f"pmSub={args.sub})  —  TRCC 2.1.6\n")
    for row in st.trace:
        print(row)
    w, h = st.resolution
    print(f"\n  RESULT  fbl={st.fbl}  {w}x{h}  mode={st.myDeviceMode}  "
          f"spi={st.myDeviceSPIMode}")
    print(f"          mySubMode={st.mySubMode}  myLddValSub={st.myLddValSub}  "
          f"themeDirection={st.themeDirection}")
    print(f"          isBiliPingmu={st.isBiliPingmu}  isFanLcd={st.isFanLcd}  "
          f"ThemeML={st.ThemeML!r}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
