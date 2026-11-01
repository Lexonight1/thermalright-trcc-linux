"""The local Windows build makes the same program CI ships.

``dev/tools/build_windows.ps1`` said it "emulates windows.yml CI exactly",
and its PyInstaller calls had drifted from CI's: no GUI skin images
(``src/trcc/ui/gui/assets``) and no ``soundcard``, so a local build was a
different program from the release -- found 2026-10-19, when the local build
became the way to test an installer in the VM.  Two copies of one fact drift
unless something compares them; this compares them.
"""
from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_CI = _ROOT / ".github" / "workflows" / "windows.yml"
_LOCAL = _ROOT / "dev" / "tools" / "build_windows.ps1"


def _pyinstaller_calls(text: str, opener: str) -> dict[str, list[str]]:
    """``{--name: [every other argument]}`` for each PyInstaller call in *text*.

    Paths are compared with forward slashes and without quotes: CI writes
    ``src/trcc/assets;trcc/assets``, PowerShell ``"src\\trcc\\assets;..."``.
    """
    calls: dict[str, list[str]] = {}
    for block in re.findall(re.escape(opener) + r" `\n(.*?__main__\.py\"?)",
                            text, re.S):
        args = [line.strip().rstrip("`").strip().replace('"', "").replace("\\", "/")
                for line in block.splitlines()]
        words = [w for line in args for w in line.split(" ", 1) if w]
        name = words[words.index("--name") + 1]
        calls[name] = sorted(line for line in args
                             if line and not line.startswith("--name"))
    return calls


def test_the_local_build_runs_pyinstaller_as_ci_does() -> None:
    """MUTATION CHECK: drop the gui assets from either file and this fails."""
    ci = _pyinstaller_calls(_CI.read_text(encoding="utf-8"), "pyinstaller")
    local = _pyinstaller_calls(_LOCAL.read_text(encoding="utf-8"),
                               "python -m PyInstaller")

    assert set(ci) == {"trcc", "trcc-gui"}, f"parsed CI as {sorted(ci)}"
    assert local == ci


def test_neither_build_asks_for_elevation() -> None:
    """TRCC's service does what needed admin; an elevated exe would bring
    back the UAC prompt at every launch and the sign-in task that came with
    it -- and Windows will not start one from the Run key."""
    for path in (_CI, _LOCAL):
        flags = [line.strip() for line in path.read_text(encoding="utf-8")
                 .splitlines() if not line.lstrip().startswith("#")]
        assert "--uac-admin `" not in flags, path.name


# ── Add-on modules ship in no build ─────────────────────────────────────────

_MACOS = _ROOT / ".github" / "workflows" / "macos.yml"
_PYPROJECT = _ROOT / "pyproject.toml"
_MODULES = _ROOT / "src" / "trcc" / "modules"


def _add_ons() -> set[str]:
    """The modules no build carries -- a user installs them.  The wheel's
    exclude names each as ``src/trcc/modules/<key>/**``: the one list."""
    return set(re.findall(r'"src/trcc/modules/(\w+)/\*\*"',
                          _PYPROJECT.read_text(encoding="utf-8")))


def _built_ins() -> set[str]:
    return {p.parent.name for p in _MODULES.glob("*/module.json")} - _add_ons()


def _frozen_runs() -> dict[str, list[str]]:
    """Every PyInstaller run that makes a shipped program, by where it is:
    the Windows CI's two, the local script's two, macOS's one."""
    runs = {f"windows.yml {name}": args for name, args in _pyinstaller_calls(
        _CI.read_text(encoding="utf-8"), "pyinstaller").items()}
    runs |= {f"build_windows.ps1 {name}": args for name, args in _pyinstaller_calls(
        _LOCAL.read_text(encoding="utf-8"), "python -m PyInstaller").items()}
    for block in re.findall(r"pyinstaller \\\n(.*?__main__\.py)",
                            _MACOS.read_text(encoding="utf-8"), re.S):
        runs["macos.yml TRCC"] = [line.strip().rstrip("\\").strip().replace('"', "")
                                  for line in block.splitlines()]
    return runs


def test_the_visualizer_is_an_add_on_and_rgb_is_built_in() -> None:
    """The decision this file gates (2026-10-31 / 11-01): RGB is the one
    built-in module; the Visualizer is installed from the module index."""
    assert (_add_ons(), _built_ins()) == ({"visualizer"}, {"rgb"})


def test_every_frozen_build_carries_the_built_ins_and_no_add_on() -> None:
    """A frozen build copied ALL of ``src/trcc/modules`` and baked in every
    ``trcc`` submodule.  Measured with PyInstaller 6.22: 34 Visualizer modules
    in the archive without ``--exclude-module``, 0 with it -- a stale copy that
    would shadow the one a user installs.  MUTATION CHECK: drop the exclude
    from macos.yml, or copy the whole modules folder again."""
    runs = _frozen_runs()
    assert len(runs) == 5, sorted(runs)
    for where, args in runs.items():
        copied = {m.group(1) for line in args
                  if (m := re.match(r"--add-data src/trcc/modules(?:/(\w+))?[;:]", line))}
        excluded = {line.split()[1] for line in args
                    if line.startswith("--exclude-module trcc.modules.")}
        assert (copied, excluded) == (
            _built_ins(), {f"trcc.modules.{key}" for key in _add_ons()}), where


def test_an_add_on_is_negated_after_every_artifact_pattern() -> None:
    """Hatchling's ``artifacts`` override ``exclude``: measured, the exclude
    alone still shipped 305 of the Visualizer's asset files.  pathspec takes
    the LAST pattern that matches, so each add-on's negation comes after every
    positive one.  MUTATION CHECK: move the negation to the top of the list."""
    text = _PYPROJECT.read_text(encoding="utf-8")
    block = text[text.index("artifacts = ["):]
    patterns = re.findall(r'^\s*"([^"]+)",', block[:block.index("\n]\n")], re.M)
    negations = [p for p in patterns if p.startswith("!")]
    assert negations == [f"!src/trcc/modules/{key}/**" for key in sorted(_add_ons())]
    assert patterns[-len(negations):] == negations
