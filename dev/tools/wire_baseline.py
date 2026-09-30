#!/usr/bin/env python3
"""Wire neutrality — did a refactor change a single byte the device sees?

A "pure refactor" of the render path is a claim about BYTES, and the only
evidence for it is the bytes.  This renders every (panel × orientation ×
background origin) combination through the real ``DisplayService.build_frame``
and hashes the wire output, so a background-slot increment can be PROVED
neutral rather than asserted:

    PYTHONPATH=src python3.12 dev/tools/wire_baseline.py record before
    # ...refactor...
    PYTHONPATH=src python3.12 dev/tools/wire_baseline.py record after
    PYTHONPATH=src python3.12 dev/tools/wire_baseline.py compare before after

``compare`` exits 0 when every row matches and 1 when any moved — and prints
WHICH rows moved, so a failure says "640x480 @ 90°, user origin" instead of
just "the hash changed".  Recordings land in ``dev/.wire_baselines/``
(git-ignored scratch), the same idiom as ``panel_snapshot.py``.

**Run ``--gate`` before trusting a verdict.**  The previous instrument of this
kind reported 44/44 neutral and moved **0 of 44 rows under mutation** — its
background was canvas-sized, the one case where both fit rules agree, so the
rule it existed to watch never ran.  ``--gate`` perturbs the pipeline in five
known ways and asserts the expected rows move, then asserts a NO-OP moves
nothing.  An instrument that cannot fail certifies nothing.

Determinism: the clock is frozen, sensors are fixed, and no ffmpeg runs — the
playback arm is installed rather than decoded.  Two ``record`` runs of an
unchanged tree must produce identical files, and ``--gate`` checks that.

**What this CANNOT see, stated so nobody trusts it for more than it does.**
Every row renders once, from a cold cache, so this measures what the device
receives and nothing about how it was arrived at.  Verified by mutation: a
1px shift of the mask's composite position moves all 228 rows, while the same
1px shift applied to the CACHE KEY alone moves none — correctly, because the
pixels are identical.

So a refactor that breaks caching — a video frozen on frame 0 because its key
stopped moving, or a live source evicting every other entry from the LRU —
passes this gate cleanly.  That is the memo's own trap (*"it surfaces as 'the
panel got slower after the refactor', and no gate in this repo would see
it"*), and closing it needs a SECOND instrument that watches cache entry
counts and hit rates.  Wire neutrality and cache health are two claims; this
tool makes one of them.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from trcc.adapters.theme.filesystem import FileContentStore
from trcc.core.models import Kind, ProductInfo, Theme, Wire
from trcc.core.ports import Paths
from trcc.core.protocol import FBL_PROFILES, get_profile
from trcc.services import _clock
from trcc.services.media import MediaService, Playback
from trcc.services.overlay import OverlayService
from trcc.services.settings import Settings

log = logging.getLogger(__name__)

OUT_DIR = Path(__file__).resolve().parents[1] / ".wire_baselines"

#: The moment every row is rendered at.  ``build_frame`` calls
#: ``compute_clock()`` with no ``now``, so without freezing this the clock
#: elements redraw at a different minute and every row moves for no reason.
FROZEN_NOW = datetime(2026, 1, 2, 3, 4, 5)

#: Fixed readings — real sensors would make the baseline depend on the box.
SENSORS: dict[str, float] = {
    "cpu:temp": 61.0, "cpu:usage": 37.0, "cpu:freq": 4207.0,
    "gpu:primary:temp": 54.0, "gpu:primary:usage": 22.0,
    "gpu:primary:clock": 1815.0,
}

def _bg_size_for(resolution: tuple[int, int]) -> tuple[int, int]:
    """A background size that PARTICIPATES on this panel, at any orientation.

    Two traps, and a fixed size falls into one or the other:

    * **Canvas-sized** is the single case where the program rule (the C#
      native-or-black width test) and the user rule (``fit_mode``) AGREE, so
      the fit axis never runs.  That is what made the previous probe of this
      kind report 44/44 neutral while moving 0 rows under mutation.
    * **Oversized** is worse and less obvious: the program rule answers SOLID
      BLACK, so the background is not drawn at all and swapping the image
      changes nothing.  A fixed 777x533 did this on TEN of the eleven
      canvases — measured, after the gate reported a suspicious 84/228.

    So: strictly smaller than the canvas in BOTH orientations (fits, therefore
    drawn), never equal to it (the two rules disagree, therefore tested), and
    not a clean ratio of it.
    """
    smaller = min(resolution)
    width = smaller * 3 // 4 + 1
    return (width, width * 3 // 4 + 1)

ORIENTATIONS = (0, 90, 180, 270)

#: Each row renders one of these.  ``program`` and ``user`` differ ONLY in
#: which tree the theme sits in, which is what ``Paths.is_user_content``
#: answers and what selects the fit rule.  ``video`` pins the cyclic-source
#: path, whose cursor keying is the part most likely to break under a slot.
ORIGINS = ("program", "user", "video")


class _ToolPaths(Paths):
    """A ``Paths`` rooted at one throwaway tree.

    Implemented here rather than imported from ``tests/`` so the tool has no
    test-package dependency — ``dev/`` importing ``tests/`` is a known wart in
    this repo and this is not the place to add another.
    """

    def __init__(self, root: Path) -> None:
        log.debug("_ToolPaths: root=%s", root)
        self._root = root

    def config_dir(self) -> Path:
        return self._root / "config"

    def data_dir(self) -> Path:
        return self._root / "data"

    def user_content_dir(self) -> Path:
        return self._root / "user"

    def log_file(self) -> Path:
        return self._root / "trcc.log"


class _FixedMedia(MediaService):
    """A ``MediaService`` whose playbacks are installed, not decoded.

    The baseline must be deterministic and offline, so no ffmpeg runs.  Only
    the READ is overridden — nothing reaches into the base's private state.
    """

    def __init__(self, playbacks: dict[str, Playback]) -> None:
        super().__init__()
        log.debug("_FixedMedia: %d preloaded playback(s)", len(playbacks))
        self._fixed = playbacks

    def playback(self, device_key: str) -> Playback | None:
        return self._fixed.get(device_key)


def _png(path: Path, size: tuple[int, int], value: int) -> None:
    """Write a deterministic PNG — a flat fill plus a corner marker.

    The marker breaks the symmetry a flat fill has under rotation: without it
    a 90° error and a correct render hash the same, and the baseline would be
    blind to exactly the geometry it exists to watch.
    """
    from PySide6.QtGui import QColor, QImage, QPainter

    log.debug("_png: %s %dx%d value=%d", path.name, *size, value)
    img = QImage(size[0], size[1], QImage.Format.Format_ARGB32)
    img.fill(QColor(value & 0xFF, (value >> 8) & 0xFF, 0x40, 0xFF))
    painter = QPainter(img)
    painter.fillRect(0, 0, size[0] // 4, size[1] // 8, QColor(255, 255, 0))
    painter.fillRect(0, 0, size[0] // 16, size[1], QColor(0, 255, 255))
    painter.end()
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(path), "PNG")


def _theme_dir(root: Path, name: str, bg_size: tuple[int, int]) -> Path:
    """A theme directory with a participating background (see
    :func:`_bg_size_for`) and a mask of the same size."""
    log.debug("_theme_dir: %s/%s bg=%dx%d", root, name, *bg_size)
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "trcc.json").write_text(json.dumps({
        "name": name,
        "width": bg_size[0], "height": bg_size[1],
        "overlay_enabled": True,
        "elements": [
            {"type": "metric", "metric": "cpu:temp", "format": "{value:.0f}°C",
             "x": 20, "y": 30, "size": 22, "color": "#ffffff",
             "name": "Microsoft YaHei"},
            {"type": "metric", "metric": "gpu:primary:usage",
             "format": "{value:.0f}%", "x": 20, "y": 70, "size": 18,
             "color": "#ff8040", "name": "Microsoft YaHei"},
            {"type": "clock", "source": "time", "x": 20, "y": 110,
             "size": 20, "color": "#40ff80", "name": "Microsoft YaHei"},
            {"type": "text", "text": "TRCC", "x": 20, "y": 150,
             "size": 16, "color": "#ffffff", "name": "Microsoft YaHei"},
        ],
    }, indent=2), encoding="utf-8")
    _png(d / "00.png", bg_size, 0x2080)
    _png(d / "01.png", bg_size, 0x8020)
    return d


def _jpeg(size: tuple[int, int], value: int) -> bytes:
    """One encoded playback frame — playbacks hold ENCODED bytes."""
    from PySide6.QtCore import QBuffer, QByteArray
    from PySide6.QtGui import QColor, QImage, QPainter

    img = QImage(size[0], size[1], QImage.Format.Format_RGB888)
    img.fill(QColor(value & 0xFF, (value >> 8) & 0xFF, 0x60))
    painter = QPainter(img)
    painter.fillRect(0, 0, size[0] // 4, size[1] // 8, QColor(255, 255, 0))
    painter.end()
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QBuffer.OpenModeFlag.WriteOnly)
    img.save(buf, "JPEG", 92)
    buf.close()
    return bytes(ba)


def _info(fbl: int, resolution: tuple[int, int]) -> ProductInfo:
    """A ProductInfo for one panel.  No device, no transport — ``build_frame``
    takes the profile directly, so the wire path needs no hardware."""
    return ProductInfo(
        vid=0x0402, pid=0x3922, vendor="Thermalright", product=f"FBL{fbl}",
        wire=Wire.SCSI, kind=Kind.LCD, fbl=fbl, native_resolution=resolution,
    )


def _rows() -> list[tuple[int, int, str]]:
    """Every (fbl, orientation, origin) the baseline covers.

    All 19 FBL profiles, not the 11 distinct resolutions: two panels at the
    same size can differ in ``rotate`` / ``jpeg`` / ``encode_baseline``, so
    collapsing to resolution would drop rows the wire actually distinguishes.
    """
    return [(fbl, o, origin)
            for fbl in sorted(FBL_PROFILES)
            for o in ORIENTATIONS
            for origin in ORIGINS]


def _render_all(root: Path, *, tweak: dict[str, Any] | None = None,
                ) -> dict[str, str]:
    """Render every row and return ``row -> sha256:len`` of the WIRE bytes.

    *tweak* is used only by ``--gate`` to perturb the pipeline; a normal
    recording passes none.
    """
    from trcc.adapters.render.qt import QtRenderer
    from trcc.services.background import BackgroundSlot
    from trcc.services.display import DisplayService

    tweak = tweak or {}
    paths = _ToolPaths(root)
    store = FileContentStore()
    # One theme pair per RESOLUTION, because the background has to be sized
    # against the canvas to participate (see ``_bg_size_for``).  Built once and
    # reused across the FBLs and orientations that share a size.
    made: dict[tuple[int, int], tuple[Path, Path]] = {}

    def fixtures(resolution: tuple[int, int]) -> tuple[Path, Path]:
        if resolution not in made:
            bg = _bg_size_for(resolution)
            tag = f"{resolution[0]}x{resolution[1]}"
            made[resolution] = (
                _theme_dir(paths.data_dir() / "themes", f"program-{tag}", bg),
                _theme_dir(paths.user_content_dir() / "data", f"user-{tag}", bg),
            )
        return made[resolution]

    renderer = QtRenderer()
    out: dict[str, str] = {}
    for fbl, orientation, origin in _rows():
        profile = get_profile(fbl)
        resolution = (profile.width, profile.height)
        info = _info(fbl, resolution)
        key = info.key
        program_theme, user_theme = fixtures(resolution)
        themes: dict[str, Theme] = {
            "program": store.load(program_theme),
            "user": store.load(user_theme),
            "video": store.load(program_theme),
        }

        playbacks: dict[str, Playback] = {}
        if origin == "video":
            playbacks[key] = Playback(
                frames=[_jpeg(resolution, 0x30 + i * 0x40) for i in range(3)],
                fps=15, cursor=1,
                is_user_content=bool(tweak.get("flip_origin")),
            )

        display = DisplayService(
            renderer=renderer, themes=store,
            overlay=OverlayService(renderer),
            settings=Settings(paths), media=_FixedMedia(playbacks),
            backgrounds=BackgroundSlot(),
            paths=_FlippedPaths(root) if tweak.get("flip_origin") else paths,
        )
        s = display._settings.for_device(key)
        s.orientation = orientation
        s.mask_path = str((user_theme if origin == "user"
                           else program_theme) / "01.png")
        s.mask_visible = not tweak.get("hide_mask")
        s.mask_position = (7, 11)
        s.brightness = int(tweak.get("brightness", 100))
        s.overlay_enabled = not tweak.get("disable_overlay")
        # Pinned, not defaulted: every fixture element sits at x=20, inside the
        # Dynamic Island's 0..104 strip, so once the App's default became
        # style 2 the island covered them on 1600x720 and "overlay off" moved
        # nothing there.  Each arm must change ONE input.
        s.split_mode = 0

        theme = themes[origin]
        if tweak.get("other_background"):
            theme = store.load(_other_theme(root, resolution))

        frame = display.build_frame(
            info=info, theme=theme, sensors=SENSORS, profile=profile,
        )
        digest = hashlib.sha256(frame).hexdigest()[:16]
        out[f"{resolution[0]}x{resolution[1]}/fbl{fbl}/{orientation}/{origin}"] = (
            f"{digest}:{len(frame)}"
        )
    return out


class _FlippedPaths(_ToolPaths):
    """``is_user_content`` inverted — the gate's fit-rule perturbation.

    This is the axis a canvas-sized fixture hides.  If flipping it moves ZERO
    rows, the background is canvas-sized and the instrument is blind.
    """

    def is_user_content(self, path: Path) -> bool:
        return not super().is_user_content(path)


def _other_theme(root: Path, resolution: tuple[int, int]) -> Path:
    """A second theme, same geometry, DIFFERENT background pixels.

    Same size as the real fixture so the only thing that changes is the image
    — otherwise the gate would be moving rows by changing the fit, not the
    background, and would pass for the wrong reason.
    """
    bg = _bg_size_for(resolution)
    name = f"other-{resolution[0]}x{resolution[1]}"
    d = root / "other" / name
    if not (d / "trcc.json").exists():
        _theme_dir(root / "other", name, bg)
        _png(d / "00.png", bg, 0x00FF)
    return d


def _freeze_clock() -> None:
    """Pin ``compute_clock`` to ``FROZEN_NOW``.

    ``build_frame`` calls it with no ``now``, so a real clock would move every
    row whenever the minute rolled over — noise indistinguishable from a
    refactor changing bytes.
    """
    import trcc.services.display as display_mod

    real = _clock.compute_clock

    def frozen(*args: Any, **kwargs: Any) -> dict[str, str]:
        kwargs["now"] = FROZEN_NOW
        return real(*args, **kwargs)

    display_mod.compute_clock = frozen
    log.debug("_freeze_clock: pinned to %s", FROZEN_NOW)


def record(label: str) -> int:
    """Render every row and write ``dev/.wire_baselines/<label>.json``."""
    log.info("record: %s", label)
    _freeze_clock()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="trcc-wire-") as td:
        rows = _render_all(Path(td))
    out = OUT_DIR / f"{label}.json"
    out.write_text(json.dumps(rows, indent=1, sort_keys=True) + "\n",
                   encoding="utf-8")
    print(f"recorded {len(rows)} rows -> {out}")
    return 0


def compare(a: str, b: str) -> int:
    """Diff two recordings; name every row that moved."""
    log.info("compare: %s vs %s", a, b)
    left = json.loads((OUT_DIR / f"{a}.json").read_text(encoding="utf-8"))
    right = json.loads((OUT_DIR / f"{b}.json").read_text(encoding="utf-8"))
    only_left = sorted(set(left) - set(right))
    only_right = sorted(set(right) - set(left))
    moved = sorted(k for k in set(left) & set(right) if left[k] != right[k])

    for k in only_left:
        print(f"  MISSING in {b}: {k}")
    for k in only_right:
        print(f"  NEW in {b}:     {k}")
    for k in moved:
        print(f"  MOVED: {k}\n      {a}: {left[k]}\n      {b}: {right[k]}")

    total = len(set(left) | set(right))
    if moved or only_left or only_right:
        print(f"\n{len(moved)} moved, {len(only_left)} missing, "
              f"{len(only_right)} new — of {total} rows.  NOT wire-neutral.")
        return 1
    print(f"\n{total}/{total} rows identical — wire-neutral.")
    return 0


def _every(row: str) -> bool:
    return True


def _no_row(row: str) -> bool:
    return False


def _not_video(row: str) -> bool:
    """Rows whose background comes from the THEME, not a playback."""
    return not row.endswith("/video")


def _only_video(row: str) -> bool:
    """Rows whose background comes from a playback, which overrides the theme."""
    return row.endswith("/video")


#: Each perturbation, and exactly which rows it must and must not move.
#:
#: ``> 0 rows moved`` is a thermometer, not a gate: it passes when a
#: perturbation moves one row for an unrelated reason while the rows it was
#: aimed at sit still.  So each arm names the rows that MUST move and the rows
#: that must NOT, and the gate reports the set difference — which is what turns
#: "84/228, hmm" into "these 68 panels render the background as solid black".
#:
#: ``origin flipped`` is the arm that matters most.  It is the fit-rule axis,
#: invisible to a canvas-sized fixture, and it is what the previous probe of
#: this kind missed entirely while reporting 44/44 neutral.  Its ``required``
#: is every theme-backed row: if even one fails to move, that panel's fixture
#: has stopped exercising the rule.
_GATE: tuple[tuple[str, dict[str, Any], Any, Any, str], ...] = (
    ("no-op", {}, _no_row, _every,
     "must move NOTHING — proves the recording is deterministic"),
    ("brightness 100->50", {"brightness": 50}, _every, _no_row,
     "post-processing reaches the wire"),
    ("mask hidden", {"hide_mask": True}, _every, _no_row,
     "the mask composite reaches the wire"),
    ("overlay off", {"disable_overlay": True}, _every, _no_row,
     "metrics reach the wire"),
    ("different background", {"other_background": True}, _not_video, _only_video,
     "the theme background reaches the wire; a playback overrides it"),
    ("origin flipped", {"flip_origin": True}, _not_video, _no_row,
     "THE FIT RULE reaches the wire on every theme-backed row"),
)


def gate() -> int:
    """Perturb the pipeline in known ways and check the RIGHT rows move.

    Run this before trusting any ``compare`` verdict.  Same idea as
    ``check_program_deps.py --gate``: re-prove the instrument against known
    answers rather than assume it works.
    """
    log.info("gate: %d perturbation(s)", len(_GATE))
    _freeze_clock()
    with tempfile.TemporaryDirectory(prefix="trcc-wire-gate-") as td:
        root = Path(td)
        base = _render_all(root / "base")
        print(f"baseline: {len(base)} rows\n")
        failures: list[str] = []
        for name, tweak, required, forbidden, why in _GATE:
            shutil.rmtree(root / "arm", ignore_errors=True)
            arm = _render_all(root / "arm", tweak=tweak)
            moved = {k for k in base if base.get(k) != arm.get(k)}
            stuck = sorted(k for k in base if required(k) and k not in moved)
            leaked = sorted(k for k in moved if forbidden(k))
            ok = not stuck and not leaked
            print(f"  {'PASS' if ok else '**FAIL**'}  {name:<22} "
                  f"moved {len(moved):4d}/{len(base)}  — {why}")
            for k in stuck[:6]:
                print(f"           should have MOVED and did not: {k}")
            if len(stuck) > 6:
                print(f"           ...and {len(stuck) - 6} more")
            for k in leaked[:6]:
                print(f"           moved but should NOT have:     {k}")
            if len(leaked) > 6:
                print(f"           ...and {len(leaked) - 6} more")
            if not ok:
                failures.append(name)
    if failures:
        print(f"\nGATE FAILED: {', '.join(failures)}.  "
              f"This instrument cannot be trusted — fix it before recording.")
        return 1
    print("\nGATE PASSED — every arm moved exactly the rows it had to.")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    p_rec = sub.add_parser("record", help="render + hash every row")
    p_rec.add_argument("label")
    p_cmp = sub.add_parser("compare", help="diff two recordings")
    p_cmp.add_argument("a")
    p_cmp.add_argument("b")
    sub.add_parser("gate", help="self-test: can this instrument fail?")
    ap.add_argument("--gate", action="store_true",
                    help="alias for the gate subcommand")
    ap.add_argument("-v", "--verbose", action="count", default=0)
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=(logging.DEBUG if args.verbose > 1
               else logging.INFO if args.verbose else logging.WARNING),
        format="%(levelname)s %(name)s: %(message)s",
    )
    if args.gate or args.cmd == "gate":
        return gate()
    if args.cmd == "record":
        return record(args.label)
    if args.cmd == "compare":
        return compare(args.a, args.b)
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
