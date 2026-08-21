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


# ── "installed but unreachable" — the fault an install hint cannot fix ─────


def test_off_path_binary_is_found_and_named(monkeypatch: pytest.MonkeyPatch,
                                            tmp_path: Path) -> None:
    """A real file in a real directory, not a mocked lookup.

    Mocking ``is_file`` would assert the search list against itself.  This
    plants an actual executable in a directory that is NOT on PATH and checks
    that locate() reports where it is.
    """
    planted = tmp_path / "ffmpeg7"
    planted.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    planted.chmod(0o755)
    monkeypatch.setattr("trcc.core.toolchain.shutil.which", lambda name: None)
    monkeypatch.setattr("trcc.core.toolchain._OFF_PATH_DIRS", (str(tmp_path),))

    on_path, off_path = resolve("ffmpeg"), None
    assert on_path is None
    from trcc.core.toolchain import locate
    on_path, off_path = locate("ffmpeg")
    assert on_path is None
    assert off_path == planted, "the planted binary was not located"


def test_health_says_unreachable_not_missing(monkeypatch: pytest.MonkeyPatch,
                                             tmp_path: Path) -> None:
    """The check must not tell a user to install what they already have.

    This is the whole point of the split: same symptom (`which` fails), two
    different faults, and "install it" is wrong advice for one of them.
    """
    from trcc.adapters.diagnostics import health
    from trcc.adapters.system.linux import DnfLinux

    planted = tmp_path / "7za"
    planted.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    planted.chmod(0o755)
    monkeypatch.setattr("trcc.core.toolchain.shutil.which", lambda name: None)
    monkeypatch.setattr("trcc.core.toolchain._OFF_PATH_DIRS", (str(tmp_path),))

    result = health.check_seven_zip_present(DnfLinux())
    assert "installed at" in result.message, result.message
    assert str(tmp_path) in result.message
    assert "will not help" in result.fix_hint, (
        "the hint still tells the user to install it")


def test_health_says_missing_when_it_really_is(monkeypatch: pytest.MonkeyPatch,
                                               tmp_path: Path) -> None:
    """The other side of the branch, and it must name what was searched.

    "7z not on PATH" does not tell a reporter that 7za and 7zz were tried too.
    """
    from trcc.adapters.diagnostics import health
    from trcc.adapters.system.linux import DnfLinux

    monkeypatch.setattr("trcc.core.toolchain.shutil.which", lambda name: None)
    monkeypatch.setattr("trcc.core.toolchain._OFF_PATH_DIRS", (str(tmp_path),))

    result = health.check_seven_zip_present(DnfLinux())
    assert "not found" in result.message
    assert "7za" in result.message, "the message does not say what was tried"
    assert "dnf install" in result.fix_hint


def test_installed_but_unimportable_is_not_reported_as_absent() -> None:
    """A distribution on sys.path that fails to import is present-and-broken.

    pytest itself is guaranteed installed here, so this asserts the mechanism
    without inventing a fixture package.
    """
    from trcc.core.toolchain import installed_elsewhere

    assert installed_elsewhere("pytest") is not None
    assert installed_elsewhere("definitely-not-a-distribution-xyz") is None


# ── derived install advice, and the floor under it ────────────────────────


class _StubPackages:
    """A PackageManager whose answers the test dictates."""

    def __init__(self, provides=None, argv=(), raises=False):
        self._provides, self._argv, self._raises = provides, argv, raises

    def owns(self, path):
        return None

    def provides(self, path):
        if self._raises:
            raise RuntimeError("package manager is wedged")
        return self._provides

    def install_argv(self, package):
        return self._argv


def _platform_with(monkeypatch, stub):
    from trcc.adapters.system.linux import DnfLinux
    plat = DnfLinux()
    monkeypatch.setattr(plat, "packages", lambda: stub)
    return plat


def test_advice_is_derived_when_the_manager_answers(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """A derived command cannot be stale — it is read when it is printed.

    Four static entries were verified broken on 2026-08-21 and every one had
    shipped in a release.
    """
    from trcc.adapters.diagnostics.health import _install_advice

    plat = _platform_with(monkeypatch,
                          _StubPackages(provides="7zip",
                                        argv=("sudo", "dnf", "install", "7zip")))
    assert _install_advice(plat, "7z", "/usr/bin/7z") == "sudo dnf install 7zip"


def test_advice_falls_back_when_the_manager_cannot_say(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """None means "cannot determine", and the user must be no worse off.

    Offline, no cache, no such tool — all reach here, and all must leave the
    static hint in place rather than printing nothing or a guess.
    """
    from trcc.adapters.diagnostics.health import _install_advice

    plat = _platform_with(monkeypatch, _StubPackages(provides=None))
    advice = _install_advice(plat, "7z", "/usr/bin/7z")
    assert advice == plat.software_install_hint("7z")
    assert "dnf install" in advice


def test_a_wedged_manager_does_not_break_the_doctor(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """A diagnostic must not raise.

    The doctor runs on a machine that is already misbehaving; a package
    manager holding a stale lock is exactly the state it exists to report on,
    so it must not be the thing that stops the report.
    """
    from trcc.adapters.diagnostics.health import _install_advice

    plat = _platform_with(monkeypatch, _StubPackages(raises=True))
    assert _install_advice(plat, "7z", "/usr/bin/7z") == (
        plat.software_install_hint("7z"))


def test_empty_argv_falls_back_too(monkeypatch: pytest.MonkeyPatch) -> None:
    """A manager that names a package but no install command is still a
    fallback, not an empty hint printed at the user."""
    from trcc.adapters.diagnostics.health import _install_advice

    plat = _platform_with(monkeypatch, _StubPackages(provides="7zip", argv=()))
    assert _install_advice(plat, "7z", "/usr/bin/7z") == (
        plat.software_install_hint("7z"))


def test_queries_are_bounded() -> None:
    """Every subprocess in the package layer carries a timeout.

    Read off the module rather than asserted in prose: an unbounded query on
    the doctor path is a hang on the machine least able to afford one.
    """
    from trcc.adapters.system import _packages

    assert _packages._TIMEOUT_S > 0
    source = Path(_packages.__file__).read_text(encoding="utf-8")
    for call in source.split("subprocess.run(")[1:]:
        assert "timeout=" in call.split(")")[0] + ")", (
            "a subprocess.run in _packages.py has no timeout")
