"""GitHub Releases API adapter — answers "is a newer version available?"

Hexagonal placement: this is the network adapter for the
``check_for_update`` flow.  Sits on top of the generic ``HttpFetcher``
port so tests inject a fake fetcher with canned JSON and never hit
api.github.com.

We only consume two pieces of the Releases payload — ``tag_name`` (e.g.
``v9.6.5``) and ``html_url`` (the release page).  Everything else is
ignored, which keeps the parse permissive against GitHub API drift.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from ...core.ports import HttpFetcher
from .http import HttpFetchError

log = logging.getLogger(__name__)


_RELEASES_LATEST_URL = (
    "https://api.github.com/repos/{owner}/{repo}/releases/latest"
)


@dataclass(frozen=True, slots=True)
class LatestRelease:
    """One latest-release snapshot from GitHub."""
    tag: str            # raw tag, e.g. "v9.6.5" or "9.6.5"
    version: str        # normalised "9.6.5" — leading 'v' stripped
    html_url: str       # release page (release notes)


class GitHubReleases:
    """Thin reader for ``/releases/latest`` on a single repo."""

    def __init__(
        self,
        http: HttpFetcher,
        *,
        owner: str = "Lexonight1",
        repo: str = "thermalright-trcc-linux",
    ) -> None:
        self._http = http
        self._url = _RELEASES_LATEST_URL.format(owner=owner, repo=repo)

    def latest(self) -> LatestRelease:
        """Fetch the latest release.  Raises ``HttpFetchError`` on failure."""
        body = self._http.fetch(self._url, timeout_s=15.0)
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as e:
            raise HttpFetchError(
                f"GitHub returned non-JSON for releases/latest: {e}",
            ) from e
        tag = str(payload.get("tag_name", "")).strip()
        url = str(payload.get("html_url", "")).strip()
        if not tag:
            raise HttpFetchError(
                "GitHub releases/latest response had no tag_name",
            )
        return LatestRelease(
            tag=tag, version=_strip_v_prefix(tag), html_url=url,
        )


# =========================================================================
# Version comparison — SemVer-ish, tolerant of v-prefix
# =========================================================================


_VERSION_PARTS = re.compile(r"^(\d+)\.(\d+)\.(\d+)")


def _strip_v_prefix(tag: str) -> str:
    return tag.lstrip("vV").strip() if tag else tag


def parse_version(version: str) -> tuple[int, int, int]:
    """Parse 'X.Y.Z' (with optional 'v' prefix) into a comparison tuple.

    Trailing pre-release / metadata is ignored — e.g. ``9.6.5-rc1``
    becomes ``(9, 6, 5)``.  Acceptable for "is there a newer version"
    coarse compare; we don't need PEP 440 strictness.
    """
    raw = _strip_v_prefix(version)
    match = _VERSION_PARTS.match(raw)
    if not match:
        return (0, 0, 0)
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def is_newer(remote: str, local: str) -> bool:
    """True if *remote* > *local*; tolerant of v-prefix + suffixes."""
    return parse_version(remote) > parse_version(local)
