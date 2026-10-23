"""The keys TRCC trusts to say which modules may be installed.

The root of the module index (:mod:`trcc.services.module_trust`): a
``root.json`` counts only when one of these signed it, and it names the keys
that sign the index itself.  They are the project's release keys, main and
spare -- the spare is there to retire a lost main key in an app update.

Each is the raw 32-byte Ed25519 public key, base64.  They are spelled here,
not read from a file, so every build -- wheel, deb, rpm, Nix, the frozen
Windows and macOS apps -- carries them with nothing to package.  The
published copies users verify releases with are ``packaging/keys/*.pub``;
``tests/test_module_trust.py`` fails if these ever differ from those.
"""
from __future__ import annotations

#: Release key -> its raw public key (base64).  Either may sign a root.
ROOT_KEYS: dict[str, str] = {
    "trcc-release-main": "RXIMCPnS6Gm0y93UeLGsppzwWa/tA3KzCJre6KyQyls=",
    "trcc-release-spare": "hhJOzM9qPCSUsM1b/gQ8LzeUlheQLQrblEV4jMBUxLs=",
}
