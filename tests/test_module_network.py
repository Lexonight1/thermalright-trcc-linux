"""The ``network`` capability: a module fetches from the sites it names, and
nowhere else -- not by a redirect either.

The manifest names the sites (``network_hosts``); ``_Network``
(``services/modules.py``) holds every URL to them; the fetcher
(``adapters/repo/http.py``) asks the same rule of each redirect before it is
followed, and refuses an address off the public internet whatever name led
there.  The redirect tests run a real HTTP server on 127.0.0.1; the address
tests a real TLS one, reached by a public name pointed at 127.0.0.1.
"""
from __future__ import annotations

import datetime
import json
import socket
import ssl
import threading
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from trcc.adapters.modules import ManifestError, read_manifest
from trcc.adapters.repo.http import UrllibHttpFetcher, _is_public, _PublicOnly
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
                      "max_bytes": MODULE_FETCH_MAX_BYTES, "allow": allow,
                      "public_only": True}]
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


# ── only the public internet, judged by the address the name resolved to ──


@pytest.mark.parametrize(("address", "public"), [
    ("93.184.215.14", True), ("2606:2800:21f:cb07:6820:80da:af6b:8b2c", True),
    ("127.0.0.1", False), ("::1", False), ("10.0.0.5", False),
    ("172.16.0.1", False), ("192.168.1.1", False), ("169.254.169.254", False),
    ("100.64.0.1", False), ("fd00::1", False), ("fe80::1", False),
    ("0.0.0.0", False), ("::ffff:127.0.0.1", False), ("::ffff:192.168.1.1", False),
    ("::ffff:93.184.215.14", True),
])
def test_only_a_public_internet_address_is_public(address: str, public: bool) -> None:
    assert _is_public(address) is public


def test_a_public_only_fetch_refuses_this_computer(site: str) -> None:
    with pytest.raises(HttpFetchError) as e:
        UrllibHttpFetcher().fetch(f"{site}/ok", timeout_s=5, public_only=True)
    assert str(e.value) == ("127.0.0.1 resolved to 127.0.0.1, which is not a public "
                            "internet address")
    assert _Site.seen == []


def _self_signed(directory: Path, host: str) -> tuple[Path, Path]:
    """A certificate for *host*, signed by its own key: (cert, key)."""
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(1)
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(host)]),
                           critical=False)
            .add_extension(x509.BasicConstraints(ca=True, path_length=None),
                           critical=True)
            .sign(key, hashes.SHA256()))
    cert_path, key_path = directory / "cert.pem", directory / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                           serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    return cert_path, key_path


@pytest.fixture
def rebound_site(tmp_path: Path,
                 monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[str, ssl.SSLContext]]:
    """https on the public name ``HOST``, which resolves to 127.0.0.1 -- a
    rebound name -- with a context that trusts its self-signed certificate,
    so the certificate is never what stops the fetch."""
    _Site.seen = []
    cert, key = _self_signed(tmp_path, HOST)
    served = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    served.load_cert_chain(cert, key)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Site)
    server.socket = served.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    resolve = socket.getaddrinfo

    def rebound(host: Any, *args: Any, **kwargs: Any) -> Any:
        return resolve("127.0.0.1" if host == HOST else host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", rebound)
    try:
        yield (f"https://{HOST}:{server.server_address[1]}",
               ssl.create_default_context(cafile=str(cert)))
    finally:
        server.shutdown()
        server.server_close()


def test_a_public_name_resolving_to_this_computer_is_reachable_unchecked(
        rebound_site: tuple[str, ssl.SSLContext]) -> None:
    url, trusted = rebound_site
    assert UrllibHttpFetcher(ssl_context=trusted).fetch(f"{url}/ok", timeout_s=5) == (
        b"body /ok")


def test_a_public_name_resolving_to_this_computer_is_refused(
        rebound_site: tuple[str, ssl.SSLContext]) -> None:
    url, trusted = rebound_site
    with pytest.raises(HttpFetchError) as e:
        UrllibHttpFetcher(ssl_context=trusted).fetch(f"{url}/ok", timeout_s=5,
                                                     public_only=True)
    assert str(e.value) == (f"{HOST} resolved to 127.0.0.1, which is not a public "
                            "internet address")
    assert _Site.seen == []


def test_through_a_proxy_the_proxy_resolves_the_name(site: str) -> None:
    port = int(site.rsplit(":", 1)[1])
    direct, proxied = (_PublicOnly("127.0.0.1", port, timeout=5) for _ in range(2))
    proxied.set_tunnel(HOST, 443)
    try:
        with pytest.raises(HttpFetchError):
            direct.connect()
        proxied._create_connection(("127.0.0.1", port), 5).close()
    finally:
        direct.close()
