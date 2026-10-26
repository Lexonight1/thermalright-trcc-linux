"""What the Modules page and "Get modules" show, decided without a toolkit:
each module's byline and why it does not run, a row per module the signed
index lists, the permission screen before an install, and the question
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

from ...core.models import MODULE_CAPABILITIES, AvailableModule, ModuleInfo

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
#: Where a module came from (``ModuleInfo.origin``), in its tile's words.
_ORIGINS: dict[str, str] = {
    "built-in": "Built-in",
    "installed": "Installed",
    "by hand": "Copied in by hand",
}


def byline(module: ModuleInfo, tr: Callable[[str], str]) -> str:
    """The line under a module's name: its version, who wrote it, and where
    it came from -- the same on every UI's Modules page."""
    parts = [tr("Version {version}").format(version=module.version)] if module.version else []
    if module.author:
        parts.append(tr("by {author}").format(author=module.author))
    parts.append(tr(_ORIGINS.get(module.origin, module.origin)))
    line = " · ".join(parts)
    log.debug("byline: %s -> %s", module.key, line)
    return line


def problem_line(module: ModuleInfo, tr: Callable[[str], str]) -> str:
    """Why *module* does not run -- here, or at all -- or "" when it does."""
    if not module.problem:
        return ""
    said = "Cannot run here: {problem}" if module.allowed else "Not run: {problem}"
    log.debug("problem_line: %s allowed=%s", module.key, module.allowed)
    return tr(said).format(problem=module.problem)


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
