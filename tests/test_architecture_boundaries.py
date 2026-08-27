"""Architecture boundary gate — the hexagon's dependency law, made executable.

CLAUDE.md states the layer law in prose: ``core`` depends on nothing in the
app; ``services`` depend only on ``core``; dependencies point inward only.
Prose does not fail the build, so the law eroded over time — lazy adapter
imports buried inside core Command bodies (hidden from a top-level grep), a
PySide6 import in a core Command, an ``sys.platform`` OS-sniff in core.  This
test makes the law executable: it parses every module under ``core/`` and
``services/`` with ``ast`` and fails on

  * any *runtime* import that resolves into ``trcc.adapters`` / ``trcc.ui`` or a
    GUI / OS / USB framework package, and
  * any ``sys.platform`` / ``platform.system()``-style OS-sniff,

because both reverse the inward dependency arrow / belong behind a port.

Imports inside ``if TYPE_CHECKING:`` are skipped — they never execute, so they
create no *runtime* dependency arrow (the architecturally correct fix may still
move such types into ``core``, but they don't rot the runtime hexagon).

Ratchet pattern: the breaches that exist today are listed in the ``KNOWN_*``
allowlists, so the gate is GREEN immediately and protective from the first
commit — any NEW breach not on the list fails the build.  Fixing a breach means
DELETING its allowlist entry; a *stale* entry (listed but no longer present in
the code) also fails the test, so the lists can only burn down to empty, never
accumulate dead weight.  When both lists are empty, the core ring is sealed.
"""
from __future__ import annotations

import ast
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
_GUARDED_TREES = ("trcc/core", "trcc/services")

# The Presentation Model layer (``ui/presentation``) is the Qt-free precursor to
# the View: it must import only inward (core / services / sibling PMs) so the
# coordination logic stays portable + unit-testable without a QApplication.  It
# must NOT import a GUI toolkit, an adapter, the App composition root, or another
# UI view (gui/qtgui/cli/api).  This makes the PMs' purity machine-enforced
# rather than convention.  (PM refactor increment 5.)
_PRESENTATION_TREE = "trcc/ui/presentation"
_PRESENTATION_FORBIDDEN_PREFIXES = (
    "trcc.adapters", "trcc.app", "trcc._boot",
    "trcc.ui.gui", "trcc.ui.qtgui", "trcc.ui.cli", "trcc.ui.api",
)

# Top-level packages the inner rings must never import: GUI toolkits, OS
# bindings, USB stacks.  ``win32*`` (pywin32) is matched by prefix below.
_BANNED_TOPLEVEL = frozenset({
    "PySide6", "PyQt5", "PyQt6", "shiboken6",
    "wmi", "winreg", "pythoncom", "pywintypes",
    "objc", "Foundation", "IOKit", "Quartz",
    "dbus", "gi", "pynvml", "usb", "hid",
})

# ── Ratchet allowlists — pre-existing breaches being burned down ─────────────
# Keyed by (path relative to src/, resolved import target).  Delete an entry as
# its breach is fixed; an entry that no longer matches any real import fails the
# test (so fixes can't leave dead allowlist cruft behind).
# Empty — the core/services rings are sealed.  A new forbidden import fails the
# gate immediately (no quarantine to hide behind).
KNOWN_IMPORT_BREACHES: frozenset[tuple[str, str]] = frozenset()

# Keyed by path relative to src/.  OS variance belongs in Platform subclasses.
KNOWN_OS_SNIFF_FILES: frozenset[str] = frozenset()

_OS_SNIFF_CALLS = frozenset({
    "system", "machine", "release", "version", "uname", "win32_ver", "mac_ver",
})

# CLAUDE.md "Logging": ``configure_logging`` is called exactly once — the CLI
# root callback.  A second call silently downgrades the user's ``-v`` back to
# INFO.  Only these files may call it.
_CONFIGURE_LOGGING_ALLOWED = frozenset({"trcc/ui/cli/main.py"})

# CLAUDE.md "Code Style": pathlib.Path preferred; ``os.path`` only where lexical
# path-STRING normalization is genuinely required — zip-slip member sanitisation
# (pathlib deliberately won't collapse ``..``, so it's the wrong tool there).
_OS_PATH_ALLOWED = frozenset({"trcc/adapters/repo/data_install.py"})


def _module_name(path: Path) -> str:
    return ".".join(path.relative_to(_SRC).with_suffix("").parts)


def _resolve_import(node: ast.ImportFrom, module_name: str) -> str:
    """Resolve a (possibly relative) ``from ... import`` to an absolute module."""
    if node.level == 0:
        return node.module or ""
    parts = module_name.split(".")
    base = parts[: len(parts) - node.level]
    return ".".join(base + ([node.module] if node.module else []))


def _is_forbidden_target(target: str) -> bool:
    if target.startswith(("trcc.adapters", "trcc.ui")):
        return True
    top = target.split(".", 1)[0]
    return top in _BANNED_TOPLEVEL or top.startswith("win32")


def _is_forbidden_in_presentation(target: str) -> bool:
    """True if ``target`` is an import the Presentation Model layer must not make.

    Allows stdlib + ``trcc.core`` / ``trcc.services`` / ``trcc.ui.presentation``;
    forbids GUI toolkits / OS bindings (``_BANNED_TOPLEVEL``), adapters, the App
    composition root, and the other UI views.
    """
    if target.startswith(_PRESENTATION_FORBIDDEN_PREFIXES):
        return True
    top = target.split(".", 1)[0]
    return top in _BANNED_TOPLEVEL or top.startswith("win32")


def _is_type_checking_test(test: ast.expr) -> bool:
    return (
        (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING")
        or (isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING")
    )


class _ImportCollector(ast.NodeVisitor):
    """Collect every runtime import target (module-level AND function-body)."""

    def __init__(self, module_name: str) -> None:
        self.module_name = module_name
        self.found: list[tuple[int, str]] = []

    def visit_If(self, node: ast.If) -> None:
        if _is_type_checking_test(node.test):
            for stmt in node.orelse:   # else-branch runs at runtime; body does not
                self.visit(stmt)
            return
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.found.append((node.lineno, alias.name))

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self.found.append((node.lineno, _resolve_import(node, self.module_name)))


class _OsSniffCollector(ast.NodeVisitor):
    """Collect ``sys.platform`` reads and ``platform.system()``-style calls."""

    def __init__(self) -> None:
        self.found: list[tuple[int, str]] = []

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if (isinstance(node.value, ast.Name) and node.value.id == "sys"
                and node.attr == "platform"):
            self.found.append((node.lineno, "sys.platform"))
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if (isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id == "platform"
                and func.attr in _OS_SNIFF_CALLS):
            self.found.append((node.lineno, f"platform.{func.attr}()"))
        self.generic_visit(node)


def _files_under(*trees: str) -> list[Path]:
    files: list[Path] = []
    for tree in trees:
        for path in sorted((_SRC / tree).rglob("*.py")):
            if "__pycache__" not in path.parts:
                files.append(path)
    return files


def _guarded_files() -> list[Path]:
    return _files_under(*_GUARDED_TREES)


class _CallNameCollector(ast.NodeVisitor):
    """Collect call sites of bare ``name(...)`` and ``shell=True`` kwargs."""

    def __init__(self, wanted: frozenset[str]) -> None:
        self.wanted = wanted
        self.calls: list[int] = []          # line numbers of wanted calls
        self.shell_true: list[int] = []     # line numbers of shell=True kwargs

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        name = (func.id if isinstance(func, ast.Name)
                else func.attr if isinstance(func, ast.Attribute) else None)
        if name in self.wanted:
            self.calls.append(node.lineno)
        for kw in node.keywords:
            if (kw.arg == "shell" and isinstance(kw.value, ast.Constant)
                    and kw.value.value is True):
                self.shell_true.append(node.lineno)
        self.generic_visit(node)


class _OsPathCollector(ast.NodeVisitor):
    """Collect ``os.path`` attribute access (``os.path.<anything>``)."""

    def __init__(self) -> None:
        self.lines: list[int] = []

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if (isinstance(node.value, ast.Name) and node.value.id == "os"
                and node.attr == "path"):
            self.lines.append(node.lineno)
        self.generic_visit(node)


def _scan() -> tuple[list[tuple[str, int, str]], list[tuple[str, int, str]]]:
    imports: list[tuple[str, int, str]] = []
    sniffs: list[tuple[str, int, str]] = []
    for path in _guarded_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
        rel = str(path.relative_to(_SRC))
        module = _module_name(path)

        ic = _ImportCollector(module)
        ic.visit(tree)
        for lineno, target in ic.found:
            if _is_forbidden_target(target):
                imports.append((rel, lineno, target))

        oc = _OsSniffCollector()
        oc.visit(tree)
        for lineno, target in oc.found:
            sniffs.append((rel, lineno, target))
    return imports, sniffs


def test_core_and_services_have_no_new_forbidden_imports() -> None:
    """No core/services module imports an adapter / ui / framework at runtime.

    Pre-existing breaches are quarantined in ``KNOWN_IMPORT_BREACHES``; any
    import outside that set is a regression and fails here.
    """
    imports, _ = _scan()
    new = [(rel, ln, tgt) for rel, ln, tgt in imports
           if (rel, tgt) not in KNOWN_IMPORT_BREACHES]
    assert not new, (
        "New inward-dependency violation(s) — core/services must not import "
        "adapters/ui/frameworks (move the dependency behind a core port):\n"
        + "\n".join(f"  {rel}:{ln}  ->  {tgt}" for rel, ln, tgt in new)
    )


def test_core_and_services_have_no_new_os_sniffing() -> None:
    """No core/services module branches on ``sys.platform`` / ``platform.*``.

    OS variance belongs in ``adapters/system/{os}.py`` Platform subclasses.
    """
    _, sniffs = _scan()
    new = [(rel, ln, tgt) for rel, ln, tgt in sniffs
           if rel not in KNOWN_OS_SNIFF_FILES]
    assert not new, (
        "New OS-sniff in core/services — move the per-OS behaviour onto the "
        "Platform port:\n"
        + "\n".join(f"  {rel}:{ln}  ->  {tgt}" for rel, ln, tgt in new)
    )


def test_import_allowlist_has_no_stale_entries() -> None:
    """Every quarantined import still exists — fixed breaches must be removed.

    Forces the ratchet to burn down: once a breach is fixed, leaving its
    allowlist entry behind fails here, so the list can only shrink.
    """
    imports, _ = _scan()
    present = {(rel, tgt) for rel, _ln, tgt in imports}
    stale = sorted(KNOWN_IMPORT_BREACHES - present)
    assert not stale, (
        "Stale KNOWN_IMPORT_BREACHES entries (breach fixed — delete the "
        "allowlist line):\n" + "\n".join(f"  {rel}  ->  {tgt}" for rel, tgt in stale)
    )


def test_os_sniff_allowlist_has_no_stale_entries() -> None:
    """Every quarantined OS-sniff file still sniffs — fixed ones must be removed."""
    _, sniffs = _scan()
    present = {rel for rel, _ln, _tgt in sniffs}
    stale = sorted(KNOWN_OS_SNIFF_FILES - present)
    assert not stale, (
        "Stale KNOWN_OS_SNIFF_FILES entries (OS-sniff removed — delete the "
        "allowlist line):\n" + "\n".join(f"  {rel}" for rel in stale)
    )


def test_no_shell_true_subprocess_anywhere() -> None:
    """CLAUDE.md Security: ``subprocess`` runs ``shell=False`` (arg lists only).

    A ``shell=True`` anywhere in the app is a shell-injection surface — banned
    outright, no allowlist.
    """
    offenders: list[str] = []
    for path in _files_under("trcc"):
        tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
        cc = _CallNameCollector(frozenset())
        cc.visit(tree)
        rel = str(path.relative_to(_SRC))
        offenders += [f"  {rel}:{ln}" for ln in cc.shell_true]
    assert not offenders, (
        "shell=True is banned (use a subprocess arg list, shell=False):\n"
        + "\n".join(offenders)
    )


def test_core_and_services_never_call_print() -> None:
    """CLAUDE.md Code Style: never ``print()`` — use the logger.

    Scoped to ``core``/``services`` where stdout output is never legitimate
    (setup adapters print user-facing console output, so they're out of scope).
    """
    offenders: list[str] = []
    for path in _guarded_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
        cc = _CallNameCollector(frozenset({"print"}))
        cc.visit(tree)
        rel = str(path.relative_to(_SRC))
        offenders += [f"  {rel}:{ln}" for ln in cc.calls]
    assert not offenders, (
        "print() in core/services — use ``log = logging.getLogger(__name__)``:\n"
        + "\n".join(offenders)
    )


def test_configure_logging_has_one_call_site() -> None:
    """CLAUDE.md Logging: ``configure_logging`` is called exactly once.

    A second call (e.g. from a GUI launch entry point) silently downgrades the
    user's ``-v`` back to INFO.  Only the CLI root callback may call it.
    """
    offenders: list[str] = []
    for path in _files_under("trcc"):
        tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
        cc = _CallNameCollector(frozenset({"configure_logging"}))
        cc.visit(tree)
        rel = str(path.relative_to(_SRC))
        if cc.calls and rel not in _CONFIGURE_LOGGING_ALLOWED:
            offenders += [f"  {rel}:{ln}" for ln in cc.calls]
    assert not offenders, (
        "configure_logging called outside the CLI root (silently resets the "
        "log level — route launch through the CLI):\n" + "\n".join(offenders)
    )


def test_os_path_confined_to_zip_slip_normalisation() -> None:
    """CLAUDE.md Code Style: prefer pathlib; ``os.path`` only where a lexical
    path-string operation is required (zip-slip member sanitisation).

    Everything else uses ``pathlib.Path``.  Only ``_OS_PATH_ALLOWED`` files may
    touch ``os.path``.
    """
    offenders: list[str] = []
    for path in _files_under("trcc"):
        rel = str(path.relative_to(_SRC))
        if rel in _OS_PATH_ALLOWED:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
        oc = _OsPathCollector()
        oc.visit(tree)
        offenders += [f"  {rel}:{ln}" for ln in oc.lines]
    assert not offenders, (
        "os.path outside the allowed zip-slip site — use pathlib.Path:\n"
        + "\n".join(offenders)
    )


def test_presentation_layer_is_qt_app_and_adapter_free() -> None:
    """``ui/presentation`` (the Presentation Model layer) imports only inward.

    The PMs are the Qt-free precursor to the View — they must never import a GUI
    toolkit, an adapter, the App composition root, or another UI view, so the
    coordination logic stays portable and unit-testable without a QApplication.
    Walks module-level AND function-body imports (skips ``TYPE_CHECKING``).  No
    allowlist: the layer is pure today, so any NEW breach fails the build.
    """
    breaches: list[str] = []
    for path in _files_under(_PRESENTATION_TREE):
        module = _module_name(path)
        collector = _ImportCollector(module)
        collector.visit(ast.parse(path.read_text(encoding="utf-8"), str(path)))
        rel = str(path.relative_to(_SRC))
        for lineno, target in collector.found:
            if _is_forbidden_in_presentation(target):
                breaches.append(f"  {rel}:{lineno} -> {target}")
    assert not breaches, (
        "ui/presentation must stay Qt/App/adapter-free (import only core / "
        "services / sibling PMs):\n" + "\n".join(breaches)
    )


# ── Query purity ─────────────────────────────────────────────────────────────
# A Query answers and changes nothing.  Stated in its docstring, enforced here,
# because a docstring is not a contract.  Detection is AST-based, on the CALLED
# ATTRIBUTE NAME — a substring scan flags `"%s is not attached"` and
# `p.install_method()` as mutations, which is exactly the false-positive class
# that has cost this project real time.

_MUTATING_CALLS = frozenset({
    "publish",       # an event means something changed
    "invalidate",    # scene cache mutation
    "write_text", "write_bytes", "mkdir", "unlink", "rmtree", "touch",
})
_MUTATING_PREFIXES = ("set_", "save", "store_", "delete_", "clear_")

_COMMANDS_TREE = _SRC / "trcc" / "core" / "commands"


def _query_mutations(source: str) -> list[str]:
    """Mutating calls inside every ``Query`` subclass's ``execute`` in *source*."""
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.ClassDef)
                and any("Query[" in ast.unparse(b) for b in node.bases)):
            continue
        for body in node.body:
            if not (isinstance(body, ast.FunctionDef) and body.name == "execute"):
                continue
            for call in ast.walk(body):
                if not (isinstance(call, ast.Call)
                        and isinstance(call.func, ast.Attribute)):
                    continue
                attr = call.func.attr
                if attr in _MUTATING_CALLS or attr.startswith(_MUTATING_PREFIXES):
                    found.append(f"{node.name}.execute -> .{attr}()")
    return found


def test_a_query_never_mutates() -> None:
    """``Query`` is the read half of the bus; a read that writes is a lie.

    Without this, "Query" is a naming convention, and the next author to add a
    ``publish`` to one gets a silent event on a call a UI polls once a second.
    """
    breaches: list[str] = []
    for path in sorted(_COMMANDS_TREE.glob("*.py")):
        for hit in _query_mutations(path.read_text(encoding="utf-8")):
            breaches.append(f"  {path.name}: {hit}")
    assert not breaches, (
        "a Query must not mutate — publish an event, write a setting or touch "
        "the filesystem.  Make it a Command instead:\n" + "\n".join(breaches)
    )


# ── Gate self-tests (the watchmen) ───────────────────────────────────────────
# A boundary gate with a logic bug that silently stops detecting is worse than
# no gate — everything goes green and breaches slip through unseen.  These feed
# the scanners synthetic known-bad / known-good fixtures and assert the engine
# still has teeth.  If detection ever regresses to "never flags", these fail.

def _imports_in(source: str, module: str = "trcc.core.commands.system") -> list[str]:
    collector = _ImportCollector(module)
    collector.visit(ast.parse(source))
    return [tgt for _ln, tgt in collector.found]


def test_selftest_query_purity_predicate_has_teeth() -> None:
    """Feed the scanner a Query that publishes; it must catch it."""
    bad = (
        "class Naughty(Query[Result]):\n"
        "    def execute(self, app):\n"
        "        app.events.publish(ThemeLoaded(key='k'))\n"
        "        return Result(ok=True)\n"
    )
    assert _query_mutations(bad) == ["Naughty.execute -> .publish()"]


def test_selftest_query_purity_ignores_commands() -> None:
    """A Command publishing is correct — the scanner must not flag it."""
    fine = (
        "class Proper(Command[Result]):\n"
        "    def execute(self, app):\n"
        "        app.events.publish(ThemeLoaded(key='k'))\n"
        "        return Result(ok=True)\n"
    )
    assert _query_mutations(fine) == []


def test_selftest_query_purity_ignores_words_in_strings() -> None:
    """The false-positive class this predicate exists to avoid.

    ``'%s is not attached'`` and ``p.install_method()`` are not mutations; a
    substring scan says they are.  Detection keys on the CALLED attribute.
    """
    innocent = (
        "class Reader(Query[Result]):\n"
        "    def execute(self, app):\n"
        "        log.debug('%s is not attached; nothing to set_ or save', k)\n"
        "        return Result(ok=True, method=app.platform.install_method())\n"
    )
    assert _query_mutations(innocent) == []


def test_selftest_relative_import_resolution() -> None:
    """The subtlest piece — if this miscomputes, ALL detection silently dies."""
    node = ast.parse("from ...adapters.diagnostics.health import x").body[0]
    assert isinstance(node, ast.ImportFrom)
    assert _resolve_import(node, "trcc.core.commands.system") == (
        "trcc.adapters.diagnostics.health"
    )
    absolute = ast.parse("from trcc.adapters.repo.http import F").body[0]
    assert isinstance(absolute, ast.ImportFrom)
    assert _resolve_import(absolute, "trcc.services.x") == "trcc.adapters.repo.http"


def test_selftest_forbidden_target_predicate() -> None:
    assert _is_forbidden_target("trcc.adapters.render.qt")
    assert _is_forbidden_target("trcc.ui.gui.trcc_app")
    assert _is_forbidden_target("PySide6.QtGui")
    assert _is_forbidden_target("win32api")          # pywin32 prefix
    assert not _is_forbidden_target("trcc.core.models")
    assert not _is_forbidden_target("logging")
    assert not _is_forbidden_target("trcc.services.display")
    # The content store's home.  This is the gate that makes the port real:
    # core/services reaching back into the implementation is a breach now.
    assert _is_forbidden_target("trcc.adapters.theme.filesystem")


def test_selftest_presentation_forbidden_predicate() -> None:
    """The PM-layer predicate flags Qt/adapter/App/other-view, allows inward."""
    assert _is_forbidden_in_presentation("PySide6.QtCore")
    assert _is_forbidden_in_presentation("trcc.adapters.render.qt")
    assert _is_forbidden_in_presentation("trcc.app")            # composition root
    assert _is_forbidden_in_presentation("trcc.ui.gui.lcd_handler")
    assert _is_forbidden_in_presentation("trcc.ui.qtgui.foo")
    assert not _is_forbidden_in_presentation("trcc.core.models")
    assert not _is_forbidden_in_presentation("trcc.services._dc")
    assert not _is_forbidden_in_presentation("trcc.ui.presentation.preview_geometry")
    assert not _is_forbidden_in_presentation("logging")


def test_selftest_import_collector_catches_function_body_import() -> None:
    """The grep-dodge: an adapter import buried inside a method must be caught."""
    src = "def execute(self):\n    from ...adapters.x import y\n    return y\n"
    assert "trcc.adapters.x" in _imports_in(src)


def test_selftest_import_collector_skips_type_checking_block() -> None:
    """TYPE_CHECKING imports never run — no runtime arrow, must NOT be flagged."""
    src = "if TYPE_CHECKING:\n    from ...adapters.x import y\n"
    assert "trcc.adapters.x" not in _imports_in(src)


def test_selftest_clean_source_is_not_flagged() -> None:
    """Known-good source must produce zero findings (no false positives)."""
    src = "from ...core.models import Wire\nimport logging\n"
    assert not [t for t in _imports_in(src) if _is_forbidden_target(t)]


def test_selftest_os_sniff_collector_catches_sys_platform() -> None:
    oc = _OsSniffCollector()
    oc.visit(ast.parse("if sys.platform == 'win32':\n    x = 1\n"))
    assert any(tgt == "sys.platform" for _ln, tgt in oc.found)
    clean = _OsSniffCollector()
    clean.visit(ast.parse("y = sys.prefix\n"))   # not a platform sniff
    assert not clean.found


def test_selftest_shell_true_and_os_path_collectors_have_teeth() -> None:
    cc = _CallNameCollector(frozenset())
    cc.visit(ast.parse("subprocess.run(cmd, shell=True)\n"))
    assert cc.shell_true
    safe = _CallNameCollector(frozenset())
    safe.visit(ast.parse("subprocess.run(cmd, shell=False)\n"))
    assert not safe.shell_true

    op = _OsPathCollector()
    op.visit(ast.parse("p = os.path.join('a', 'b')\n"))
    assert op.lines


def test_ok_false_results_carry_a_message() -> None:
    """Every ``Result(ok=False, …)`` in a core Command must carry a non-empty
    ``message``.

    ``App.dispatch`` is the universal user-action log: it WARNs on every
    ``ok=False`` outcome with ``result.message``.  A blank/absent message makes
    a rejected user action invisible there — so the message IS the log line for
    that branch.  This gate keeps every failure branch self-explanatory without
    mandating a duplicate per-branch ``log`` call. (logging coverage)
    """
    offenders: list[str] = []
    for path in _files_under("trcc/core/commands"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            kw = {k.arg: k.value for k in node.keywords if k.arg}
            ok = kw.get("ok")
            if not (isinstance(ok, ast.Constant) and ok.value is False):
                continue
            msg = kw.get("message")
            blank = msg is None or (
                isinstance(msg, ast.Constant) and not str(msg.value).strip()
            )
            if blank:
                offenders.append(f"{path.relative_to(_SRC)}:{node.lineno}")
    assert not offenders, (
        "Result(ok=False) with no/blank message — App.dispatch's WARNING would "
        f"be uninformative: {offenders}"
    )


def test_gui_on_handlers_log_or_are_exempt() -> None:
    """Every GUI ``_on_*`` user-interaction handler must LOG — a click /
    selection / value change is a user action and must be visible.

    EXEMPT: per-tick handlers (timer-wired via ``timeout.connect`` /
    ``make_timer``, or named ``*_tick``) which are DEBUG/silent by design, and
    debounced live-drag handlers (they re-arm a debounce; the SETTLED value
    logs elsewhere).  Locks in user-interaction click coverage so a new silent
    handler fails CI. (logging coverage)
    """
    import re

    log_methods = {"info", "debug", "warning", "error", "exception", "critical"}
    gui_files = _files_under("trcc/ui/gui")
    alltext = "\n".join(p.read_text(encoding="utf-8") for p in gui_files)
    timer_wired = set(re.findall(
        r"(?:timeout\.connect|make_timer)\(\s*self\.(\w+)", alltext))

    def _logs(node: ast.AST) -> bool:
        for n in ast.walk(node):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and n.func.attr in log_methods):
                base = n.func.value
                # ``frame_log`` is the per-frame family (see core.logs) — a
                # handler that logs through it IS logging; it is simply gated
                # behind -v with the rest of the frame path.  Not recognising
                # it would read "moved to a cheaper logger" as "went silent".
                if isinstance(base, ast.Name) and base.id in (
                        "log", "logger", "frame_log"):
                    return True
                if (isinstance(base, ast.Attribute)
                        and base.attr in ("log", "logger", "_log")):
                    return True
        return False

    def _arms_debounce(fn: ast.AST) -> bool:
        for n in ast.walk(fn):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and n.func.attr == "start"):
                tgt = ast.unparse(n.func.value).lower()
                if "debounce" in tgt or "_timer" in tgt:
                    return True
        return False

    offenders: list[str] = []
    for path in gui_files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
            for fn in cls.body:
                if (not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
                        or not fn.name.startswith("_on_")):
                    continue
                if _logs(fn):
                    continue
                if fn.name in timer_wired or fn.name.endswith("_tick"):
                    continue   # per-tick — DEBUG/silent by design
                if _arms_debounce(fn):
                    continue   # live-drag — settled value logs elsewhere
                offenders.append(
                    f"{path.relative_to(_SRC)}:{fn.lineno} {cls.name}.{fn.name}")
    assert not offenders, (
        "GUI _on_* handler with no log (not tick/debounce-exempt) — a user "
        f"interaction would be invisible in the log: {offenders}"
    )


def test_local_theme_browser_view_does_no_filesystem_walk() -> None:
    """The local theme browser Views (legacy ``uc_theme_local`` AND the qtgui
    ``local_theme_browser``) render ListThemes entries — they must NOT walk the
    disk themselves.

    A private disk walk in the View is exactly what diverged from the universal
    ``ListThemes`` Command and shadowed a user-saved theme behind a same-named
    shipped one (the green-"Theme1" collision).  This gate keeps listing +
    deletion flowing through Commands in BOTH UIs. (#theme-collision)
    """
    browsers = (
        _SRC / "trcc" / "ui" / "gui" / "uc_theme_local.py",
        _SRC / "trcc" / "ui" / "qtgui" / "panels" / "local_theme_browser.py",
    )
    forbidden = {"iterdir", "glob", "rglob", "scandir", "rmtree", "walk",
                 "listdir"}
    offenders = [
        f"{path.name}:{n.lineno} .{n.func.attr}()"
        for path in browsers
        for n in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and n.func.attr in forbidden
    ]
    assert not offenders, (
        "theme browser View does filesystem traversal — listing must come from "
        f"ListThemes (set_themes), deletion from DeleteTheme: {offenders}"
    )


# =========================================================================
# Daemon-safety: CLI / API must reach App state through Commands only
# =========================================================================


def test_cli_and_api_never_touch_app_settings_directly() -> None:
    """The command-only UIs must not read/write ``app.settings``.

    Under ``TRCC_DAEMON=1`` those adapters hold an ``AppProxy``, which exposes
    ``dispatch(cmd)`` and raises ``AttributeError`` for everything else.  So a
    bare ``app.settings.…`` works in-process and CRASHES against a daemon —
    exactly how ``display play-video`` died before reaching the wire, and
    ``display play`` / ``led play`` / ``GET /system/language`` alongside it
    (#249).  It is also a hexagonal breach: state changes belong to Commands,
    which every UI already dispatches, so routing through the bus keeps
    CLI / API / GUI / qtgui identical AND daemon-safe.

    Detects ``<anything>.settings`` attribute access in ui/cli + ui/api.  The
    fix is always a Command: ``ControlCenterSnapshot`` to read app prefs,
    ``Set*`` / the owning Command to write.
    """
    offenders: list[str] = []
    for path in _files_under("trcc/ui/cli", "trcc/ui/api"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "settings":
                offenders.append(
                    f"{path.relative_to(_SRC)}:{node.lineno} — "
                    f"reaches .settings directly"
                )
    assert not offenders, (
        "CLI/API must query + mutate App state via Commands, never "
        "app.settings (AppProxy exposes dispatch() only, so these crash "
        "under TRCC_DAEMON=1):\n  " + "\n  ".join(offenders)
    )


# =============================================================================
# Storage boundary — the one outbound dependency with no port
# =============================================================================
#
# ``core`` and ``services`` are the inner rings; CLAUDE.md calls services "the
# core hexagon — all business logic, PURE PYTHON".  Core declares 23 outbound
# ports (transports, sensors, Paths, Renderer, HttpFetcher, ScreenCapture,
# PackageManager, …) — every outbound dependency EXCEPT the filesystem.
# ``Paths`` answers *where* a thing belongs; nothing answers *put it there*, so
# the inner rings call ``shutil`` / ``Path.write_bytes`` directly.
#
# Nothing caught it: the import gate above bans adapters, UI and frameworks,
# and ``shutil`` / ``zipfile`` / ``pathlib`` are unbanned stdlib.  These two
# ratchets are that missing gate.  Both work like ``test_logging_coverage``:
# a count that RISES fails (new breach) and a count that FALLS fails (fix
# landed — lower the baseline so the ground cannot be given back quietly).

_FS_WRITE_CALLS = frozenset({
    "write_text", "write_bytes", "mkdir", "unlink", "rmdir", "touch",
    "symlink_to", "chmod", "fsync",
})
_FS_READ_CALLS = frozenset({"read_text", "read_bytes"})
# Path INTERROGATION — asking the filesystem a question ABOUT a path rather
# than moving its bytes.  These were absent until 2026-08-24, which made the
# total read as "filesystem calls in core+services" when it only ever counted
# content I/O: the old ``ThemeService`` scored 34 here against 87 by a full
# sweep.  A check whose denominator excludes what it seeks reports a gap it
# cannot see, so the probes are counted and the baselines re-measured.
_FS_PROBE_CALLS = frozenset({
    "exists", "is_dir", "is_file", "iterdir", "glob", "rglob", "stat", "lstat",
})
_FS_MODULES = frozenset({"shutil", "zipfile", "tarfile", "tempfile"})

# Per-file file-I/O counts in core/ + services/, as they stand.  Burn these
# down by moving the work behind the storage port; drop an entry when it hits
# zero.  ``.replace()`` and ``.open()`` are deliberately NOT counted — str.replace
# and builtins.open share those names, and counting them inflated the first
# measurement of this by 40%.
# ``services/theme.py`` (34) is GONE from this table, not fixed in place: the
# class was a persistence adapter filed under ``services/`` — 21 of its 25
# methods touched the filesystem — and it now lives at
# ``adapters/theme/filesystem.py`` behind the ``ContentStore`` port.  This
# ratchet counts the inner rings, so re-homing it removes it from the
# denominator.
#
# RE-BASELINED 2026-08-24, and the jump is the point: 33 -> 110.  Nothing
# regressed.  The counter had never looked at path INTERROGATION, so it was
# reporting content I/O under the name "filesystem calls" — and four files
# doing nothing but interrogation were invisible to it ENTIRELY:
# ``services/display.py`` (4), ``services/cloud_theme.py`` (5),
# ``services/overlay.py`` (2) and ``core/libraries.py`` (1).  Two of those are
# the services CLAUDE.md calls the pure-Python core hexagon.  A gate that
# cannot see a file cannot report that file's drift, which is why the numbers
# below are measured rather than carried forward.
KNOWN_FS_IO: dict[str, int] = {
    "trcc/core/_safe.py": 3,
    "trcc/core/commands/_helpers.py": 7,
    "trcc/core/commands/device.py": 13,
    "trcc/core/commands/theme.py": 32,
    "trcc/core/libraries.py": 1,
    "trcc/core/toolchain.py": 2,
    "trcc/services/_dc.py": 3,
    "trcc/services/cloud_theme.py": 5,
    "trcc/services/display.py": 4,
    "trcc/services/first_run.py": 5,
    "trcc/services/media.py": 5,
    "trcc/services/migration.py": 13,
    "trcc/services/overlay.py": 2,
    "trcc/services/settings.py": 4,
    "trcc/services/theme_directories.py": 2,
    # +2 on 2026-08-27, and NOT a regression: ``theme_directories`` moved INTO
    # services from ``ui/presentation`` so a core Query could call it, bringing
    # its two ``.exists()`` probes (the #136 portrait fallback, and the
    # same-name variant lookup) into the counted rings.  The mirror image of
    # the ``services/theme.py`` row that LEFT this table when it was re-homed
    # to an adapter: this ratchet counts the inner rings, so what it measures
    # moves when a file does.
    "trcc/services/video_export.py": 7,
}


def _is_fs_call(node: ast.Call) -> bool:
    """True if *node* asks the filesystem something.

    ``.replace()`` and ``.open()`` stay OUT: ``str.replace`` and
    ``builtins.open`` share those names and counting them inflated the first
    measurement of this by 40%.

    ``resolve`` is the one name that needed a discriminator rather than a
    verdict.  ``Path.resolve()`` takes no positional operand — the path IS the
    receiver — while all three same-named calls in the inner rings that are
    NOT filesystem calls pass one: ``Registry._on_missing.resolve(name, key,
    table)`` and ``toolchain.resolve('ffmpeg')`` twice (a PATH probe, which
    belongs with ``PackageManager``).  Arity is the rule, not a blocklist of
    those three receivers, so a new non-path ``resolve`` cannot silently join
    the count — and ``path.resolve(strict=True)`` still does.
    """
    if not isinstance(node.func, ast.Attribute):
        return False
    name = node.func.attr
    if getattr(node.func.value, "id", None) in _FS_MODULES:
        return True
    if name in _FS_WRITE_CALLS or name in _FS_READ_CALLS or name in _FS_PROBE_CALLS:
        return True
    return name == "resolve" and not node.args


def _fs_io_counts() -> dict[str, int]:
    """Per-file count of unambiguous filesystem calls in core/ + services/."""
    counts: dict[str, int] = {}
    for area in ("core", "services"):
        for path in sorted((_SRC / "trcc" / area).rglob("*.py")):
            rel = str(path.relative_to(_SRC))
            n = sum(
                1 for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
                if isinstance(node, ast.Call) and _is_fs_call(node)
            )
            if n:
                counts[rel] = n
    return counts


def test_no_new_filesystem_io_in_core_or_services() -> None:
    """The inner rings must not grow new direct filesystem calls."""
    counts = _fs_io_counts()
    risen = {f: (KNOWN_FS_IO.get(f, 0), n) for f, n in counts.items()
             if n > KNOWN_FS_IO.get(f, 0)}
    assert not risen, (
        "New direct filesystem I/O in core/services — infrastructure belongs "
        "behind a port, not in the hexagon:\n"
        + "\n".join(f"  {f}: {was} → {now}" for f, (was, now) in risen.items())
    )


def test_filesystem_io_baseline_has_no_slack() -> None:
    """A fixed breach must lower the baseline, so ground cannot be re-taken."""
    counts = _fs_io_counts()
    stale = {f: (want, counts.get(f, 0)) for f, want in KNOWN_FS_IO.items()
             if counts.get(f, 0) < want}
    assert not stale, (
        "Filesystem I/O went DOWN — lower KNOWN_FS_IO to lock the win in:\n"
        + "\n".join(f"  {f}: {want} → {now}" for f, (want, now) in stale.items())
    )


# ── The gui must ask the bus, like every other UI ────────────────────────────
#
# ``AppProxy.__getattr__`` raises for everything except ``dispatch`` — "daemon
# mode only exposes dispatch(cmd)".  So every ``self._app.<attr>`` in a UI is an
# AttributeError under ``TRCC_DAEMON=1``, and that — not missing plumbing — is
# what "GUI as a remote daemon client" being pending actually means.  Measured
# against a real proxy: all 11 distinct attributes the two GUIs reach for raise.
#
# The CLI and API are already at ZERO; they dispatch Commands and read Results.
# The Queries the GUIs need were all built for #249 and never adopted:
# ``ControlCenterSnapshot``, ``LedSnapshot``, ``GetPaths``, ``DeviceState``,
# ``VideoStatus``, ``BuildPreview``, ``LcdSnapshot``.
#
# This is a ratchet rather than a ban because burn-down leaks without one: the
# session that first measured these added a NEW reach (``store=self._app.themes``)
# while removing port-passing elsewhere in the same pass.
#
# ``dispatch`` is the whole point and is never counted.  Two reaches are
# deliberately IN the baseline but out of scope for burn-down — ``splash``'s
# ``discover_and_connect`` and ``closeEvent``'s ``close``.  They are app
# LIFECYCLE, which a GUI running as a daemon *client* must not own; converting
# them would be wrong, so they stay counted and stay put.
_APP_ATTRS = frozenset({"_app", "app", "_trcc"})

KNOWN_APP_REACHES: dict[str, int] = {
    "ui/gui/lcd_handler.py": 6,
    "ui/gui/splash.py": 1,
    "ui/gui/trcc_app.py": 11,
    "ui/qtgui/panels/device_panel.py": 1,
    "ui/qtgui/panels/led/_base.py": 1,
    "ui/qtgui/panels/led_panel.py": 1,
    "ui/qtgui/panels/screencast_panel.py": 2,
}


def _is_app_reach(node: ast.Attribute) -> bool:
    """True if *node* reads an App attribute other than ``dispatch``.

    Matches ``self._app.x`` / ``self.app.x`` / ``self._trcc.x``.  Grep cannot
    do this job — the pattern that was tried first required a trailing dot and
    a fixed service list, and undercounted 15 against 50 because
    ``self._app.display`` passed as an ARGUMENT never matched.
    """
    value = node.value
    return (
        isinstance(value, ast.Attribute)
        and isinstance(value.value, ast.Name)
        and value.value.id == "self"
        and value.attr in _APP_ATTRS
        and node.attr != "dispatch"
    )


def _app_reach_counts() -> dict[str, int]:
    counts: dict[str, int] = {}
    for path in (_SRC / "trcc" / "ui").rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(_SRC / "trcc").as_posix()
        n = sum(
            1 for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
            if isinstance(node, ast.Attribute) and _is_app_reach(node)
        )
        if n:
            counts[rel] = n
    return counts


def test_no_new_ui_reaches_past_dispatch() -> None:
    """A UI must not grow new App-internal reads — each one breaks daemon mode."""
    counts = _app_reach_counts()
    risen = {f: (KNOWN_APP_REACHES.get(f, 0), n) for f, n in counts.items()
             if n > KNOWN_APP_REACHES.get(f, 0)}
    assert not risen, (
        "New UI reach past dispatch — every one is an AttributeError under "
        "TRCC_DAEMON=1; ask the bus with a Query instead:\n"
        + "\n".join(f"  {f}: {was} → {now}" for f, (was, now) in risen.items())
    )


def test_ui_reach_baseline_has_no_slack() -> None:
    """An adopted Query must lower the baseline, so ground cannot be re-taken."""
    counts = _app_reach_counts()
    stale = {f: (want, counts.get(f, 0)) for f, want in KNOWN_APP_REACHES.items()
             if counts.get(f, 0) < want}
    assert not stale, (
        "UI reaches went DOWN — lower KNOWN_APP_REACHES to lock the win in:\n"
        + "\n".join(f"  {f}: {want} → {now}" for f, (want, now) in stale.items())
    )


def test_cli_and_api_never_reach_past_dispatch() -> None:
    """The two UIs that are already clean must STAY clean.

    Not a ratchet — an invariant.  They were measured at zero, so there is no
    baseline to burn down and any reach at all is a regression.
    """
    counts = _app_reach_counts()
    dirty = {f: n for f, n in counts.items()
             if f.startswith(("ui/cli/", "ui/api/"))}
    assert not dirty, (
        "The CLI/API dispatch Commands and read Results — keep it that way:\n"
        + "\n".join(f"  {f}: {n}" for f, n in dirty.items())
    )


# ── The theme-directory layout is already a domain object; use it ────────────
#
# ``core.models.ThemeDir`` owns the layout (``00.png`` / ``01.png`` /
# ``Theme.png`` / ``config1.dc`` / ``trcc.json`` / ``config.json`` /
# ``Theme.zt``) and its docstring says ``FileContentStore`` MUST use these names
# rather than a "candidates list", so we never render ``Theme.png`` — which is
# the panel thumbnail, not the background.
#
# It is used in 3 files.  The names are spelled literally in 12 more, behind 7
# constants that duplicate a ``ThemeDir`` property that already exists —
# including ``services/theme.py``, which defines ``_CONFIG_FILE = "trcc.json"``
# while importing ``ThemeDir`` (which has ``.json``).  That is how a member
# with 13 spellings and no constant (``Theme.png``) happens.

_THEME_DIR_MEMBERS = frozenset({
    "00.png", "01.png", "Theme.png", "config1.dc", "trcc.json",
    "config.json", "Theme.zt",
})

# 48 → 2.  ``ThemeDir`` was adopted across all 12 files that re-spelled the
# layout, and the 7 constants duplicating one of its properties are gone.
#
# The two survivors are NOT breaches and never will be: both name a file in
# ``config_dir`` that merely SHARES a string with a theme member — the app's
# own ``trcc.json`` settings file, and the legacy app ``config.json`` the
# debug report reads.  Different files, same name; ``ThemeDir`` would be the
# wrong owner for either.  They are listed at their real count rather than
# exempted, so if one grows a third the gate still notices.
KNOWN_LAYOUT_LITERALS: dict[str, int] = {
    "trcc/adapters/diagnostics/debug_report.py": 1,
    "trcc/services/settings.py": 1,
}


def _layout_literal_counts() -> dict[str, int]:
    """Per-file count of theme-layout filenames spelled outside ``ThemeDir``."""
    counts: dict[str, int] = {}
    for path in sorted((_SRC / "trcc").rglob("*.py")):
        rel = str(path.relative_to(_SRC))
        if rel == "trcc/core/models.py":      # ThemeDir itself — the one owner
            continue
        n = sum(1 for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
                if isinstance(node, ast.Constant)
                and node.value in _THEME_DIR_MEMBERS)
        if n:
            counts[rel] = n
    return counts


def test_no_new_theme_layout_literals() -> None:
    """New code must ask ``ThemeDir``, not re-spell the filename."""
    counts = _layout_literal_counts()
    risen = {f: (KNOWN_LAYOUT_LITERALS.get(f, 0), n) for f, n in counts.items()
             if n > KNOWN_LAYOUT_LITERALS.get(f, 0)}
    assert not risen, (
        "New theme-layout filename literal(s) — use core.models.ThemeDir "
        "(.bg/.mask/.preview/.dc/.json/.legacy_json/.zt):\n"
        + "\n".join(f"  {f}: {was} → {now}" for f, (was, now) in risen.items())
    )


def test_theme_layout_literal_baseline_has_no_slack() -> None:
    """Adopting ThemeDir must lower the baseline, locking the win in."""
    counts = _layout_literal_counts()
    stale = {f: (want, counts.get(f, 0))
             for f, want in KNOWN_LAYOUT_LITERALS.items()
             if counts.get(f, 0) < want}
    assert not stale, (
        "Layout literals went DOWN — lower KNOWN_LAYOUT_LITERALS to lock it "
        "in:\n"
        + "\n".join(f"  {f}: {want} → {now}" for f, (want, now) in stale.items())
    )


# ── A str-enum in a Result: what actually breaks, and what does not ─────────
#
# ``LedStyle`` subclasses ``str``, so a Result field holding one arrives
# IN-PROCESS as the enum while the SAME Result crossing the daemon socket is
# JSON and lands as a plain ``'ax120'``.
#
# Half of the obvious worry is FALSE, and the test says so on purpose: a
# str-enum hashes and compares equal to its own value, so a dict keyed by the
# enum is looked up perfectly well by the bare string.  It was written the
# other way first, asserting a silent miss, and running it disproved that.
#
# What does break is attribute access — ``.name`` on a plain string raises.
# So a UI reading such a field rebuilds the enum from the value.


def test_a_str_enum_result_field_keys_dicts_but_loses_its_attributes() -> None:
    """The precise half that a daemon-mode UI has to care about."""
    import json

    from trcc.core.led_models import LEGACY_STYLE_ID, LedStyle

    style = LedStyle.AX120
    assert isinstance(style, str), "the annotation is honest — it IS a str"

    over_the_wire = json.loads(json.dumps(style))
    assert over_the_wire == "ax120" and type(over_the_wire) is str

    # NOT broken: equality and hashing carry through, so the table still hits.
    assert LEGACY_STYLE_ID.get(over_the_wire) == LEGACY_STYLE_ID[style]

    # Broken: the enum's attributes are gone.
    assert not hasattr(over_the_wire, "name")
    assert LedStyle(over_the_wire).name == "AX120", (
        "rebuilding from the value is what restores them"
    )
