"""One credit, as a line of rich text -- name, who made it, its licence (a
link to its text), what it gave TRCC -- shared by every window's About, so
the gui and hudgui write a credit the same way.  Toolkit-free: the window
puts the string in its own label."""
from __future__ import annotations

import html
import logging

from ...core.credits import Credit

log = logging.getLogger(__name__)


def credit_html(credit: Credit, accent: str) -> str:
    """*credit* as rich text, the licence and maker in *accent*."""
    log.debug("credit_html: %s (%s)", credit.name, credit.licence_url or "no link")
    licence = html.escape(credit.licence)
    if credit.licence_url and licence:
        licence = (f"<a href='{html.escape(credit.licence_url, quote=True)}'"
                   f" style='color:{accent}'>{licence}</a>")
    who = ", ".join(part for part in (html.escape(credit.author), licence) if part)
    used = (f"<br><span style='color:#b3b3b3'>{html.escape(credit.used_for)}</span>"
            if credit.used_for else "")
    return (f"<b style='color:white; font-size:11pt'>{html.escape(credit.name)}</b>"
            f"<span style='color:{accent}'>&nbsp; {who}</span>{used}")
