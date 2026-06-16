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


def _declared_scripts() -> set[str]:
    """Console-script names the wheel actually produces."""
    data = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    project = data.get("project", {})
    return set(project.get("scripts", {})) | set(project.get("gui-scripts", {}))


def _packaged_binaries() -> set[str]:
    """``trcc*`` binary names hardcoded in release.yml's packaging specs:
    the RPM ``%files`` ``/usr/bin/<name>`` entries + the DEB wrapper loops
    (``for cmd in <names>; do``).  The Arch build installs the wheel directly
    (globs ``trcc*``), so it has no hardcoded list to drift."""
    text = _RELEASE_YML.read_text(encoding="utf-8")
    names = set(re.findall(r"/usr/bin/(trcc[A-Za-z0-9_-]*)", text))
    for group in re.findall(r"for cmd in ([A-Za-z0-9 _-]+?);\s*do", text):
        names |= {tok for tok in group.split() if tok.startswith("trcc")}
    return names


def test_release_packaging_matches_entry_points() -> None:
    scripts = _declared_scripts()
    packaged = _packaged_binaries()
    assert scripts, "no [project.scripts] found in pyproject.toml"
    assert packaged, "no /usr/bin/trcc* binaries found in release.yml"

    stale = packaged - scripts
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
