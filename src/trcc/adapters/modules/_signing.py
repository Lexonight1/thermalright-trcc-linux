"""Ed25519 signatures, for the module index -- the one file that imports
``cryptography`` for it (a base dependency).  Same scheme as the release's
``SHA256SUMS.txt.sig`` (``dev/tools/sign_release.py``)."""
from __future__ import annotations

import logging

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from ...core.ports import SignatureChecker

log = logging.getLogger(__name__)

#: An Ed25519 public key and signature, in bytes.
_KEY_BYTES, _SIG_BYTES = 32, 64


class Ed25519Checker(SignatureChecker):
    """Raw Ed25519: a 32-byte public key, a 64-byte signature."""

    def verify(self, key: bytes, message: bytes, signature: bytes) -> bool:
        if len(key) != _KEY_BYTES or len(signature) != _SIG_BYTES:
            log.warning("Ed25519Checker.verify: key %d bytes, signature %d bytes "
                        "-- not Ed25519", len(key), len(signature))
            return False
        try:
            Ed25519PublicKey.from_public_bytes(key).verify(signature, message)
        except (InvalidSignature, ValueError):
            log.debug("Ed25519Checker.verify: %d-byte message -- no match",
                      len(message))
            return False
        log.debug("Ed25519Checker.verify: %d-byte message -- match", len(message))
        return True
