"""Guard: the distro packaging file-lists must match ``[project.scripts]``.

The Linux release hardcodes the console-script binary names in two places — the
RPM ``%files`` list and the DEB ``postinst``/``prerm`` wrapper loops.  When the
entry points changed at the next→root cutover (``trcc-detect`` / ``trcc-test`` /
``trcc-next`` removed), ``release.yml`` was NOT updated, so the RPM build failed
with ``File not found: /usr/bin/trcc-detect`` — and it went unnoticed for two
releases (v9.7.0, v9.7.1) because the packaging job only runs on a TAG push,
never on regular CI.

This test runs in the normal suite (every push / PR), so the drift is caught the
moment someone touches ``[project.scripts]`` — not silently at the next release.
"""
from __future__ import annotations

import re
from pathlib import Path

import tomllib

_ROOT = Path(__file__).resolve().parent.parent
_PYPROJECT = _ROOT / "pyproject.toml"
_RELEASE_YML = _ROOT / ".github" / "workflows" / "release.yml"
_RPM_SPEC = _ROOT / "packaging" / "rpm" / "trcc-linux.spec"

# Binaries the packaging installs directly from ``src/trcc/assets/`` (NOT wheel
# console scripts), so they legitimately appear in the file-lists without a
# matching ``[project.scripts]`` entry.  ``trcc-imc`` is the standalone
# privileged MCHBAR reader (pkexec target).
_ASSET_HELPERS = frozenset({"trcc-imc"})


def _declared_scripts() -> set[str]:
    """Console-script names the wheel actually produces."""
    data = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    project = data.get("project", {})
    return set(project.get("scripts", {})) | set(project.get("gui-scripts", {}))


def _packaged_binaries() -> set[str]:
    """``trcc*`` binary names hardcoded in the packaging specs:
    release.yml's RPM ``%files`` (``/usr/bin/<name>``) + DEB wrapper loops
    (``for cmd in <names>; do``), AND the standalone RPM spec's ``%files``
    (``%{_bindir}/<name>``).  The Arch build installs the wheel directly
    (globs ``trcc*``), so it has no hardcoded list to drift."""
    text = _RELEASE_YML.read_text(encoding="utf-8")
    names = set(re.findall(r"/usr/bin/(trcc[A-Za-z0-9_-]*)", text))
    for group in re.findall(r"for cmd in ([A-Za-z0-9 _-]+?);\s*do", text):
        names |= {tok for tok in group.split() if tok.startswith("trcc")}
    if _RPM_SPEC.is_file():
        spec = _RPM_SPEC.read_text(encoding="utf-8")
        names |= set(re.findall(r"%\{_bindir\}/(trcc[A-Za-z0-9_-]*)", spec))
    return names


def test_release_packaging_matches_entry_points() -> None:
    scripts = _declared_scripts()
    packaged = _packaged_binaries()
    assert scripts, "no [project.scripts] found in pyproject.toml"
    assert packaged, "no /usr/bin/trcc* binaries found in release.yml"

    stale = packaged - scripts - _ASSET_HELPERS
    assert not stale, (
        "release.yml packages binaries with no matching [project.scripts] "
        f"entry — the RPM/DEB build will fail with 'File not found': {sorted(stale)}"
    )
    missing = scripts - packaged
    assert not missing, (
        f"[project.scripts] entries not packaged in release.yml: {sorted(missing)}"
    )


def test_release_docker_comments_have_no_quote_breakers() -> None:
    """Shell comments inside the single-quoted ``docker ... bash -c '...'`` blocks
    must not contain apostrophes or backticks.

    An apostrophe (e.g. ``isn't``) prematurely CLOSES the single-quoted block, so
    the host shell then runs the next word as a command — the second latent bug
    the entry-point fix uncovered (``line 28: on: command not found``, DEB build
    exit 127).  A backtick becomes command substitution once the quote is broken.
    These blocks are apostrophe-free by construction, so any in a deeply-indented
    shell comment is a release-time break — caught here on every push.
    """
    text = _RELEASE_YML.read_text(encoding="utf-8")
    offenders = [
        (i, line.strip())
        for i, line in enumerate(text.splitlines(), 1)
        if re.match(r"\s{8,}#", line) and ("'" in line or "`" in line)
    ]
    assert not offenders, (
        "apostrophe/backtick in a shell comment inside a single-quoted docker "
        f"block — breaks quoting at release time: {offenders}"
    )


# ── Arch runtime dependencies ─────────────────────────────────────────

# pyproject name -> Arch package name.  Only names that differ or need the
# `python-` prefix are interesting; the mapping is explicit so a rename shows
# up as a test failure rather than a silent skip.
_ARCH_PKG = {
    "PySide6": "pyside6",
    "numpy": "python-numpy",
    "psutil": "python-psutil",
    "pyusb": "python-pyusb",
    "pyudev": "python-pyudev",
    "click": "python-click",
    "typer": "python-typer",
    "fastapi": "python-fastapi",
    "prompt_toolkit": "python-prompt_toolkit",
    "python-multipart": "python-multipart",
    "certifi": "python-certifi",
    "nvidia-ml-py": "python-nvidia-ml-py",
    "uvicorn": "python-uvicorn",
    "sounddevice": "python-sounddevice",
}

# Hard deps in pyproject that Arch does NOT ship in its official repos, so a
# `depend =` line would make `pacman -U` fail to resolve for every user.  These
# are knowingly absent; anything else missing is a bug.  Verified against
# archlinux.org/packages: both are AUR-only.
#
# Consequence, recorded honestly rather than hidden: on Arch, `trcc api` has no
# uvicorn and audio features have no sounddevice unless the user installs them
# from the AUR.  Fedora solves the same problem by vendoring via pip into the
# package (release.yml); Arch does not.  That gap is real and unfixed.
_ARCH_UNAVAILABLE = {"python-uvicorn", "python-sounddevice"}


def _arch_depends() -> set[str]:
    text = _RELEASE_YML.read_text()
    start = text.index("          depend = python\n")
    block = text[start:text.index("          INFO", start)]
    return set(re.findall(r"^\s*depend = (\S+)", block, re.M))


def _pyproject_linux_runtime_deps() -> list[str]:
    data = tomllib.loads(_PYPROJECT.read_text())
    out = []
    for spec in data["project"]["dependencies"]:
        if "sys_platform == 'win32'" in spec:
            continue
        out.append(re.split(r"[><=;\[]", spec)[0].strip())
    return out


def test_arch_package_declares_every_hard_runtime_dep() -> None:
    """pacman does not read pyproject.toml.

    `nvidia-ml-py` was made a hard dependency to fix "no NVIDIA GPU sensors"
    (#207, #161), but the Arch spec listed it as an `optdepend` — so on
    Arch/CachyOS it was simply never installed, the reader stayed missing, and
    the fix never reached the reporter who asked for it.  Every hard runtime
    dep must be declared for the distro too, or it does not ship.
    """
    declared = _arch_depends()
    missing: list[str] = []
    for name in _pyproject_linux_runtime_deps():
        arch = _ARCH_PKG.get(name)
        assert arch is not None, (
            f"{name!r} is a hard dependency but has no Arch package mapping — "
            f"add it to _ARCH_PKG (and to the release.yml depends) or record "
            f"why Arch cannot have it"
        )
        if arch in _ARCH_UNAVAILABLE:
            continue
        if arch not in declared:
            missing.append(f"{name} -> {arch}")
    assert not missing, (
        "Arch package under-declares hard runtime dependencies "
        f"(pacman will not install them): {missing}"
    )


def test_arch_unavailable_list_has_no_stale_entries() -> None:
    """The exception list must not outlive its reason.

    If a package lands in Arch's repos and gets a `depend =` line, its excuse
    here is stale and should be deleted — otherwise the list quietly grows into
    a place where real gaps hide.
    """
    declared = _arch_depends()
    stale = _ARCH_UNAVAILABLE & declared
    assert not stale, (
        f"{stale} are declared as Arch depends but still listed as unavailable "
        f"— remove them from _ARCH_UNAVAILABLE"
    )
