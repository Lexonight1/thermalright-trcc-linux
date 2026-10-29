"""The Modules page's list, for either window.

One tile per module: its symbol on the left, its name, what it does, a
"Guide ›" to its section of the guide, an On/Off switch, a Remove for one the
user installed, and why it cannot run -- or why a switch was refused, or what
switching off could not undo -- when there is something to say.  "Get
modules" turns the page into the signed index's list: Install or Update after
a permission screen that says what the module may do, in the words its
capabilities are known by (``ui/presentation/module_catalog``).  With
developer mode on (its switch sits beside "Get modules"), "Install from
folder…" installs a module folder the user picks, unsigned, after the same
screen saying nobody reviewed it -- the App reads the folder
(``ReadModuleFolder``), so the screen shows what will install.

The window builds the Commands, as the visual controls' windows do: *listing*
makes its ``ListModules``, *switch* its ``SetModuleEnabled``, *icon* its
``ModuleIcon``, *browse* / *install* / *remove* the index's, *developer* /
*read_folder* / *install_folder* developer mode's -- the symbol
arrives as bytes the App read, so a window over the socket draws it too.
Everything that may wait -- a switch can ask for the password, the index and
a bundle are downloads -- is sent off the GUI thread.  The list follows
``ModuleChanged``, ``ModuleInstalled`` and ``ModuleRemoved``, so what another
UI does shows here too.  Each window frames it in its own page and style.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import replace
from functools import partial
from pathlib import Path
from typing import Any

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QImageReader, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..core.commands import Command
from ..core.events import DeveloperModeChanged, ModuleChanged, ModuleInstalled, ModuleRemoved
from ..core.i18n import tr
from ..core.models import AvailableModule, ModuleAsks, ModuleInfo
from ..core.ports import CommandBus
from ..core.results import ModuleCatalogResult, ModuleResult
from ._module_faces import face_loaded
from .presentation.module_catalog import (
    DEVELOPER_MODE,
    FROM_FOLDER,
    REOPEN,
    byline,
    catalog_rows,
    consent,
    problem_line,
    removal_question,
)
from .qt_background import dispatch_in_background

log = logging.getLogger(__name__)

#: What the page says before any feature is a module.
NONE_YET = "No modules yet -- features arrive here as they become modules."
#: The folder dialog's title.
CHOOSE_FOLDER = "Choose a module folder"
READING = "Reading the module list…"
#: The property a switch, a Remove or an Install carries: the module it acts on.
_KEY_PROPERTY = "trcc_module_key"
#: The property a "Guide ›" carries: where it opens.
_GUIDE_PROPERTY = "trcc_module_guide"
#: The symbol's size on a tile, in pixels.
ICON_SIZE = 40


class ModulesView(QScrollArea):
    """Every module as a tile with its switch, on *app* -- whichever window,
    in-process or over the socket -- and the index's modules to get."""

    def __init__(self, app: CommandBus,
                 listing: Callable[[], Command[Any]],
                 switch: Callable[[str, bool], Command[Any]],
                 icon: Callable[[str], Command[Any]],
                 parent: QWidget | None = None, *,
                 browse: Callable[[], Command[Any]],
                 install: Callable[[str, ModuleAsks], Command[Any]],
                 remove: Callable[[str], Command[Any]],
                 developer: Callable[..., Command[Any]],
                 read_folder: Callable[..., Command[Any]],
                 install_folder: Callable[..., Command[Any]]) -> None:
        super().__init__(parent)
        log.info("ModulesView: built")
        self._app = app
        self._listing = listing
        self._switch = switch
        self._icon = icon
        self._browse, self._install, self._remove = browse, install, remove
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self._body = QWidget()
        self._column = QVBoxLayout(self._body)
        self._column.setSpacing(10)
        self.setWidget(self._body)
        #: The switches by module key -- what a test or a refresh reads.
        self.switches: dict[str, QPushButton] = {}
        #: Each installed module's Remove, by key.
        self.removes: dict[str, QPushButton] = {}
        #: Each listed module's Install / Update, by key, while browsing.
        self.actions: dict[str, QPushButton] = {}
        #: What the last switch of each module left to say -- why it was
        #: refused, or what switching off could not undo -- shown on its tile
        #: until a switch with nothing to say, never only in a log.
        self.notes: dict[str, str] = {}
        #: The index's list: shown instead of the tiles while browsing, with
        #: what the last read or install had to say.
        self.browsing = False
        self.catalog: tuple[AvailableModule, ...] = ()
        self.catalog_note = ""
        #: The modules whose UI this window had loaded at the last refresh --
        #: an update to one of them is finished only by reopening the window.
        self._with_faces: set[str] = set()
        #: Each tile's title by key, for the question a Remove asks.
        self._titles: dict[str, str] = {}
        #: The module an install or removal is under way for -- its answer
        #: may carry only a message.  One at a time.
        self._pending = ""
        #: Developer mode's switch and its install from a folder.
        self.developer = DeveloperMode(self, developer, read_folder, install_folder)
        #: The window's language -- "" until it says, English as written.
        self._lang = ""
        #: TRCC's own words in it.
        self._translate: Callable[[str], str] = str
        self.refresh()

    def set_language(self, lang: str) -> None:
        """The window's language changed: the tiles again, in it."""
        log.info("ModulesView.set_language: %s", lang)
        self._lang = lang
        self._translate = partial(tr, lang=lang)
        self.refresh()

    def _words(self, key: str) -> Callable[[str], str]:
        """Module *key*'s title and summary: its own words, else TRCC's --
        never around them, so no module rewords what TRCC says of it."""
        log.debug("ModulesView._words: %s in %r", key, self._lang)
        return partial(tr, lang=self._lang, module=key) if self._lang else str

    def refresh(self) -> None:
        """Rebuild the tiles from what the App says now -- or the index's
        list, while browsing."""
        if self.browsing:
            self._show_catalog()
            return
        listed = self._app.dispatch(self._listing())
        modules = listed.modules
        self.developer.on = bool(getattr(listed, "developer_mode", False))
        log.info("ModulesView.refresh: %s developer_mode=%s",
                 {m.key: m.enabled for m in modules}, self.developer.on)
        self._clear()
        self._with_faces = {m.key for m in modules if face_loaded(m.key)}
        self._column.addWidget(self._bar("Get modules", "modulesGet", self.open_catalog,
                                         *self.developer.controls()))
        if self.developer.note:
            note = QLabel(self.developer.note)
            note.setObjectName("modulesFolderNote")
            note.setWordWrap(True)
            self._column.addWidget(note)
        if not modules:
            empty = QLabel(self._translate(NONE_YET))
            empty.setObjectName("modulesEmpty")
            self._column.addWidget(empty)
        for module in modules:
            self._column.addWidget(self._tile(module))
        self._column.addStretch(1)

    def _clear(self) -> None:
        """Every tile or row gone -- at once: deleteLater waits for the event
        loop, and the old tile showed under the new one until it ran."""
        log.debug("ModulesView._clear: %d item(s)", self._column.count())
        while self._column.count():
            item = self._column.takeAt(0)
            if item is not None and (widget := item.widget()) is not None:
                widget.hide()
                widget.deleteLater()
        self.switches.clear()
        self.removes.clear()
        self.actions.clear()
        self._titles.clear()

    def _bar(self, text: str, name: str, action: Callable[[], None],
             *extra: QWidget) -> QWidget:
        """The row above the list: one button, then any *extra*."""
        log.debug("ModulesView._bar: %s + %d", text, len(extra))
        bar = QWidget()
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)
        button = QPushButton(self._translate(text))
        button.setObjectName(name)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(action)
        row.addWidget(button, 0, Qt.AlignmentFlag.AlignLeft)
        for widget in extra:
            row.addWidget(widget, 0, Qt.AlignmentFlag.AlignLeft)
        row.addStretch(1)
        return bar

    def on_event(self, event: object) -> None:
        """Follow a module switched, installed or removed in any UI."""
        if isinstance(event, ModuleChanged):
            log.info("ModulesView.on_event: %s -> %s note=%r", event.key,
                     event.enabled, event.note)
            self._note(event.key, event.note)
            self.refresh()
        elif isinstance(event, ModuleInstalled):
            log.info("ModulesView.on_event: %s %s installed (loaded here: %s)",
                     event.key, event.version, event.key in self._with_faces)
            if event.key in self._with_faces:
                self.notes[event.key] = REOPEN
            self.refresh()
        elif isinstance(event, ModuleRemoved):
            log.info("ModulesView.on_event: %s removed", event.key)
            self.notes.pop(event.key, None)
            self.refresh()
        elif isinstance(event, DeveloperModeChanged):
            log.info("ModulesView.on_event: developer mode %s", event.on)
            self.refresh()

    def _symbol(self, key: str) -> QPixmap:
        """Module *key*'s symbol at the tile's size, from the bytes the App
        sends -- an SVG drawn at that size, not scaled up.  Null if none."""
        result = self._app.dispatch(self._icon(key))
        if not result.ok:
            log.warning("ModulesView._symbol: %s -- %s", key, result.message)
            return QPixmap()
        buffer = QBuffer()
        buffer.setData(QByteArray(result.image))
        buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        reader = QImageReader(buffer)
        reader.setScaledSize(reader.size().scaled(
            ICON_SIZE, ICON_SIZE, Qt.AspectRatioMode.KeepAspectRatio))
        image = reader.read()
        log.debug("ModulesView._symbol: %s %dx%d %s", key, image.width(),
                  image.height(), reader.errorString() if image.isNull() else "ok")
        return QPixmap.fromImage(image)

    def _tile(self, module: ModuleInfo) -> QFrame:
        """One module: name, what it does, why not here, its switch -- and,
        installed by the user, its Remove."""
        log.debug("ModulesView._tile: %s", module.key)
        words = self._words(module.key)
        tile = QFrame()
        tile.setObjectName("moduleTile")
        row = QHBoxLayout(tile)
        if module.icon and not (pixmap := self._symbol(module.key)).isNull():
            symbol = QLabel()
            symbol.setObjectName("moduleIcon")
            symbol.setPixmap(pixmap)
            row.addWidget(symbol, 0, Qt.AlignmentFlag.AlignTop)
        text = QVBoxLayout()
        name = QLabel(words(module.title))
        name.setObjectName("moduleTitle")
        text.addWidget(name)
        about = QLabel(byline(module, self._translate))
        about.setObjectName("moduleByline")
        text.addWidget(about)
        summary = QLabel(words(module.summary))
        summary.setWordWrap(True)
        text.addWidget(summary)
        note = (problem_line(module, self._translate)
                or self._translate(self.notes.get(module.key, "")))
        if note:
            problem = QLabel(note)
            problem.setObjectName("moduleProblem")
            problem.setWordWrap(True)
            text.addWidget(problem)
        if module.guide:
            guide = QPushButton(self._translate("Guide ›"))
            guide.setObjectName("moduleGuide")
            guide.setFlat(True)
            guide.setCursor(Qt.CursorShape.PointingHandCursor)
            guide.setProperty(_GUIDE_PROPERTY, module.guide)
            guide.clicked.connect(self._on_guide)
            text.addWidget(guide, 0, Qt.AlignmentFlag.AlignLeft)
        row.addLayout(text, 1)
        self._titles[module.key] = module.title
        if module.origin == "installed":     # TRCC's own are never removed
            remove = QPushButton(self._translate("Remove"))
            remove.setObjectName("moduleRemove")
            remove.setCursor(Qt.CursorShape.PointingHandCursor)
            remove.setProperty(_KEY_PROPERTY, module.key)
            remove.clicked.connect(self._on_remove)
            row.addWidget(remove, 0, Qt.AlignmentFlag.AlignVCenter)
            self.removes[module.key] = remove
        switch = QPushButton(self._translate("On" if module.enabled else "Off"))
        switch.setCheckable(True)
        switch.setChecked(module.enabled)
        switch.setCursor(Qt.CursorShape.PointingHandCursor)
        switch.setProperty(_KEY_PROPERTY, module.key)
        switch.clicked.connect(self._on_switch)
        row.addWidget(switch, 0, Qt.AlignmentFlag.AlignVCenter)
        self.switches[module.key] = switch
        return tile

    def _on_switch(self, checked: bool) -> None:
        """Ask the App to switch the module -- off the GUI thread, since
        switching off may ask for the password; the list then shows its
        answer."""
        sender = self.sender()
        key = str(sender.property(_KEY_PROPERTY)) if sender is not None else ""
        log.info("ModulesView._on_switch: %s -> %s", key, checked)
        if isinstance(sender, QPushButton):
            sender.setEnabled(False)       # one switch at a time
        dispatch_in_background(self._app, self._switch(key, checked),
                               self._switched,
                               failed=partial(_could_not, key))

    def _switched(self, result: ModuleResult) -> None:
        """The App's answer to a switch: its refusal or its note, on the tile."""
        if result.ok:
            log.info("ModulesView._switched: %s -> %s note=%r", result.key,
                     result.enabled, result.note)
            self._note(result.key, result.note)
        else:
            log.warning("ModulesView._switched: %s refused: %s", result.key,
                        result.message)
            self.notes[result.key] = result.message
        self.refresh()

    def _note(self, key: str, note: str) -> None:
        """*key*'s tile says *note* from now on -- or nothing, for ""."""
        log.debug("ModulesView._note: %s %r", key, note)
        if note:
            self.notes[key] = note
        else:
            self.notes.pop(key, None)

    def _on_guide(self) -> None:
        """Open the module's section of the guide, as the "?" button opens
        the troubleshooting guide."""
        sender = self.sender()
        url = str(sender.property(_GUIDE_PROPERTY)) if sender is not None else ""
        log.info("ModulesView._on_guide: %s", url)
        QDesktopServices.openUrl(QUrl(url))

    # ── removing ──────────────────────────────────────────────────────────

    def _on_remove(self) -> None:
        """Remove the module, once the user said so -- off the GUI thread."""
        sender = self.sender()
        key = str(sender.property(_KEY_PROPERTY)) if sender is not None else ""
        log.info("ModulesView._on_remove: %s", key)
        if self._pending or not self.ask(
                removal_question(self._words(key)(self._titles.get(key, key)),
                                 self._translate), "",
                self._translate("Remove")):
            log.info("ModulesView._on_remove: %s kept (busy with %r)", key, self._pending)
            return
        if isinstance(sender, QPushButton):
            sender.setEnabled(False)
        self._pending = key
        dispatch_in_background(self._app, self._remove(key), self._removed,
                               failed=partial(_could_not, key))

    def _removed(self, result: ModuleResult) -> None:
        """The App's answer to a removal: gone, or why not, on the tile."""
        key, self._pending = self._pending, ""
        log.info("ModulesView._removed: %s ok=%s %s", key, result.ok, result.message)
        if not result.ok:
            self.notes[key] = result.message
        self.refresh()

    # ── getting modules ───────────────────────────────────────────────────

    def open_catalog(self) -> None:
        """Show the index's list -- read off the GUI thread, a download."""
        log.info("ModulesView.open_catalog")
        self.browsing, self.catalog, self.catalog_note = True, (), READING
        self._show_catalog()
        dispatch_in_background(self._app, self._browse(), self._listed,
                               failed=_could_not_read)

    def close_catalog(self) -> None:
        """Back to the tiles."""
        log.info("ModulesView.close_catalog")
        self.browsing = False
        self.refresh()

    def _listed(self, result: ModuleCatalogResult) -> None:
        """The index's answer: its modules, or why it could not be read."""
        log.info("ModulesView._listed: ok=%s %d module(s)", result.ok,
                 len(result.modules) if result.ok else 0)
        self.catalog = result.modules if result.ok else ()
        self.catalog_note = "" if result.ok else result.message
        if self.browsing:
            self._show_catalog()

    def _show_catalog(self) -> None:
        """The list: a row per module the index offers, and its button."""
        log.debug("ModulesView._show_catalog: %d row(s)", len(self.catalog))
        self._clear()
        self._column.addWidget(self._bar("‹ Back", "modulesBack", self.close_catalog))
        if self.catalog_note:
            note = QLabel(self._translate(self.catalog_note))
            note.setObjectName("moduleProblem")
            note.setWordWrap(True)
            self._column.addWidget(note)
        for row in catalog_rows(self.catalog, self._translate):
            frame = QFrame()
            frame.setObjectName("moduleTile")
            line = QHBoxLayout(frame)
            text = QVBoxLayout()
            words = self._words(row.key)
            name = QLabel(f"{words(row.title)}  {row.version}")
            name.setObjectName("moduleTitle")
            text.addWidget(name)
            summary = QLabel(words(row.summary))
            summary.setWordWrap(True)
            text.addWidget(summary)
            if row.detail:
                detail = QLabel(row.detail)
                detail.setObjectName("moduleProblem")
                detail.setWordWrap(True)
                text.addWidget(detail)
            line.addLayout(text, 1)
            button = QPushButton(self._translate(row.action))
            button.setEnabled(row.enabled)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setProperty(_KEY_PROPERTY, row.key)
            button.clicked.connect(self._on_install)
            line.addWidget(button, 0, Qt.AlignmentFlag.AlignVCenter)
            self.actions[row.key] = button
            self._column.addWidget(frame)
        self._column.addStretch(1)

    def _on_install(self) -> None:
        """Install or update the module -- after the permission screen; what
        it asks for is what is sent as accepted."""
        sender = self.sender()
        key = str(sender.property(_KEY_PROPERTY)) if sender is not None else ""
        module = next((m for m in self.catalog if m.key == key), None)
        log.info("ModulesView._on_install: %s %s (busy with %r)", key,
                 module.version if module else "not listed", self._pending)
        if module is None or self._pending:
            return
        question, lines = consent(module, self._translate,
                                  title=self._words(key)(module.title))
        confirm = "Update" if module.status == "update" else "Install"
        if not self.ask(question, "\n".join(lines), self._translate(confirm)):
            log.info("ModulesView._on_install: %s declined", key)
            return
        if isinstance(sender, QPushButton):
            sender.setEnabled(False)
        self._pending = key
        dispatch_in_background(
            self._app, self._install(key, module.asks),
            self._installed, failed=partial(_could_not, key))

    def _installed(self, result: ModuleResult) -> None:
        """The App's answer to an install: the row now says Installed -- no
        second download -- or the list says why not."""
        key, self._pending = self._pending, ""
        log.info("ModulesView._installed: %s ok=%s %s", key, result.ok, result.message)
        self.catalog_note = "\n".join(filter(None, (result.message, result.note)))
        if result.ok:
            self.catalog = tuple(
                replace(m, status="installed", installed_version=m.version)
                if m.key == key else m for m in self.catalog)
        if self.browsing:
            self._show_catalog()

    def ask(self, question: str, details: str, confirm: str) -> bool:
        """The user's yes to *question* -- *details* under it, *confirm* the
        button that says yes.  A method, so a test answers for the user."""
        log.info("ModulesView.ask: %s", question)
        # The window's child, not this view's: the page styles the view's
        # labels light-on-dark, and a dialog under it drew light-on-light.
        box = QMessageBox(self.window())
        box.setIcon(QMessageBox.Icon.Question)
        box.setText(question)
        if details:
            box.setInformativeText(details)
        yes = box.addButton(confirm, QMessageBox.ButtonRole.AcceptRole)
        box.addButton(self._translate("Cancel"), QMessageBox.ButtonRole.RejectRole)
        box.exec()
        answered = box.clickedButton() is yes
        log.info("ModulesView.ask: %s -> %s", question, answered)
        return answered


class DeveloperMode:
    """Developer mode on the Modules page: its switch, and -- while it is on
    -- "Install from folder…", the folder flow behind it.  The page owns one;
    it reads the page's bus and words (same module), and asks it only for
    its question box (``ask``) and a ``refresh``."""

    def __init__(self, view: ModulesView, developer: Callable[..., Command[Any]],
                 read_folder: Callable[..., Command[Any]],
                 install_folder: Callable[..., Command[Any]]) -> None:
        log.debug("DeveloperMode: built")
        self._view = view
        self._developer, self._read, self._install = developer, read_folder, install_folder
        #: Developer mode, as the App last said.
        self.on = False
        #: The switch and the button, as last built; None while not shown.
        self.switch: QCheckBox | None = None
        self.button: QPushButton | None = None
        #: What the last install from a folder had to say.
        self.note = ""
        #: The module an install from a folder is under way for.
        self.busy = ""

    def controls(self) -> list[QWidget]:
        """The switch -- and, while developer mode is on, the button."""
        log.debug("DeveloperMode.controls: on=%s busy=%r", self.on, self.busy)
        translate = self._view._translate
        switch = QCheckBox(translate(DEVELOPER_MODE))
        switch.setObjectName("modulesDeveloper")
        switch.setChecked(self.on)
        switch.toggled.connect(self._on_switch)
        self.switch, self.button = switch, None
        if not self.on:
            return [switch]
        button = QPushButton(translate(FROM_FOLDER))
        button.setObjectName("modulesFromFolder")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setEnabled(not self.busy)
        button.clicked.connect(self._on_from_folder)
        self.button = button
        return [switch, button]

    def _on_switch(self, on: bool) -> None:
        """Developer mode on or off, in the App -- the page follows the event
        every UI hears."""
        log.info("DeveloperMode._on_switch: %s", on)
        result = self._view._app.dispatch(self._developer(on=on))
        if not result.ok:
            log.warning("DeveloperMode._on_switch: %s", result.message)

    def choose_folder(self) -> Path | None:
        """The folder the user picks, or None -- a method, so a test picks."""
        log.info("DeveloperMode.choose_folder")
        chosen = QFileDialog.getExistingDirectory(
            self._view.window(), self._view._translate(CHOOSE_FOLDER))
        return Path(chosen) if chosen else None

    def _on_from_folder(self) -> None:
        """Install the module in a folder the user picks -- after the
        permission screen, which says nobody reviewed it."""
        folder = self.choose_folder()
        log.info("DeveloperMode._on_from_folder: %s (busy with %r)", folder, self.busy)
        if folder is None or self.busy:
            return
        read = self._view._app.dispatch(self._read(path=folder))
        module = getattr(read, "module", None)
        if not read.ok or module is None:
            self.note = read.message
            self._view.refresh()
            return
        question, lines = consent(module, self._view._translate,
                                  title=self._view._words(module.key)(module.title),
                                  unsigned=True)
        if not self._view.ask(question, "\n".join(lines), self._view._translate("Install")):
            log.info("DeveloperMode._on_from_folder: %s declined", module.key)
            return
        self.busy = module.key
        if self.button is not None:
            self.button.setEnabled(False)
        dispatch_in_background(
            self._view._app, self._install(path=folder, asks=module.asks),
            self._installed, failed=partial(_could_not, module.key))

    def _installed(self, result: ModuleResult) -> None:
        """The App's answer to an install from a folder, shown on the page."""
        key, self.busy = self.busy, ""
        log.info("DeveloperMode._installed: %s ok=%s %s", key, result.ok, result.message)
        self.note = "\n".join(filter(None, (result.message, result.note)))
        self._view.refresh()


def _could_not(key: str, message: str) -> ModuleResult:
    """The App went away while a switch, install or removal waited -- the
    page says so."""
    log.warning("_could_not: %s -- %s", key, message)
    return ModuleResult(ok=False, key=key, message=message)


def _could_not_read(message: str) -> ModuleCatalogResult:
    """The App went away while the index was read."""
    log.warning("_could_not_read: %s", message)
    return ModuleCatalogResult(ok=False, message=message)
