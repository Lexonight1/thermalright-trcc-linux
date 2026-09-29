"""``TlsIdentity`` for the LAN API — a self-signed certificate made once, or the user's own.

Opt-in (``trcc api --tls``): the token otherwise crosses the LAN in cleartext,
but turning TLS on by default would break every existing ``http://`` client.

A self-signed certificate cannot be verified by name, so clients PIN it: the
CLI prints its SHA-256 fingerprint next to the pairing code.  That is why the
certificate is made ONCE and reused until it expires — a new one every start
would break every client that pinned the last.

``cryptography`` is imported inside the functions, so only a ``--tls`` start
pays for it.
"""
from __future__ import annotations

import datetime
import hashlib
import ipaddress
import logging
import os
import socket
from pathlib import Path
from typing import TYPE_CHECKING

from ...core.models import TlsFiles
from ...core.ports import TlsIdentity
from .network import get_lan_ip

if TYPE_CHECKING:
    from cryptography.x509 import Certificate

log = logging.getLogger(__name__)

_CERT = "cert.pem"
_KEY = "key.pem"
#: Long on purpose: clients pin the fingerprint, and expiry forces a re-pin.
_VALID_DAYS = 3650


class CryptographyTls(TlsIdentity):
    """The ``TlsIdentity`` port on the ``cryptography`` package."""

    def supplied(self, cert: Path, key: Path) -> TlsFiles:
        """The user's own certificate and key (``--tls-cert`` / ``--tls-key``)."""
        from cryptography import x509
        loaded = x509.load_pem_x509_certificate(cert.read_bytes())
        files = TlsFiles(cert, key, _fingerprint(loaded))
        log.info("CryptographyTls.supplied: %s (sha256 %s)", cert, files.fingerprint)
        return files

    def self_signed(self, directory: Path, bind_host: str,
                    now: datetime.datetime | None = None) -> TlsFiles:
        """The pair in *directory*, made if absent or expired.  *now*: tests only."""
        log.info("CryptographyTls.self_signed: %s bind=%s", directory, bind_host)
        return _self_signed(directory, _hosts(bind_host),
                            now or datetime.datetime.now(datetime.timezone.utc))


def _hosts(bind_host: str) -> list[str]:
    """Every address a client may use: loopback, this host's name, its LAN IP,
    and the bind address itself unless it is a wildcard."""
    hosts = ["localhost", "127.0.0.1", "::1", socket.gethostname(), get_lan_ip(),
             *([] if bind_host in ("0.0.0.0", "::") else [bind_host])]
    log.debug("tls._hosts: %s", hosts)
    return hosts


def _self_signed(directory: Path, hosts: list[str],
                 now: datetime.datetime) -> TlsFiles:
    """The certificate in *directory*, made for *hosts* if absent or expired."""
    from cryptography import x509
    cert_path, key_path = directory / _CERT, directory / _KEY
    if cert_path.is_file() and key_path.is_file():
        existing = x509.load_pem_x509_certificate(cert_path.read_bytes())
        expires = _expires(existing)
        if expires > now:
            files = TlsFiles(cert_path, key_path, _fingerprint(existing))
            log.info("tls.self_signed: reusing %s (sha256 %s, expires %s)",
                     cert_path, files.fingerprint, expires.date())
            return files
        log.warning("tls.self_signed: %s expired %s — making a new one; "
                    "clients that pinned the old fingerprint must re-pin",
                    cert_path, expires.date())
    return _generate(directory, hosts, now)


def _generate(directory: Path, hosts: list[str],
              now: datetime.datetime) -> TlsFiles:
    """Write a fresh EC P-256 key (0600) and a certificate naming *hosts*."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    directory.mkdir(parents=True, exist_ok=True)
    directory.chmod(0o700)
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "TRCC Linux API")])
    names: list[x509.GeneralName] = []
    for host in dict.fromkeys(hosts):
        try:
            names.append(x509.IPAddress(ipaddress.ip_address(host)))
        except ValueError:
            names.append(x509.DNSName(host))
    cert = (x509.CertificateBuilder()
            .subject_name(name).issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=5))
            .not_valid_after(now + datetime.timedelta(days=_VALID_DAYS))
            .add_extension(x509.SubjectAlternativeName(names), critical=False)
            .sign(key, hashes.SHA256()))
    key_path, cert_path = directory / _KEY, directory / _CERT
    # Created 0600, never chmod-ed after: a key readable for even a moment is
    # a key another account could have copied.  ``os.open`` applies the mode
    # only when it CREATES the file, so an old key is removed first.
    key_path.unlink(missing_ok=True)
    fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(key.private_bytes(serialization.Encoding.PEM,
                                  serialization.PrivateFormat.PKCS8,
                                  serialization.NoEncryption()))
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    files = TlsFiles(cert_path, key_path, _fingerprint(cert))
    log.info("tls._generate: new certificate %s for %s (sha256 %s)",
             cert_path, list(dict.fromkeys(hosts)), files.fingerprint)
    return files


def _expires(cert: Certificate) -> datetime.datetime:
    """When *cert* expires, timezone-aware, on any ``cryptography`` a distro ships.

    ``not_valid_after_utc`` arrived in 42 and deprecates the naive
    ``not_valid_after``; Ubuntu 24.04 ships 41, Debian 12 ships 38.
    """
    expires = (getattr(cert, "not_valid_after_utc", None)
               or cert.not_valid_after.replace(tzinfo=datetime.timezone.utc))
    log.debug("tls._expires: %s", expires)
    return expires


def _fingerprint(cert: Certificate) -> str:
    """SHA-256 of the certificate's DER bytes, as ``AB:CD:…`` — what clients pin."""
    from cryptography.hazmat.primitives import serialization
    der = cert.public_bytes(serialization.Encoding.DER)
    digest = hashlib.sha256(der).hexdigest().upper()
    fingerprint = ":".join(digest[i:i + 2] for i in range(0, len(digest), 2))
    log.debug("tls._fingerprint: %s", fingerprint)
    return fingerprint
