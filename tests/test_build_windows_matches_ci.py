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
