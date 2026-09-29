"""The LAN API's TLS files: made once, private, pinnable, renewed on expiry."""
from __future__ import annotations

import datetime
import hashlib
import ipaddress
import stat

from cryptography import x509
from cryptography.hazmat.primitives import serialization

from trcc.adapters.infra import tls

_HOSTS = ["localhost", "127.0.0.1", "::1", "box.lan", "192.168.1.20"]
_NOW = datetime.datetime.now(datetime.timezone.utc)


def _sha256(cert_path) -> str:
    der = x509.load_pem_x509_certificate(cert_path.read_bytes()).public_bytes(
        serialization.Encoding.DER)
    digest = hashlib.sha256(der).hexdigest().upper()
    return ":".join(digest[i:i + 2] for i in range(0, len(digest), 2))


def test_a_new_pair_is_private_and_its_fingerprint_is_the_certificates(tmp_path) -> None:
    files = tls._self_signed(tmp_path / "tls", _HOSTS, _NOW)

    assert stat.S_IMODE(files.key.stat().st_mode) == 0o600
    assert stat.S_IMODE((tmp_path / "tls").stat().st_mode) == 0o700
    assert files.fingerprint == _sha256(files.cert)


def test_the_certificate_names_every_address_a_client_may_use(tmp_path) -> None:
    files = tls._self_signed(tmp_path / "tls", _HOSTS, _NOW)
    san = x509.load_pem_x509_certificate(files.cert.read_bytes()).extensions \
        .get_extension_for_class(x509.SubjectAlternativeName).value

    assert set(san.get_values_for_type(x509.DNSName)) == {"localhost", "box.lan"}
    assert set(san.get_values_for_type(x509.IPAddress)) == {
        ipaddress.ip_address(h) for h in ("127.0.0.1", "::1", "192.168.1.20")}


def test_a_second_start_reuses_the_pinned_certificate(tmp_path) -> None:
    """A new certificate every start would break every client that pinned it."""
    first = tls._self_signed(tmp_path / "tls", _HOSTS, _NOW)
    second = tls._self_signed(tmp_path / "tls", _HOSTS, _NOW)

    assert second.fingerprint == first.fingerprint


def test_an_expired_certificate_is_replaced_and_the_key_stays_private(tmp_path) -> None:
    first = tls._self_signed(tmp_path / "tls", _HOSTS, _NOW)
    later = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=4000)

    renewed = tls._self_signed(tmp_path / "tls", _HOSTS, later)

    assert renewed.fingerprint != first.fingerprint
    assert stat.S_IMODE(renewed.key.stat().st_mode) == 0o600


def test_a_supplied_certificate_reports_its_own_fingerprint(tmp_path) -> None:
    made = tls._self_signed(tmp_path / "tls", _HOSTS, _NOW)

    assert tls.CryptographyTls().supplied(made.cert, made.key).fingerprint == made.fingerprint


def test_the_addresses_cover_loopback_the_lan_and_never_a_wildcard(monkeypatch) -> None:
    monkeypatch.setattr(tls, "get_lan_ip", lambda: "192.168.1.20")
    monkeypatch.setattr(tls.socket, "gethostname", lambda: "box.lan")

    assert tls._hosts("0.0.0.0") == ["localhost", "127.0.0.1", "::1", "box.lan",
                                     "192.168.1.20"]
    assert tls._hosts("10.0.0.5")[-1] == "10.0.0.5"
