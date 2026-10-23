"""The ``network`` capability: a module fetches from the sites it names, and
nowhere else -- not by a redirect either.

The manifest names the sites (``network_hosts``); ``_Network``
(``services/modules.py``) holds every URL to them; the fetcher
(``adapters/repo/http.py``) asks the same rule of each redirect before it is
followed.  The redirect test runs a real HTTP server on 127.0.0.1.
"""
from __future__ import annotations

import json
import threading
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from trcc.adapters.modules import ManifestError, read_manifest
from trcc.adapters.repo.http import UrllibHttpFetcher
from trcc.app import App
from trcc.core.errors import HttpFetchError, ModuleUnavailable
from trcc.core.models import MODULE_FETCH_MAX_BYTES, MODULE_FETCH_TIMEOUT_S, ModuleManifest
from trcc.core.ports import ModuleServices
from trcc.services.modules import ModuleScope

HOST = "api.example.com"


def _manifest(tmp_path: Path, **fields: Any) -> Path:
    folder = tmp_path / "weather"
    folder.mkdir()
    path = folder / "module.json"
    path.write_text(json.dumps({"key": "weather", "title": "Weather",
                                "summary": "The forecast.", **fields}))
    return path


# ── the manifest ──────────────────────────────────────────────────────────


def test_a_module_names_the_sites_it_fetches_from(tmp_path: Path) -> None:
    manifest = read_manifest(_manifest(tmp_path, capabilities=["network"],
                                       network_hosts=[HOST, "geo.example.org"]))
    assert (manifest.capabilities, manifest.network_hosts) == (
        ("network",), (HOST, "geo.example.org"))


@pytest.mark.parametrize(("fields", "message"), [
    ({"capabilities": ["network"]}, "the network capability and network_hosts go together"),
    ({"network_hosts": [HOST]}, "the network capability and network_hosts go together"),
    *[({"capabilities": ["network"], "network_hosts": [bad]},
       "network_hosts must be a list of public hostnames")
      for bad in ("localhost", "127.0.0.1", "192.168.1.1", "API.example.com",
                  "https://api.example.com", "api.example.com/x", "api.example.com.",
                  "*.example.com", "")],
])
def test_a_manifest_naming_no_site_or_a_private_one_is_refused(
        tmp_path: Path, fields: dict[str, Any], message: str) -> None:
    """Never the LAN, the router or TRCC's own API: they are not the
    internet a module was granted."""
    with pytest.raises(ManifestError) as e:
        read_manifest(_manifest(tmp_path, **fields))
    assert message in str(e.value)


# ── the module's port ─────────────────────────────────────────────────────


class _Services:
    """The App's side, recording each fetch it is asked for."""

    def __init__(self) -> None:
        self.asked: list[tuple[str, Callable[[str], bool]]] = []

    def fetch(self, url: str, allow: Callable[[str], bool]) -> bytes:
        self.asked.append((url, allow))
        return b"forecast"


def _scope(capabilities: tuple[str, ...] = ("network",),
           hosts: tuple[str, ...] = (HOST,)) -> tuple[ModuleScope, _Services]:
    services = _Services()
    manifest = ModuleManifest(key="weather", title="Weather", summary="s",
                              capabilities=capabilities, network_hosts=hosts)
    return ModuleScope(cast("ModuleServices", services), manifest), services


@pytest.mark.parametrize(("url", "allowed"), [
    (f"https://{HOST}/v1/forecast?lat=1", True),
    (f"https://{HOST}:443/v1", True),
    (f"http://{HOST}/v1", False),                       # not https
    ("https://geo.example.org/v1", False),              # a site it did not name
    (f"https://evil.{HOST}/v1", False),                 # no subdomains
    (f"https://{HOST}:8443/v1", False),                 # not the default port
    (f"https://user@{HOST}/v1", False),                 # no user in the URL
    (f"https://{HOST}@evil.example.net/v1", False),     # the host is evil's
    (f"https://{HOST}:notaport/v1", False),
    ("https://[::1/v1", False),                         # not a URL at all
])
def test_only_https_on_a_named_site_is_allowed(url: str, allowed: bool) -> None:
    scope, services = _scope()
    if allowed:
        assert scope.network.fetch(url) == b"forecast"
        assert [u for u, _ in services.asked] == [url]
        return
    with pytest.raises(ModuleUnavailable) as e:
        scope.network.fetch(url)
    assert str(e.value) == (f"Weather asked for {url} -- not https on a site its "
                            "module.json names")
    assert services.asked == []                         # nothing was sent


def test_every_redirect_is_asked_the_same_rule() -> None:
    """The check the App's fetcher asks of each hop is this module's own."""
    scope, services = _scope()
    scope.network.fetch(f"https://{HOST}/v1")
    (_, allow), = services.asked
    assert allow(f"https://{HOST}/elsewhere")
    assert not allow("https://evil.example.net/")
    assert not allow(f"http://{HOST}/v1")


def test_a_module_that_did_not_declare_network_is_refused() -> None:
    scope, services = _scope(capabilities=("events",), hosts=())
    with pytest.raises(ModuleUnavailable) as e:
        _ = scope.network                   # reaching it is what is refused
    assert str(e.value) == ("Weather asked for 'network' without declaring it in "
                            "its module.json")
    assert services.asked == []


def test_the_app_fetches_capped_and_checked() -> None:
    asked: list[dict[str, Any]] = []

    def fetch(url: str, **kwargs: Any) -> bytes:
        asked.append({"url": url, **kwargs})
        return b"ok"

    def allow(url: str) -> bool:
        return True

    app = cast("App", SimpleNamespace(http=SimpleNamespace(fetch=fetch)))
    assert App.fetch(app, f"https://{HOST}/v1", allow) == b"ok"
    assert asked == [{"url": f"https://{HOST}/v1", "timeout_s": MODULE_FETCH_TIMEOUT_S,
                      "max_bytes": MODULE_FETCH_MAX_BYTES, "allow": allow}]
    assert (MODULE_FETCH_MAX_BYTES, MODULE_FETCH_TIMEOUT_S) == (2 << 20, 20.0)


# ── the fetcher, on a real server ─────────────────────────────────────────


class _Site(BaseHTTPRequestHandler):
    """``/ok`` answers; ``/hop`` redirects to ``/ok``; ``/away`` redirects to
    ``/forbidden``."""
    seen: list[str] = []

    def do_GET(self) -> None:
        type(self).seen.append(self.path)
        target = {"/hop": "/ok", "/away": "/forbidden"}.get(self.path)
        if target is not None:
            self.send_response(302)
            self.send_header("Location", target)
            self.end_headers()
            return
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"body " + self.path.encode())

    def log_message(self, *args: Any) -> None:
        pass


@pytest.fixture
def site() -> Iterator[str]:
    _Site.seen = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Site)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def _not_forbidden(url: str) -> bool:
    return not url.endswith("/forbidden")


def test_an_allowed_redirect_is_followed(site: str) -> None:
    body = UrllibHttpFetcher().fetch(f"{site}/hop", timeout_s=5, allow=_not_forbidden)
    assert (body, _Site.seen) == (b"body /ok", ["/hop", "/ok"])


def test_a_redirect_off_the_allowed_sites_is_never_requested(site: str) -> None:
    with pytest.raises(HttpFetchError) as e:
        UrllibHttpFetcher().fetch(f"{site}/away", timeout_s=5, allow=_not_forbidden)
    assert str(e.value) == (f"GET {site}/away redirected to {site}/forbidden, "
                            "which is not allowed")
    assert _Site.seen == ["/away"]


def test_a_url_it_refuses_is_never_sent(site: str) -> None:
    with pytest.raises(HttpFetchError) as e:
        UrllibHttpFetcher().fetch(f"{site}/forbidden", timeout_s=5, allow=_not_forbidden)
    assert str(e.value) == f"GET {site}/forbidden is not allowed"
    assert _Site.seen == []


def test_without_a_check_redirects_are_followed_as_before(site: str) -> None:
    assert UrllibHttpFetcher().fetch(f"{site}/away", timeout_s=5) == b"body /forbidden"
