#!/usr/bin/env python3
"""Build the module website from the signed index -- what GitHub Pages serves.

Usage::

    PYTHONPATH=src python3.12 dev/tools/build_module_site.py site/modules -o _site

``site/modules/`` holds ``root.json`` and ``index.json``, signed on the
maintainer's machine (``sign_module_index.py``) and committed.  This opens
them the way TRCC does -- ``ModuleTrust``, against the release keys TRCC
pins -- and writes ``<out>/modules/``: the two files byte for byte, and an
``index.html`` listing what they list.  A file TRCC would refuse is never
published: the build fails, and Pages keeps serving the last good one.

The trust is in the signatures, not this page.  TRCC reads only
``root.json`` and ``index.json``; ``index.html`` is for people, generated
from the verified index so it cannot say anything the index does not.  It
has no script, and every text in it is escaped.

``--trust PUB`` verifies against another root key instead -- a throwaway
pair, to build a site from a test index.
"""
from __future__ import annotations

import argparse
import html
import logging
import sys
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

from trcc.adapters.modules._signing import Ed25519Checker
from trcc.core.errors import UntrustedModule
from trcc.core.models import MODULE_INDEX_URL, ModuleIndex, ModuleRelease
from trcc.core.trust_keys import ROOT_KEYS
from trcc.services.module_trust import ModuleTrust

from sign_module_index import public_key  # dev/tools, beside this file

log = logging.getLogger(__name__)

FILES = ("root.json", "index.json")
REPO = "https://github.com/Lexonight1/thermalright-trcc-linux"
GUIDE = f"{REPO}/blob/main/doc/GUIDE_MODULES.md"

PAGE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
body {{ font-family: system-ui, sans-serif; max-width: 52rem; margin: 2rem auto;
       padding: 0 1rem; line-height: 1.5; color: #1d1d1f; background: #fbfbfd; }}
h1 {{ font-size: 1.6rem; margin-bottom: .2rem; }}
.lead {{ color: #555; margin-top: 0; }}
.module {{ border: 1px solid #ddd; border-radius: 10px; padding: .8rem 1rem;
          margin: .8rem 0; background: #fff; }}
.module h2 {{ font-size: 1.15rem; margin: 0; }}
.meta {{ color: #666; font-size: .9rem; }}
.revoked {{ border-color: #c33; }}
code {{ font-size: .9em; }}
</style>
</head>
<body>
{body}
</body>
</html>
"""


def _e(text: object) -> str:
    return html.escape(str(text), quote=True)


def _release(release: ModuleRelease, revoked: str | None) -> str:
    """One listed module, as its tile on the page -- marked when the index
    takes it back (*revoked*: why)."""
    log.debug("_release: %s %s revoked=%s", release.key, release.version, revoked)
    asks = release.asks.describe() or "nothing beyond what every module has"
    taken = ("" if revoked is None else
             f'<p><b>Taken back:</b> {_e(revoked)} -- TRCC switches it off.</p>\n')
    return (f'<div class="module{"" if revoked is None else " revoked"}" '
            f'id="{_e(release.key)}">\n'
            f"<h2>{_e(release.title or release.key)} "
            f'<span class="meta">version {_e(release.version)}</span></h2>\n'
            f"<p>{_e(release.summary)}</p>\n{taken}"
            f'<p class="meta">by {_e(release.author or "(unnamed)")} · '
            f"<code>{_e(release.key)}</code> · needs TRCC {_e(release.min_trcc)} or newer"
            f"<br>asks for: {_e(asks)}"
            f'<br>reviewed at commit <code>{_e(release.commit[:12])}</code> · '
            f'<a href="{_e(release.url)}">bundle</a> '
            f"({release.size:,} bytes, sha256 <code>{_e(release.sha256[:16])}…</code>)</p>\n"
            "</div>")


def render(index: ModuleIndex, trust: ModuleTrust, now: datetime) -> str:
    """``index.html`` for verified *index* -- what it takes back judged by
    *trust*, as the app judges it."""
    log.info("render: index %d, %d module(s), %d revoked", index.version,
             len(index.releases), len(index.revoked))
    parts = ["<h1>TRCC modules</h1>",
             '<p class="lead">Every module here was reviewed and signed by the '
             "TRCC maintainer. Install them from TRCC itself: open "
             "<b>Modules</b>, then <b>Get modules</b>. "
             f'<a href="{_e(GUIDE)}">The modules guide</a> says how, and how to '
             "get your own listed.</p>"]
    if index.releases:
        parts += [_release(r, trust.revoked(index, r.key, r.sha256))
                  for r in (index.releases[key] for key in sorted(index.releases))]
    else:
        parts.append("<p>No modules are listed yet.</p>")
    # Taken back and no longer listed: only here, so a user can see why it stopped.
    if unlisted := [r for r in index.revoked if r.key not in index.releases]:
        parts.append("<h2>Taken back</h2>")
        parts += [f'<div class="module revoked"><b>{_e(r.key)}</b>'
                  f'{" (one release)" if r.sha256 else ""}: {_e(r.reason)}</div>'
                  for r in unlisted]
    state = ("" if now < index.expires else
             " <b>This list has expired: TRCC installs and updates nothing from it "
             "until it is renewed.</b>")
    parts.append(f'<p class="meta">Index version {index.version}, valid until '
                 f'{index.expires:%Y-%m-%d}.{state} TRCC reads <a href="root.json">'
                 f'root.json</a> and <a href="index.json">index.json</a> from '
                 f"<code>{_e(MODULE_INDEX_URL)}</code>; this page is made from them.</p>")
    return PAGE.format(title="TRCC modules", body="\n".join(parts))


def landing() -> str:
    """The site's front page: where the modules are, and the project."""
    log.debug("landing")
    return PAGE.format(title="TRCC Linux", body=(
        "<h1>TRCC Linux</h1>"
        f'<p><a href="modules/">Modules</a> · <a href="{_e(REPO)}">the project</a></p>'))


def build(source: Path, out: Path, root_keys: Iterable[str], now: datetime) -> ModuleIndex:
    """Verify *source*'s two files and write the site to *out*.  Raises
    ``UntrustedModule`` (nothing written) when TRCC would refuse them."""
    log.info("build: %s -> %s", source, out)
    data = {name: (source / name).read_bytes() for name in FILES}
    trust = ModuleTrust(Ed25519Checker(), root_keys=root_keys)
    index = trust.open_index(data["index.json"], trust.open_root(data["root.json"]))
    modules = out / "modules"
    modules.mkdir(parents=True, exist_ok=True)
    for name, raw in data.items():
        (modules / name).write_bytes(raw)
    (modules / "index.html").write_text(render(index, trust, now), encoding="utf-8")
    (out / "index.html").write_text(landing(), encoding="utf-8")
    log.info("build: wrote %s", sorted(p.name for p in modules.iterdir()))
    return index


def _trusted(pub: Path | None) -> list[str]:
    """The root keys to verify against: TRCC's pinned ones, or *pub*'s."""
    log.debug("_trusted: %s", pub or "the pinned release keys")
    return list(ROOT_KEYS.values()) if pub is None else [public_key(pub)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("source", type=Path, help="the folder with root.json and index.json")
    parser.add_argument("-o", "--out", type=Path, required=True, help="where the site goes")
    parser.add_argument("--trust", type=Path, help="a root public key to verify against "
                        "instead of TRCC's pinned release keys (a test index)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    log.info("main: %s", args.source)
    try:
        index = build(args.source, args.out, _trusted(args.trust),
                      datetime.now(timezone.utc))
    except (UntrustedModule, OSError) as e:
        print(f"NOT BUILT -- {e}", file=sys.stderr)
        return 1
    print(f"built {args.out}/modules: index {index.version}, "
          f"{len(index.releases)} module(s), valid until {index.expires:%Y-%m-%d}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
