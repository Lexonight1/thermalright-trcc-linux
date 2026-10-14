"""AboutPanel — credits, version, project links.

Chrome panel — no Commands dispatched, no events subscribed.  Reads the
package's ``__version__`` and the current language for localization.
"""
from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QLabel, QTextBrowser, QVBoxLayout

from ..base import BasePanel

log = logging.getLogger(__name__)


class AboutPanel(BasePanel):
    """Project identity + version + links."""

    def _setup_ui(self) -> None:
        log.debug("_setup_ui")
        from .... import __version__

        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 40, 40, 40)
        layout.setSpacing(12)
        layout.addStretch(1)

        title = QLabel("TRCC Linux", self)
        title_font = QFont()
        title_font.setPointSize(28)
        title_font.setBold(True)
        title.setFont(title_font)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        self._version_label = QLabel(f"version {__version__}", self)
        self._version_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._version_label)

        self._tagline = QLabel(self._tagline_text(), self)
        self._tagline.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._tagline.setWordWrap(True)
        layout.addWidget(self._tagline)

        layout.addSpacing(20)

        links = QLabel(self)
        links.setAlignment(Qt.AlignmentFlag.AlignCenter)
        links.setTextFormat(Qt.TextFormat.RichText)
        links.setOpenExternalLinks(True)
        links.setText(
            'Source: <a href="https://github.com/Lexonight1/'
            'thermalright-trcc-linux">github.com/Lexonight1/'
            'thermalright-trcc-linux</a><br>'
            'Issues / feature requests: '
            '<a href="https://github.com/Lexonight1/thermalright-trcc-linux/'
            'issues">file a GitHub issue</a>',
        )
        layout.addWidget(links)

        # About: the open-source work TRCC is built on -- core.credits, the
        # list every UI shows.
        import html

        from ....core.credits import GROUPS, INSPIRATIONS, WE_LOVE, WHAT_TRCC_DOES
        parts = ["<h3>Inspired by</h3>"]
        parts += [f"<p><b>{html.escape(p.name)}</b> &mdash; {html.escape(p.did)}</p>"
                  for p in INSPIRATIONS]
        parts += [f"<p>{html.escape(WHAT_TRCC_DOES)}</p>", f"<h2>{html.escape(WE_LOVE)}</h2>"]
        for title, credits in GROUPS:
            parts.append(f"<h3>{html.escape(title)}</h3>")
            for c in credits:
                licence = html.escape(c.licence)
                if c.licence_url and licence:
                    licence = f"<a href='{html.escape(c.licence_url, quote=True)}'>{licence}</a>"
                who = ", ".join(part for part in (html.escape(c.author), licence) if part)
                used = f"<br>{html.escape(c.used_for)}" if c.used_for else ""
                parts.append(f"<p><b>{html.escape(c.name)}</b> {who}{used}</p>")
        self._credits = QTextBrowser(self)
        self._credits.setOpenExternalLinks(True)
        self._credits.setHtml("".join(parts))
        layout.addWidget(self._credits, 6)
        log.info("AboutPanel: %d group(s)", len(GROUPS))

        layout.addStretch(2)

    @staticmethod
    def _tagline_text() -> str:
        log.debug("_tagline_text")
        return (
            "Open-source control for Thermalright LCD coolers on Linux.\n"
            "Architecture: hexagonal (ports + adapters), one Command bus, "
            "four UIs (CLI / API / GUI / REPL)."
        )
