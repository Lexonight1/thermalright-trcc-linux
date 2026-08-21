"""An alias is a claim: run it, don't list it.

``core.toolchain`` says ``7za`` provides ``7z``.  The first draft of that table
said the opposite — it excluded ``7za`` as "a reduced build", on the strength of
a shorter format list read off ``7za i``.  Both variants support 7z/zip/xz/tar/
gzip/bzip2, and the app only ever touches ``.7z``.  Running it settled in
seconds what reading about it got wrong.

So these tests do not assert the table's contents.  They take every alias
actually installed on the box and drive it through the exact commands
``adapters/repo/data_install.py`` runs — ``l -slt`` then ``x -o`` — against a
real archive.  An alias that cannot do that fails here, whatever anyone typed.

The tests skip aliases that are not installed rather than mocking them: a mock
would assert the table against itself, which is how a wrong alias survives.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from trcc.core.toolchain import TOOL_ALIASES, present, resolve

#: Aliases of 7z present on this machine.  Parametrised at collection so the
#: report names which ones were actually exercised.
_SEVEN_ZIP_PRESENT = [a for a in TOOL_ALIASES["7z"] if shutil.which(a)]


@pytest.fixture(scope="module")
def archive(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A real .7z, built by whichever 7-Zip this box has."""
    maker = resolve("7z")
    if maker is None:
        pytest.skip("no 7-Zip on this machine")
    d = tmp_path_factory.mktemp("sevenzip")
    (d / "payload.txt").write_text("trcc theme data", encoding="utf-8")
    out = d / "t.7z"
    subprocess.run([maker, "a", "-bso0", "-bsp0", str(out), str(d / "payload.txt")],
                   check=True, capture_output=True, timeout=60)
    return out


@pytest.mark.skipif(not _SEVEN_ZIP_PRESENT, reason="no 7-Zip variant installed")
@pytest.mark.parametrize("alias", _SEVEN_ZIP_PRESENT)
def test_every_7z_alias_lists_and_extracts(alias: str, archive: Path,
                                           tmp_path: Path) -> None:
    """The two commands data_install actually runs, per alias.

    ``7zr`` is in the table deliberately: it is the minimal build and supports
    little beyond the 7z format, which is the only format we use.  Excluding it
    on reputation would leave a machine that has it reporting "7z not on PATH".
    """
    listing = subprocess.run([alias, "l", "-slt", str(archive)],
                             capture_output=True, timeout=60, check=False)
    assert listing.returncode == 0, (
        f"{alias} cannot list our archives — it is in TOOL_ALIASES['7z'] and "
        f"must not be: {listing.stderr[:200]!r}"
    )

    target = tmp_path / alias
    extract = subprocess.run(
        [alias, "x", str(archive), f"-o{target}", "-y", "-bso0", "-bsp0"],
        capture_output=True, timeout=60, check=False)
    assert extract.returncode == 0, (
        f"{alias} cannot extract our archives: {extract.stderr[:200]!r}")
    assert (target / "payload.txt").read_text(encoding="utf-8") == (
        "trcc theme data"), f"{alias} extracted wrong content"


def test_resolve_prefers_the_unversioned_name() -> None:
    """Order is preference, not desperation — the plain name wins when present."""
    for tool, aliases in TOOL_ALIASES.items():
        found = resolve(tool)
        if found is None:
            continue
        installed = [a for a in aliases if shutil.which(a)]
        assert found == installed[0], (
            f"resolve({tool!r}) returned {found!r}, but {installed[0]!r} is "
            f"present and earlier in the preference order")


def test_unknown_tool_falls_back_to_its_own_name(monkeypatch: pytest.MonkeyPatch
                                                 ) -> None:
    """A new resolve() call site must not return None merely because nobody
    added a row — that would turn "we forgot the table" into "not installed"."""
    monkeypatch.setattr("trcc.core.toolchain.shutil.which",
                        lambda name: f"/usr/bin/{name}")
    assert resolve("brand-new-tool") == "brand-new-tool"


def test_absent_tool_is_none_not_a_guess(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing installed means None, so callers can say so honestly."""
    monkeypatch.setattr("trcc.core.toolchain.shutil.which", lambda name: None)
    assert resolve("ffmpeg") is None
    assert present("ffmpeg") is False


def test_a_versioned_ffmpeg_alone_is_found(monkeypatch: pytest.MonkeyPatch) -> None:
    """The NetBSD case, which no install hint could fix.

    pkgsrc has no ``ffmpeg`` package -- only ffmpeg4..7, each installing a
    versioned binary -- so every hardcoded ``which("ffmpeg")`` failed there
    even after the user installed the right thing.
    """
    monkeypatch.setattr("trcc.core.toolchain.shutil.which",
                        lambda name: "/usr/pkg/bin/ffmpeg7"
                        if name == "ffmpeg7" else None)
    assert resolve("ffmpeg") == "ffmpeg7"
    assert present("ffmpeg") is True


def test_the_fedora_case_is_found(monkeypatch: pytest.MonkeyPatch) -> None:
    """`dnf install p7zip` installs 7za and nothing named 7z.

    The program was always fine; the app could not find it.  With resolution
    that bug cannot happen, which is why this is a fix and not a workaround.
    """
    monkeypatch.setattr("trcc.core.toolchain.shutil.which",
                        lambda name: "/usr/bin/7za" if name == "7za" else None)
    assert resolve("7z") == "7za"
