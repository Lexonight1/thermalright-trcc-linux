"""What "Get modules" shows, decided without a toolkit: a row per module the
signed index lists, the permission screen before an install, and the question
before a removal.

Every text is an English key, translated by the caller's *tr* -- the gui's
``tr`` for its language, ``str`` for the CLI -- and filled in after, so a
translation carries the ``{placeholders}`` too.  The permission screen says
what the module may do in the words its capabilities are known by
(``MODULE_CAPABILITIES``), and the sites it fetches from: exactly what the
install will send as accepted, so the App's consent check holds the user to
what they saw.
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from ...core.models import MODULE_CAPABILITIES, AvailableModule

log = logging.getLogger(__name__)

#: A status -> its row's button and whether it can be pressed.
_ACTIONS: dict[str, tuple[str, bool]] = {
    "available": ("Install", True),
    "update": ("Update", True),
    "installed": ("Installed", False),
    "revoked": ("Taken back", False),
}
#: What a tile says after an update, while this window holds the old code.
REOPEN = "Reopen this window to finish updating."
REVIEWED = "Reviewed by TRCC -- not guaranteed safe."
MAY = "It may:"


@dataclass(frozen=True, slots=True)
class CatalogRow:
    """One module of the list: what it is, its button, and a line under it
    -- the version it updates, or why it was taken back."""
    key: str
    title: str
    version: str
    summary: str
    action: str
    enabled: bool
    detail: str = ""


def catalog_rows(modules: Iterable[AvailableModule],
                 tr: Callable[[str], str]) -> tuple[CatalogRow, ...]:
    """The rows, in the index's order; ``action`` stays an English key for
    the caller to translate, ``detail`` is translated here."""
    rows = tuple(_row(m, tr) for m in modules)
    log.debug("catalog_rows: %s", {r.key: r.action for r in rows})
    return rows


def _row(module: AvailableModule, tr: Callable[[str], str]) -> CatalogRow:
    action, enabled = _ACTIONS.get(module.status, ("Install", False))
    detail = (module.reason if module.status == "revoked"
              else tr("installed: {version}").format(version=module.installed_version)
              if module.status == "update" else "")
    log.debug("_row: %s %s -> %s", module.key, module.status, action)
    return CatalogRow(key=module.key, title=module.title, version=module.version,
                      summary=module.summary, action=action, enabled=enabled,
                      detail=detail)


def consent(module: AvailableModule, tr: Callable[[str], str]) -> tuple[str, list[str]]:
    """The permission screen: its question, and each line under it -- every
    capability in its own words, then every site."""
    question = tr("Install {title} {version}?").format(title=tr(module.title),
                                                       version=module.version)
    lines = [tr(REVIEWED)]
    asks = [tr(MODULE_CAPABILITIES.get(c, c)) for c in module.capabilities]
    asks += [tr("fetches from {site}").format(site=h) for h in module.network_hosts]
    if asks:
        lines += [tr(MAY), *(f"  - {a}" for a in asks)]
    log.info("consent: %s %s asks %d thing(s)", module.key, module.version, len(asks))
    return question, lines


def removal_question(title: str, tr: Callable[[str], str]) -> str:
    """What a Remove asks first."""
    log.debug("removal_question: %s", title)
    return tr("Remove {title}? Themes made with it stay.").format(title=tr(title))
