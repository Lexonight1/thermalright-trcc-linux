#!/usr/bin/env python3
"""Import KarmaViz's shaders, palettes and presets into TRCC's assets.

KarmaViz (https://github.com/karmatripping/KarmaViz) is MIT, (c) 2025 Jason
Swint.  This reads a clone of it as DATA -- it never imports or runs a line of
it -- and writes:

* ``src/trcc/assets/shaders/karmaviz/`` -- its GLSL, beside every other
  visual's shader:
    - ``warps/<name>.glsl`` -- one warp map each (``get_pattern``);
    - ``waveforms/<name>.glsl`` -- one waveform each, whole;
    - ``line.glsl`` + ``lines/<name>.glsl`` -- the thesaurus: most waveforms
      are ONE ~30-line "draw a glowing line" skeleton copied whole, differing
      only in the few lines that shape the curve.  The skeleton is written
      once (``line.glsl``); each entry keeps only its shaping lines, as
      ``waveform_value_at()``.  A file is factored only when its opening and
      ending match the skeleton exactly, and the entry is proven to put the
      original back together.
* ``src/trcc/assets/karmaviz/`` -- ``palettes/``, ``presets/`` (KarmaViz's
  own, decoded from gzipped JSON; its loader can also ``pickle.loads`` a
  preset, so that loader is never ported), its ``LICENSE`` and a ``NOTICE``
  saying what changed.

One shader per name.  KarmaViz loads one per file name and keeps whichever
file its directory walk reaches last, so 25 names are shipped twice or more --
often an exact copy in ``basic/``, and 12 times a damaged copy (``si1(``, a
number split by spaces, an older API).  Here: a copy that does not compile is
dropped; of the rest, the category folder beats ``basic/``; a copy whose code
genuinely differs is kept, named ``<name>_<folder>``.

Compiling needs OpenGL 3 -- this runs on the dev box, through TRCC's own GL
layer::

    PYTHONPATH=src python3.12 dev/tools/import_karmaviz.py ~/src/KarmaViz
"""
from __future__ import annotations

import argparse
import difflib
import gzip
import json
import re
import shutil
import struct
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from trcc.adapters.visual._gl import (
    GL_FRAGMENT_SHADER,
    SHADERS,
    EglSurface,
    Gl,
    _compiled,
)

ASSETS = SHADERS.parent / "karmaviz"
OUT = SHADERS / "karmaviz"
#: Code at least this similar (comments and spacing ignored) is one shader.
SAME = 0.95
#: The waveform line skeleton's opening and ending, comments and spacing
#: stripped -- read off KarmaViz's ``sine_wave``; every factored file must
#: carry exactly these around its shaping lines.
OPENING = ("float compute_waveform_intensity_at_xy(float x_coord, float y_coord) "
           "{ if (!waveform_enabled || waveform_length <= 0) { return 0.0; } "
           "if (!waveform_enabled || waveform_length <= 0) "
           "{ float waveform_value = 0.0; } "
           "float norm_x = clamp(x_coord, 0.0, 1.0); "
           "float sample = texture(waveform_data, vec2(norm_x, 0.5)).r;")
ENDING = ("float waveform_y = 0.5 + waveform_value * 0.25; "
          "float line_thickness = 0.02; float glow_radius = 0.08; "
          "float distance_to_line = abs(y_coord - waveform_y); "
          "float core_intensity = smoothstep(line_thickness, 0.0, distance_to_line); "
          "float glow_intensity = exp(-distance_to_line * distance_to_line "
          "/ (glow_radius * glow_radius)) * 0.3; "
          "return clamp(core_intensity + glow_intensity, 0.0, 1.0); }")
ENDING_FIRST = "float waveform_y = 0.5 + waveform_value * 0.25;"
#: The skeleton, written once: the opening without KarmaViz's dead second
#: ``if`` (it declared a variable nothing read), then the shaping call, then
#: the ending exactly as the originals carry it.
LINE = '''\
// KarmaViz's waveform line, written once: most of its waveforms are this
// skeleton copied whole, differing only in waveform_value_at() -- the entries
// under lines/.  The ending is KarmaViz's own.
// From KarmaViz {commit} waveforms/basic/sine_wave.kvwf, MIT (c) 2025 Jason
// Swint: see assets/karmaviz/LICENSE.  Changed: the shaping lines moved out.
float compute_waveform_intensity_at_xy(float x_coord, float y_coord) {{
    if (!waveform_enabled || waveform_length <= 0) {{
        return 0.0;
    }}
    float norm_x = clamp(x_coord, 0.0, 1.0);
    float sample = texture(waveform_data, vec2(norm_x, 0.5)).r;
    float waveform_value = waveform_value_at(x_coord, y_coord, norm_x, sample);

{ending}'''


@dataclass(frozen=True)
class Shader:
    """One KarmaViz ``.kvwf`` (waveform) or ``.kvwm`` (warp map)."""
    path: Path            # relative to the clone
    name: str
    category: str
    description: str
    author: str
    code: str

    @property
    def is_wave(self) -> bool:
        return self.path.suffix == ".kvwf"

    @property
    def stem(self) -> str:
        return self.path.stem


def read_shader(root: Path, path: Path) -> Shader:
    """Parse KarmaViz's container: a 4-byte magic, a u32 version, one byte,
    then seven u32-length-prefixed UTF-8 strings."""
    data = path.read_bytes()
    if data[:4] not in (b"KVWF", b"KVWM"):
        raise ValueError(f"{path}: not a KarmaViz shader ({data[:4]!r})")
    offset, fields = 9, []
    for _ in range(7):
        (size,) = struct.unpack_from("<I", data, offset)
        offset += 4
        fields.append(data[offset:offset + size].decode("utf-8"))
        offset += size
    name, category, description, _level, author, _version, code = fields
    return Shader(path.relative_to(root), name, category, description, author, code)


def plain(code: str) -> str:
    """*code* without comments, its spacing collapsed -- what it SAYS."""
    return re.sub(r"\s+", " ", re.sub(r"//[^\n]*", "", code)).strip()


class Compiler:
    """Compiles a waveform or warp map inside KarmaViz's own frame shader,
    on TRCC's GL layer -- the test that drops a damaged copy."""

    def __init__(self, root: Path, sine: str) -> None:
        text = (root / "shaders" / "shaders.py").read_text(encoding="utf-8")
        found = re.search(r'FRAGMENT_SHADER = """//glsl\n(.*?)"""', text, re.DOTALL)
        if found is None:
            raise ValueError("KarmaViz's shaders.py has no FRAGMENT_SHADER")
        self._frame = found.group(1)
        self._sine = sine
        self._surface = EglSurface.open()
        self.gl: Gl = self._surface.gl
        print(f"compiling on {self._surface.describe()}")

    def why_not(self, wave: str | None = None, warp: str | None = None) -> str:
        """"" when it compiles, else the driver's first complaint."""
        source = self._frame.replace("// WAVEFORM_RENDER_PLACEHOLDER", wave or self._sine)
        if warp is None:
            source = source.replace("// WARP_DISPATCHER_PLACEHOLDER", "return pos;")
        else:
            source = (source.replace("// WARP_MAP_FUNCTIONS_PLACEHOLDER", warp)
                      .replace("// WARP_DISPATCHER_PLACEHOLDER", "return get_pattern(pos, t);"))
        made, why = _compiled(self.gl, GL_FRAGMENT_SHADER, source)
        return "" if made else (why.strip().splitlines() or ["refused"])[0]

    def close(self) -> None:
        self._surface.close()


def choose(shaders: list[Shader],
           compiler: Compiler) -> tuple[dict[tuple[bool, str], Shader], list[str]]:
    """One shader per name -- the rule in this module's docstring.  Returns
    ``{(is_wave, output name): shader}`` -- waveforms and warp maps are two
    namespaces (``cellular_growth`` is both) -- and a line per decision."""
    groups: dict[tuple[bool, str], list[Shader]] = {}
    for s in shaders:
        groups.setdefault((s.is_wave, s.stem), []).append(s)
    chosen: dict[tuple[bool, str], Shader] = {}
    notes: list[str] = []
    for (_wave, stem), copies in sorted(groups.items()):
        working = []
        for s in copies:
            why = compiler.why_not(wave=s.code) if s.is_wave else compiler.why_not(warp=s.code)
            if why:
                notes.append(f"dropped {s.path} -- does not compile: {why}")
            else:
                working.append(s)
        working.sort(key=lambda s: (s.path.parent.name.lower() == "basic", str(s.path)))
        kept: list[Shader] = []
        for s in working:
            twin = next((k for k in kept if difflib.SequenceMatcher(
                None, plain(k.code), plain(s.code), autojunk=False).ratio() >= SAME), None)
            if twin is not None:
                notes.append(f"dropped {s.path} -- the same shader as {twin.path}")
            else:
                kept.append(s)
        for i, s in enumerate(kept):
            out = stem if i == 0 else f"{stem}_{s.path.parent.name.lower()}"
            if i:
                notes.append(f"renamed {s.path} to {out} -- a different shader under the same name")
            if (s.is_wave, out) in chosen:
                raise ValueError(f"two shaders would be written as {out}")
            chosen[(s.is_wave, out)] = s
    return chosen, notes


def shaping_lines(code: str) -> str | None:
    """The lines between the skeleton's opening and ending, when *code* is
    exactly the skeleton around them (and they set ``waveform_value`` once,
    with no early ``return``); else ``None``."""
    said = plain(code)
    if not (said.startswith(OPENING) and said.endswith(ENDING)):
        return None
    lines = code.splitlines()
    start = next(i for i, line in enumerate(lines) if "float sample = texture(" in line) + 1
    end = next(i for i, line in enumerate(lines) if plain(line) == ENDING_FIRST)
    while end > start and plain(lines[end - 1]) == "":
        end -= 1                     # the ending's own comment and blank lines
    middle = lines[start:end]
    body = "\n".join(middle)
    if (len(re.findall(r"\bfloat\s+waveform_value\s*=", body)) != 1
            or re.search(r"\breturn\b", body)):
        return None
    if plain(OPENING + " " + body + " " + ENDING) != said:
        raise AssertionError("the entry does not put the original back together")
    while middle and not middle[0].strip():
        middle.pop(0)
    indent = min(len(line) - len(line.lstrip()) for line in middle if line.strip())
    return "\n".join("    " + line[indent:] if line.strip() else "" for line in middle)


def header(s: Shader, commit: str, kind: str, extra: str = "") -> str:
    desc = s.description.strip().replace("\n", " ")
    lines = [f'// KarmaViz {kind} "{s.name}" ({s.category}): {desc}',
             f"// From KarmaViz {commit} {s.path.as_posix()}, MIT (c) 2025 Jason",
             "// Swint: see assets/karmaviz/LICENSE."]
    if extra:
        lines.append(f"// {extra}")
    return "\n".join(lines) + "\n"


def write_shaders(chosen: dict[tuple[bool, str], Shader], commit: str,
                  compiler: Compiler) -> dict[str, int]:
    for folder in ("warps", "waveforms", "lines"):
        shutil.rmtree(OUT / folder, ignore_errors=True)
        (OUT / folder).mkdir(parents=True)
    ending = next(s for s in chosen.values() if s.path.name == "sine_wave.kvwf").code
    ending = ending[ending.index("    // Render as horizontal waveform line"):].rstrip() + "\n"
    line = LINE.format(commit=commit, ending=ending)
    (OUT / "line.glsl").write_text(line, encoding="utf-8")
    counts = {"warps": 0, "waveforms": 0, "lines": 0}
    for (_wave, out), s in sorted(chosen.items()):
        if not s.is_wave:
            text = header(s, commit, "warp map") + s.code.strip("\n") + "\n"
            folder = "warps"
        elif (shaping := shaping_lines(s.code)) is not None:
            text = (header(s, commit, "waveform",
                           "Its shaping lines only: karmaviz/line.glsl draws the line.")
                    + "float waveform_value_at(float x_coord, float y_coord, "
                      "float norm_x, float sample) {\n"
                    + shaping + "\n    return waveform_value;\n}\n")
            folder = "lines"
            if why := compiler.why_not(wave=text + line):
                raise AssertionError(f"lines/{out}: the entry does not compile: {why}")
        else:
            text = header(s, commit, "waveform") + s.code.strip("\n") + "\n"
            folder = "waveforms"
        (OUT / folder / f"{out}.glsl").write_text(text, encoding="utf-8")
        counts[folder] += 1
    return counts


def write_data(root: Path, names: set[tuple[bool, str]], commit: str,
               notes: list[str]) -> tuple[int, int]:
    """Palettes verbatim; KarmaViz's own presets decoded; the licence; a NOTICE."""
    shutil.rmtree(ASSETS, ignore_errors=True)
    (ASSETS / "palettes").mkdir(parents=True)
    (ASSETS / "presets").mkdir(parents=True)
    palettes = 0
    for p in sorted((root / "palettes").glob("*.json")):
        data = json.loads(p.read_text(encoding="utf-8"))
        if not (isinstance(data.get("name"), str) and data.get("colors")
                and all(len(c) == 3 for c in data["colors"])):
            raise ValueError(f"{p}: not a palette")
        shutil.copyfile(p, ASSETS / "palettes" / p.name)
        palettes += 1
    presets = 0
    for p in sorted((root / "presets").glob("*.kviz")):
        raw = p.read_bytes()
        if raw[:4] != b"KVIZ" or (start := raw.find(b"\x1f\x8b")) < 0:
            raise ValueError(f"{p}: not a gzipped KarmaViz preset")
        data = json.loads(gzip.decompress(raw[start:]))   # never pickle
        if data.get("author") != "KarmaViz":
            notes.append(f"left out preset {p.name} -- by {data.get('author')!r}, not KarmaViz")
            continue
        wave = data["waveform_info"]["current_waveform_name"]
        warp = data["warp_map_info"]["active_warp_map_name"]
        for is_wave, kind, ref in ((True, "waveform", wave), (False, "warp map", warp)):
            if ref and (is_wave, ref) not in names:
                raise ValueError(f"{p.name}: its {kind} {ref!r} was not imported")
        for machine_local in ("file_path", "thumbnail_path"):
            data.pop(machine_local, None)
        (ASSETS / "presets" / f"{p.stem}.json").write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        presets += 1
    shutil.copyfile(root / "LICENSE.md", ASSETS / "LICENSE")
    (ASSETS / "NOTICE").write_text(
        f"KarmaViz {commit} -- https://github.com/karmatripping/KarmaViz\n"
        "MIT (c) 2025 Jason Swint; the licence is LICENSE, beside this file.\n"
        "It covers these palettes and presets, and the GLSL under\n"
        "assets/shaders/karmaviz/.  Imported by dev/tools/import_karmaviz.py.\n\n"
        "Changed from the original:\n"
        "- shaders: converted from .kvwf/.kvwm to .glsl; one per name;\n"
        "  the waveform line skeleton written once (line.glsl), the entries\n"
        "  under lines/ keeping only their shaping lines;\n"
        "- presets: decoded from gzipped JSON; machine-local paths removed.\n\n"
        + "\n".join(f"- {n}" for n in notes) + "\n", encoding="utf-8")
    return palettes, presets


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("clone", type=Path, help="a clone of KarmaViz")
    root = parser.parse_args().clone.resolve()
    commit = subprocess.run(["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
                            capture_output=True, text=True, check=True).stdout.strip()
    # Exactly the folders KarmaViz's own loaders walk -- a .kvwf elsewhere in
    # the repo (one sits at its root) is never loaded by KarmaViz either.
    files = sorted([*(root / "waveforms").rglob("*.kvwf"),
                    *(root / "warp_maps").rglob("*.kvwm")])
    shaders = [read_shader(root, p) for p in files]
    sine = next(s for s in shaders if s.path.as_posix() == "waveforms/basic/sine_wave.kvwf")
    compiler = Compiler(root, sine.code)
    try:
        chosen, notes = choose(shaders, compiler)
        counts = write_shaders(chosen, commit, compiler)
    finally:
        compiler.close()
    palettes, presets = write_data(root, set(chosen), commit, notes)
    print(f"KarmaViz {commit}: {len(shaders)} shader files -> {len(chosen)} shaders "
          f"({counts['warps']} warp maps, {counts['waveforms']} whole waveforms, "
          f"{counts['lines']} line entries + line.glsl); {palettes} palettes, "
          f"{presets} presets")
    for note in notes:
        print(" ", note)
    return 0


if __name__ == "__main__":
    sys.exit(main())
