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
import sys
from pathlib import Path

import pytest

# ``tomllib`` is 3.11+; this reads pyproject, which does not change with the
# Python running it, so 3.11 and 3.12 carry the gate and 3.10 skips cleanly
# instead of erroring at collection.
tomllib = pytest.importorskip("tomllib")

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

# The name mapping and the "Arch has no package for these" list live in
# dev/tools/check_program_deps.py, which is the tool that VERIFIES them against
# live distro repos.  Imported rather than copied: a second copy would drift
# from the one being checked, and then neither is trustworthy.
#
# Recorded consequence of ARCH_UNAVAILABLE, rather than hidden: on Arch,
# `trcc api` has no uvicorn and audio has no sounddevice unless the user pulls
# them from the AUR.  Fedora solves the same problem by vendoring via pip;
# Arch does not.  That gap is real and unfixed.
_DEV_TOOLS_DIR = _ROOT / "dev" / "tools"
if str(_DEV_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_DEV_TOOLS_DIR))

def _fam(manager: str):
    """The LinuxFamily row for *manager* — families are records, not classes."""
    from trcc.adapters.system.linux import _FAMILIES

    return next(f for f in _FAMILIES if f.manager == manager)


from check_program_deps import (  # noqa: E402
    _PKG_NAMES,
    DELIBERATELY_OPTIONAL,
)
from check_program_deps import (  # noqa: E402
    ARCH_UNAVAILABLE as _ARCH_UNAVAILABLE,
)
from check_program_deps import (  # noqa: E402
    arch_declared_depends as _arch_depends,
)
from check_program_deps import (  # noqa: E402
    pyproject_runtime_deps as _pyproject_linux_runtime_deps,
)

_ARCH_PKG = {name: pkgs[0] for name, pkgs in _PKG_NAMES.items()}


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
        if arch in _ARCH_UNAVAILABLE or name in DELIBERATELY_OPTIONAL:
            continue   # no package, or optional ON PURPOSE with a recorded reason
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


# ── the NVIDIA reader: TWO constraints, both true ─────────────────────

def test_neither_hid_binding_is_ever_a_hard_arch_dep() -> None:
    """Naming one FORCES THE OTHER OFF, and a Steam Deck needs the other.

    ``python-hidapi`` and ``python-hid`` ship the same ``hid`` module from two
    different projects, so pacman treats them as conflicting.  Declaring either
    as a depend therefore means "remove the one you have".  SteamOS ships
    ``python-hid`` because ``jupiter-hw-support`` requires it, so
    ``pacman -U trcc-linux`` could not resolve at all and the reporter could
    not install by any route (#293).

    We genuinely do not care which one is present: ``_HidBinding.detect()``
    iterates ``(_CythonHidBinding, _ApmortonHidBinding)`` and uses whichever
    imports, and ``open_errors()`` resolves apmorton's ``HIDException`` by name
    because it is not an ``OSError``.  Our own .deb has shipped the apmorton
    binding for releases, so the alternative is not theoretical.

    This is the nvidia trap in a second costume — an optdepend with no recorded
    reason reads as an oversight and gets "fixed" into a hard depend.  The
    reason is in ``DELIBERATELY_OPTIONAL``; this keeps it from being undone.
    """
    import sys as _sys
    _dev_tools = _ROOT / "dev" / "tools"
    if str(_dev_tools) not in _sys.path:
        _sys.path.insert(0, str(_dev_tools))
    from check_program_deps import arch_declared_depends

    declared = arch_declared_depends()
    for pkg in ("python-hidapi", "python-hid"):
        assert pkg not in declared, (
            f"Arch hard-depends on {pkg} — it conflicts with the other `hid` "
            f"provider, so this uninstalls a package the user's system may "
            f"require (#293, a Steam Deck). Keep BOTH as optdepends; "
            f"_HidBinding.detect() already probes for whichever is installed."
        )


def test_nvidia_reader_is_never_a_hard_dep() -> None:
    """It must stay OPTIONAL — a hard dep inflicts NVIDIA drivers on AMD boxes.

    #216 (em73es): the Arch package hard-depended on python-nvidia-ml-py, which
    depends on `nvidia-utils` — ~938 MB of driver + EGL stack on an AMD-only
    system. It was made an optdepend on 2026-07-09 to fix exactly that.

    On 2026-07-15 I reverted it to a hard depend "to fix #207" without asking
    why it was optional, shipped it in v9.9.0, and a user reported the 938 MB
    within hours. I then wrote a test asserting the HARD dep — encoding my own
    regression as a guard, so the next person fixing #216 would hit my test
    telling them they were wrong.

    Debian is the same trap: python3-pynvml pulls libnvidia-ml1 and lives in
    *contrib*, so a hard Depends also breaks installs where contrib is off.

    The reader is optional. Making it available is the USER's choice; making
    that choice OBVIOUS is ours — see the paired test below. Neither half can
    be "fixed" by breaking the other.
    """
    import sys as _sys
    _dev_tools = _ROOT / "dev" / "tools"
    if str(_dev_tools) not in _sys.path:
        _sys.path.insert(0, str(_dev_tools))
    from check_program_deps import arch_declared_depends, deb_declared_depends

    assert "python-nvidia-ml-py" not in arch_declared_depends(), (
        "Arch hard-depends on python-nvidia-ml-py — that drags nvidia-utils "
        "(~938 MB) onto every AMD/Intel system (#216). Keep it an optdepend "
        "and tell the user instead (software_install_hint)."
    )
    assert "python3-pynvml" not in deb_declared_depends(), (
        "the deb hard-depends on python3-pynvml — it pulls libnvidia-ml1 and "
        "is in contrib, so this inflicts NVIDIA libs on AMD users and breaks "
        "installs without contrib enabled. Keep it a Recommends."
    )


def test_nvidia_reader_advice_names_a_package_that_exists() -> None:
    """#207: an NVIDIA user must be told the RIGHT command for their distro.

    The reader being optional is only acceptable if the app says how to get
    it. One name for all of Linux is a lie — Arch's package is
    python-nvidia-ml-py, Debian/Ubuntu's is python3-pynvml. Advising the wrong
    one sends the user to a command that fails, which is #207 with extra steps.
    """
    from trcc.adapters.system.linux import (
        LinuxOS,
    )

    # Asserted through the shipping path, not a table: the per-manager names
    # moved onto the family classes on 2026-08-19.
    assert "python-nvidia-ml-py" in LinuxOS(_fam("pacman")).software_install_hint("pynvml"), (
        "Arch's package is python-nvidia-ml-py; python3-pynvml does not exist "
        "there and pacman will fail"
    )
    assert "python3-pynvml" in LinuxOS(_fam("apt")).software_install_hint("pynvml")
    assert "python3-pynvml" in LinuxOS(_fam("dnf")).software_install_hint("pynvml")
    # A family with no CONFIRMED name must not borrow Debian's — that is #207
    # in the other direction.  Alpine gets the pip fallback instead.
    assert "python3-pynvml" not in LinuxOS(_fam("apk")).software_install_hint("pynvml")


def test_python_multipart_floor_is_at_or_above_the_import_rename() -> None:
    """FastAPI probes the POST-rename import name first, so the floor must clear it.

    ``python-multipart`` renamed its module ``multipart`` -> ``python_multipart``
    in 0.0.13.  FastAPI >= 0.113 tries ``import python_multipart`` and only then
    falls back to ``multipart.multipart`` — an import name an unrelated PyPI
    project also claims.  Our floor was ``>=0.0.6``, which let a resolver
    produce a tree where that fallback is the only path, and FastAPI then
    refuses EVERY form route at import: the whole API surface, at startup, with
    a message about installing a package that is already installed.

    Reported from the field on 2026-09-21 by a contributor whose distro package
    shipped the pre-rename module; their entire API test suite could not be
    collected and they could only describe it as an environment difference.

    The gate is the RENAME (0.0.13), not the pin.  We pin 0.0.18 to match
    FastAPI's own ``standard`` extra, and that may move; what must never move
    back is the guarantee that the name FastAPI looks for exists.
    """
    rename = (0, 0, 13)

    data = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    spec = next(
        (d for d in data["project"]["dependencies"]
         if d.split("[")[0].split(">")[0].split("=")[0].strip()
         == "python-multipart"),
        None,
    )
    assert spec is not None, "python-multipart is no longer a declared dependency"

    match = re.search(r">=\s*(\d+)\.(\d+)\.(\d+)", spec)
    assert match is not None, (
        f"python-multipart must declare a >= floor, got {spec!r}"
    )
    floor = tuple(int(g) for g in match.groups())
    assert floor >= rename, (
        f"python-multipart floor {'.'.join(map(str, floor))} predates the "
        f"{'.'.join(map(str, rename))} import rename, so pip may install a "
        f"version whose module is named 'multipart' — FastAPI then refuses "
        f"every form route at import"
    )
