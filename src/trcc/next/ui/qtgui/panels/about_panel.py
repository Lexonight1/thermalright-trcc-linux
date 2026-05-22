"""AboutPanel — credits, version, project links.

Chrome panel — no Commands dispatched, no events subscribed.  Reads the
package's ``__version__`` and the current language for localization.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QLabel, QVBoxLayout

from ..base import BasePanel


class AboutPanel(BasePanel):
    """Project identity + version + links."""

    def _setup_ui(self) -> None:
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

        layout.addStretch(2)

    @staticmethod
    def _tagline_text() -> str:
        return (
            "Open-source control for Thermalright LCD coolers on Linux.\n"
            "Architecture: hexagonal (ports + adapters), one Command bus, "
            "four UIs (CLI / API / GUI / REPL)."
        )

    def apply_language(self, lang: str) -> None:
        """Localized text re-render — minimal for now (English only),
        but the hook is in place for when ``tr()`` keys land."""
        del lang
        from .... import __version__
        self._version_label.setText(f"version {__version__}")
