"""End-to-end Command tests for the final batch of backend ports:
update check, upgrade dry-run, slideshow service + Commands, keepalive
loop service + Command.
"""
from __future__ import annotations

import pytest

from trcc.adapters.repo.github_releases import GitHubReleases
from trcc.core._version import is_newer, parse_version
from trcc.core.errors import HttpFetchError
from trcc.core.ports import HttpFetcher
from trcc.services.slideshow import SlideshowConfig, SlideshowService

# =========================================================================
# Version parser + GitHub adapter
# =========================================================================


def test_parse_version_strips_v_prefix() -> None:
    assert parse_version("v9.6.5") == (9, 6, 5)
    assert parse_version("9.6.5") == (9, 6, 5)
    assert parse_version("9.6.5-rc1") == (9, 6, 5)


def test_parse_version_handles_garbage() -> None:
    assert parse_version("not-a-version") == (0, 0, 0)
    assert parse_version("") == (0, 0, 0)


@pytest.mark.parametrize(
    "remote,local,expected", [
        ("v9.6.6", "9.6.5", True),
        ("9.7.0",  "9.6.5", True),
        ("10.0.0", "9.6.5", True),
        ("9.6.5",  "9.6.5", False),
        ("9.6.5",  "v9.6.5", False),
        ("9.6.4",  "9.6.5", False),
        ("",       "9.6.5", False),
    ],
)
def test_is_newer(remote: str, local: str, expected: bool) -> None:
    assert is_newer(remote, local) is expected


class _FakeHttp(HttpFetcher):
    """Canned-response fetcher — maps URL → bytes."""

    def __init__(self, responses: dict[str, bytes]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    def fetch(self, url: str, timeout_s: float = 30.0) -> bytes:
        del timeout_s
        self.calls.append(url)
        if url in self.responses:
            return self.responses[url]
        raise HttpFetchError(f"unexpected URL: {url}")


def test_github_releases_parses_json_payload() -> None:
    url = (
        "https://api.github.com/repos/Lexonight1/"
        "thermalright-trcc-linux/releases/latest"
    )
    payload = b'{"tag_name": "v10.0.0", "html_url": "https://example/r"}'
    http = _FakeHttp({url: payload})
    releases = GitHubReleases(http=http)
    latest = releases.latest()
    assert latest.tag == "v10.0.0"
    assert latest.version == "10.0.0"
    assert latest.html_url == "https://example/r"


def test_github_releases_raises_on_missing_tag() -> None:
    url = (
        "https://api.github.com/repos/Lexonight1/"
        "thermalright-trcc-linux/releases/latest"
    )
    http = _FakeHttp({url: b'{}'})
    releases = GitHubReleases(http=http)
    with pytest.raises(HttpFetchError):
        releases.latest()


# =========================================================================
# Slideshow service
# =========================================================================


def test_slideshow_disabled_returns_none() -> None:
    svc = SlideshowService()
    config = SlideshowConfig(enabled=False, themes=["a", "b"], interval_s=1.0)
    assert svc.advance("k", config, now=100.0) is None


def test_slideshow_first_tick_returns_current_theme() -> None:
    svc = SlideshowService()
    config = SlideshowConfig(enabled=True, themes=["a", "b"], interval_s=10.0)
    assert svc.advance("k", config, now=100.0) == "a"


def test_slideshow_holds_within_interval() -> None:
    svc = SlideshowService()
    config = SlideshowConfig(enabled=True, themes=["a", "b"], interval_s=10.0)
    svc.advance("k", config, now=100.0)              # first tick → "a"
    assert svc.advance("k", config, now=105.0) is None   # before interval


def test_slideshow_rotates_after_interval() -> None:
    svc = SlideshowService()
    config = SlideshowConfig(enabled=True, themes=["a", "b", "c"], interval_s=10.0)
    svc.advance("k", config, now=100.0)              # → "a"
    assert svc.advance("k", config, now=111.0) == "b"
    assert svc.advance("k", config, now=122.0) == "c"
    # Wrap.
    assert svc.advance("k", config, now=133.0) == "a"


def test_slideshow_reset_drops_cursor() -> None:
    svc = SlideshowService()
    config = SlideshowConfig(enabled=True, themes=["a", "b"], interval_s=10.0)
    svc.advance("k", config, now=100.0)
    svc.advance("k", config, now=120.0)
    svc.reset("k")
    # Reset clears cursor + timer; next advance returns "a" again.
    assert svc.advance("k", config, now=130.0) == "a"


# =========================================================================
# Keepalive: the cache + resend now live in DeviceSender — see
# tests/test_device_sender.py.  (KeepaliveService absorbed, foundation #6.)
# =========================================================================


# =========================================================================
# Command-level
# =========================================================================


@pytest.fixture
def _trcc_app(fake_platform):
    from trcc.app import App
    return App(fake_platform)


def test_check_for_update_with_canned_http(_trcc_app) -> None:
    """Replace App.http via the public github_releases attribute."""
    from trcc.adapters.repo.github_releases import GitHubReleases
    from trcc.core.commands import CheckForUpdate

    url = (
        "https://api.github.com/repos/Lexonight1/"
        "thermalright-trcc-linux/releases/latest"
    )
    payload = b'{"tag_name": "v99.0.0", "html_url": "https://x"}'
    _trcc_app.github_releases = GitHubReleases(http=_FakeHttp({url: payload}))
    result = _trcc_app.dispatch(CheckForUpdate())
    assert result.ok is True
    assert result.update_available is True
    assert result.latest_version == "99.0.0"


def test_check_for_update_network_failure(_trcc_app) -> None:
    from trcc.adapters.repo.github_releases import GitHubReleases
    from trcc.core.commands import CheckForUpdate

    _trcc_app.github_releases = GitHubReleases(http=_FakeHttp({}))
    result = _trcc_app.dispatch(CheckForUpdate())
    assert result.ok is False
    assert "failed" in result.message.lower()


def test_run_upgrade_dry_run_returns_command(_trcc_app) -> None:
    from trcc.core.commands import RunUpgrade

    result = _trcc_app.dispatch(RunUpgrade(dry_run=True))
    # On a system with a detected pm, command is populated; otherwise
    # we get a structured no-pm error.  Either is a valid pass.
    if result.ok:
        assert result.command
        assert result.command[0] == "sudo"
    else:
        assert "package manager" in result.message.lower()


def test_set_slideshow_persists_state(_trcc_app) -> None:
    from trcc.core.commands import SetSlideshow

    r = _trcc_app.dispatch(
        SetSlideshow(key="0402:3922", enabled=True),
    )
    assert r.ok is True
    assert r.enabled is True
    settings = _trcc_app.settings.for_device("0402:3922")
    assert settings.slideshow_enabled is True


def test_configure_slideshow_sets_themes_and_interval(_trcc_app) -> None:
    from trcc.core.commands import ConfigureSlideshow

    r = _trcc_app.dispatch(ConfigureSlideshow(
        key="0402:3922",
        themes=("alpha", "beta"),
        interval_s=15.0,
    ))
    assert r.ok is True
    assert r.themes == ["alpha", "beta"]
    assert r.interval_s == 15.0


def test_configure_slideshow_rejects_short_interval(_trcc_app) -> None:
    from trcc.core.commands import ConfigureSlideshow

    r = _trcc_app.dispatch(ConfigureSlideshow(
        key="0402:3922", interval_s=0.1,
    ))
    assert r.ok is False
    assert "interval_s" in r.message


def test_keepalive_loop_without_cached_frame(_trcc_app) -> None:
    from trcc.core.commands import KeepAliveLoop

    r = _trcc_app.dispatch(KeepAliveLoop(key="0402:3922", count=1))
    assert r.ok is False
    assert "no cached frame" in r.message.lower()


def test_keepalive_loop_negative_count_rejected(_trcc_app) -> None:
    """``count=0`` is the loop-forever sentinel (G18 / legacy parity);
    only strictly-negative values get rejected with a count-related
    message.  Without a cached frame the loop short-circuits before
    the count branch, so we don't assert on that path here."""
    from trcc.core.commands import KeepAliveLoop

    r = _trcc_app.dispatch(KeepAliveLoop(key="0402:3922", count=-1))
    assert r.ok is False
    assert "count" in r.message.lower()


# Note: a full keepalive loop test would need a connected fake device;
# the existing test_integration_pipeline.py covers the SendFrame →
# keepalive.store path implicitly when device send succeeds.
