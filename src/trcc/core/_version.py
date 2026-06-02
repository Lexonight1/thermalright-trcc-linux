"""Tolerant semver-ish version parser shared by the release checker
and the GUI's about panel.

Why centralised: before this file existed, two parsers existed.
``adapters/repo/github_releases.py`` had a regex-based parser that
tolerated leading ``v`` prefixes and trailing ``-rc1``/``+build``
suffixes — exactly the shapes GitHub release tags use.
``ui/gui/uc_about.py`` had ``tuple(int(x) for x in v.split('.'))``
which crashed on those same shapes, and it was being called against
GitHub release tags.  Centralising the tolerant parser fixes the
live crash (audit bug B3) and removes a duplicate-with-divergent-
behaviour pair.

See ``memory/project_hexagonal_solid_dry_plan`` §1 + B3.

Pure-stdlib, no project imports.
"""

from __future__ import annotations

import re

_VERSION_PARTS = re.compile(r"^(\d+)\.(\d+)\.(\d+)")


def parse_version(version: str) -> tuple[int, int, int]:
    """Parse ``X.Y.Z`` (with optional ``v`` prefix and trailing
    pre-release / metadata) into ``(major, minor, patch)``.

    Tolerates the shapes GitHub release tags actually use:
    ``v9.6.5``, ``9.6.5-rc1``, ``9.6.5+build.42``.  Returns
    ``(0, 0, 0)`` on anything that doesn't match — comparisons against
    a real version always sort the unknown one first, which gives the
    right "no upgrade available" UX rather than a stack trace.
    """
    raw = version.lstrip("vV").strip() if version else ""
    match = _VERSION_PARTS.match(raw)
    if not match:
        return (0, 0, 0)
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def is_newer(remote: str, local: str) -> bool:
    """True iff *remote* parses to a higher version tuple than *local*."""
    return parse_version(remote) > parse_version(local)
