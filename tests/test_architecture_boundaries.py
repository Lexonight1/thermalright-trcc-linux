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
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"

# The UI->UI collectors live with the other instruments, so the tool a human
# runs and the gate CI runs are the same code.  ``tests/test_ui_parity.py``
# reaches for ``ui_contract`` the same way.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dev" / "tools"))

import ui_contract  # noqa: E402  # pyright: ignore[reportMissingImports]

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
# bindings, USB stacks, HARDWARE PROBES.  ``win32*`` (pywin32) is matched by
# prefix below.
#
# ``psutil`` joined 2026-08-31.  It had been absent while ``pynvml`` — the same
# category, a hardware probe — was banned, and that gap is exactly how
# ``ListDisks`` came to ``import psutil`` inside ``core/commands/system.py``:
# the ONLY hardware probe imported anywhere in core or services, invisible to
# the gate that exists to forbid precisely that.  ``Platform.disk_partitions()``
# now carries it, with the shared body on ``BaseOS``.
_BANNED_TOPLEVEL = frozenset({
    "PySide6", "PyQt5", "PyQt6", "shiboken6",
    "wmi", "winreg", "pythoncom", "pywintypes",
    "objc", "Foundation", "IOKit", "Quartz",
    "dbus", "gi", "pynvml", "psutil", "usb", "hid",
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

# CLAUDE.md "Logging": ``configure_logging`` is called exactly once.  A second
# call silently downgrades the user's ``-v``.  The one call site now lives
# inside ``ensure_configured`` (the logging adapter), which no-ops when a
# ``_trcc_handler``-tagged handler is already attached — so the "exactly once"
# rule is enforced by a guard rather than by everyone remembering it.
#
# It used to be the CLI root callback alone, on the premise that every launch
# goes through the CLI.  ``trcc-gui`` and ``trcc-lcd`` do not: they are console
# scripts bound straight to the typer command, so the callback never ran and
# those launches produced NO log file at all.
_CONFIGURE_LOGGING_ALLOWED = frozenset({"trcc/adapters/infra/logging.py"})

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


def test_no_lambdas_in_src() -> None:
    """``feedback_no_lambdas`` (2026-05-14): every callable gets a real symbol.

    A traceback, a debugger and a grep should all name the handler.  Forty
    ``<lambda>`` frames name nothing, and the ones that close over a loop
    variable hide *which* widget fired.

    Zero, not a ratchet.  The count was 45 on 2026-09-20 and went to 0 in one
    pass, so there is no ground to give back — and the rule had existed for
    four months while nothing enforced it, which is how two fresh ones shipped
    in ``a081f037``.

    The replacement shapes, for whoever trips this:

    * plain delegation -> a named method that takes the signal's argument;
    * a value captured per widget -> put it ON the widget with
      ``setProperty`` and read it from ``sender()`` in ONE named slot;
    * an injected callback -> hold it as an attribute and call it from a slot.
    """
    offenders: list[str] = []
    for path in _files_under("trcc"):
        rel = str(path.relative_to(_SRC))
        tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
        offenders += [
            f"  {rel}:{node.lineno}"
            for node in ast.walk(tree) if isinstance(node, ast.Lambda)
        ]
    assert not offenders, (
        "lambda in src/ — give the callable a name so a traceback can print "
        "it (see feedback_no_lambdas):\n" + "\n".join(offenders)
    )


def test_selftest_lambda_detector_sees_one() -> None:
    """The detector must find a lambda it is shown.

    A ban that has only ever returned zero has not been proven to look —
    ``dup_bodies`` records shipping a confident zero from a broken probe, and
    a scratch probe did exactly that again on 2026-09-20.
    """
    tree = ast.parse("handler = lambda v: v + 1\n")
    assert [n for n in ast.walk(tree) if isinstance(n, ast.Lambda)]


# =========================================================================
# The UI -> UI axis.  Collectors live in ``dev/tools/ui_contract.py`` and are
# IMPORTED, never re-derived: a measurement written twice drifts, and this one
# already did — a scratch copy resolved relative imports one package level too
# low and lost a cross-skin reach without saying so.
# =========================================================================

#: All four are the CLI acting as the **router** — ``trcc gui`` is this process
#: starting a DIFFERENT face, which ``ui/_uis.py`` states outright.  Ratcheted
#: rather than exempted, because two of them are not purely routing:
#: ``configure_auth`` / ``set_pairing_code`` are the CLI reaching into the API's
#: auth internals, which is a contract hole wearing a router's coat.
#:
#:   ui/cli/main.py:158    -> qtgui.launch
#:   ui/cli/main.py:217    -> gui.launch
#:   ui/cli/main.py:267    -> api.main.configure_auth, serve, set_pairing_code
#:   ui/cli/system.py:597  -> api.main.build_app
MAX_CROSS_SKIN = 4

#: Modules under ``ui/`` but outside every skin whose only production consumer
#: is ONE skin — they claim to be shared and are not.  Measured 2026-09-20: all
#: nine are in ``ui/presentation`` and all nine serve gui, 1361 of that
#: package's 1756 lines.  Deleting ``ui/gui`` takes this to zero; until then it
#: may not grow.
MAX_SINGLE_SKIN_SHARED = 9


def test_no_skin_reaches_into_another_skin() -> None:
    """A UI may drive the core; it may not drive another UI.

    ``ui_contract``'s original checks see only ``services`` / ``adapters``
    imports, so a UI reaching SIDEWAYS was invisible to every gate in the tree.
    """
    flagged = [e for e in ui_contract.cross_edges() if e.kind == "cross-skin"]
    assert len(flagged) <= MAX_CROSS_SKIN, (
        f"{len(flagged)} skin-to-skin reach(es), over the ceiling of "
        f"{MAX_CROSS_SKIN} — a skin is importing another skin's modules "
        f"instead of dispatching a Command:\n"
        + "\n".join(f"  {e.src} -> {e.dst}  {', '.join(e.names)}  {e.where}"
                    for e in flagged)
    )
    assert len(flagged) >= MAX_CROSS_SKIN, (
        f"only {len(flagged)} skin-to-skin reach(es) left — lower "
        f"MAX_CROSS_SKIN to {len(flagged)} so the ground is not given back"
    )


def test_shared_ui_infrastructure_is_not_named_by_a_skin() -> None:
    """Only the composition root may know which faces exist.

    ``ui/_uis.py`` names every skin's entry point in order to build them, the
    same way ``adapters/device/_base.py`` names its device classes to register
    them.  Any OTHER shared module naming a skin is an inversion: infrastructure
    that has learned who its consumers are cannot be reused by a new one.

    Zero, not a ratchet — measured 2026-09-20, all nine shared-to-skin imports
    in the tree are the composition root, so there is no ground to give back.
    """
    inversions = [e for e in ui_contract.cross_edges() if e.kind == "inversion"]
    assert not inversions, (
        "shared ui/ infrastructure names a skin (only ui/_uis.py may):\n"
        + "\n".join(f"  {e.src} -> {e.dst}  {', '.join(e.names)}  {e.where}"
                    for e in inversions)
    )


def test_shared_ui_modules_really_are_shared() -> None:
    """A module outside every skin, used by exactly one, is mislocated.

    Not a style point: ``ui/presentation`` is the layer plan §4.1 calls homeless
    in the hexagon, and this is the measurement of that drift — 81% of its lines
    serve gui alone while its location advertises otherwise.  Whoever deletes
    ``ui/gui`` needs to know which of these go with it.
    """
    stranded = ui_contract.mislocated()
    assert len(stranded) <= MAX_SINGLE_SKIN_SHARED, (
        f"{len(stranded)} shared-location module(s) serve exactly one skin, "
        f"over the ceiling of {MAX_SINGLE_SKIN_SHARED} — move it into that "
        f"skin, or give it a second consumer:\n"
        + "\n".join(f"  {skin:<6} {lines:>5} lines  {module}"
                    for module, lines, skin in stranded)
    )
    assert len(stranded) >= MAX_SINGLE_SKIN_SHARED, (
        f"only {len(stranded)} stranded module(s) left — lower "
        f"MAX_SINGLE_SKIN_SHARED to {len(stranded)} so the ground is not "
        f"given back"
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
    """Every ``_on_*`` handler in EITHER Qt skin must LOG — a click /
    selection / value change is a user action and must be visible.

    EXEMPT: per-tick handlers (timer-wired via ``timeout.connect`` /
    ``make_timer``, or named ``*_tick``) which are DEBUG/silent by design, and
    debounced live-drag handlers (they re-arm a debounce; the SETTLED value
    logs elsewhere).  Locks in user-interaction click coverage so a new silent
    handler fails CI. (logging coverage)
    """
    import re

    log_methods = {"info", "debug", "warning", "error", "exception", "critical"}
    # BOTH Qt skins.  This read ``trcc/ui/gui`` alone until 2026-09-18, so
    # every ``_on_*`` handler in the newer skin was ungated — and qtgui is
    # where new panels land.  Measured when widened: 0 offenders, so it cost
    # nothing to close and would have cost a silent handler to leave open.
    gui_files = _files_under("trcc/ui/gui") + _files_under("trcc/ui/qtgui")
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
    "trcc/core/commands/device.py": 12,
    # 32 -> 23.  The file was half-migrated in place: 23 calls already went
    # through ``app.themes`` while 32 went around it.  What moved answered a
    # STORAGE question — writing a theme's manifest and grid tile
    # (``write_manifest`` / ``write_preview`` / ``copy_preview``) and choosing
    # which image can stand in as a tile (``tile_path``, which had been a
    # private helper in a Command module despite its own docstring calling
    # itself "single source ... so every UI agrees").  Also gone: pre-resolving
    # a path before ``is_under``, which resolves both sides itself, and
    # ``DeleteTheme`` hand-rolling resolve+resolve+relative_to — the containment
    # rule spelled twice, in the one place where getting it wrong deletes a
    # user's files.
    #
    # The 23 that remain are NOT deferred work, they are the ones a port should
    # not take:
    #   * 4 ``.resolve()`` canonicalising a value, not reading content — the
    #     path persisted to settings, a dedup key, the delete target.
    #   * 3 guards on a path the USER typed at a CLI/API boundary
    #     (``self.path.is_file()``).  Use-case input validation.
    #   * 2 ``output_path.parent.mkdir`` creating the user's chosen export
    #     destination, which is outside any store.
    #   * 4 ``is_dir()`` theme guards.  ``is_theme_dir`` looks like the answer
    #     but is STRICTER — it also requires a marker file — so substituting it
    #     would make exports start failing on marker-less directories.  That may
    #     well be a fix; it is not a refactor, so it is not smuggled in here.
    #   * the rest read a user's own file to hand its bytes to the store, which
    #     is the ingest boundary itself.
    # +2 on 2026-09-08: ``ExportVideoClip`` and ``ProbeVideoDuration`` each
    # ``is_file()`` their source before queueing minutes of ffmpeg — the same
    # existence check ``LoadVideo`` two definitions above already makes, and
    # the alternative is a worker thread failing on it after the caller has
    # returned.
    "trcc/core/commands/theme.py": 25,
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
    "trcc/services/theme_directories.py": 1,
    # +2 on 2026-08-27, and NOT a regression: ``theme_directories`` moved INTO
    # services from ``ui/presentation`` so a core Query could call it, bringing
    # its two ``.exists()`` probes (the #136 portrait fallback, and the
    # same-name variant lookup) into the counted rings.  The mirror image of
    # the ``services/theme.py`` row that LEFT this table when it was re-homed
    # to an adapter: this ratchet counts the inner rings, so what it measures
    # moves when a file does.
    # -1 on 2026-09-23: the same-name variant lookup was
    # ``oriented_theme_reload_target``, a SECOND answer to the question
    # ``oriented_theme_path`` already answers on ``OrientationChanged`` -- and
    # a worse one (user-tree-first, and its callers reloaded with the default
    # ``reset_overrides=True``, persist-clearing the user's overlay edits).
    # Deleted with both its skin callers; the #136 portrait fallback is the
    # one probe left.
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


# ── Every UI must ask the bus ────────────────────────────────────────────────
#
# ``AppProxy.__getattr__`` raises for everything except ``dispatch`` — "daemon
# mode only exposes dispatch(cmd)".  So every read of an App attribute in a UI
# is an AttributeError under ``TRCC_DAEMON=1``, and that — not missing plumbing
# — is what "GUI as a remote daemon client" being pending actually means.
#
# This is a ratchet rather than a ban because burn-down leaks without one: the
# session that first measured these added a NEW reach (``store=self._app.themes``)
# while removing port-passing elsewhere in the same pass.
#
# ── Why the collector has FOUR binding rules ─────────────────────────────────
#
# It used to have one — ``self.<app-attr>.x`` — and reported **13** against a
# real **39**.  Worse, it made ``test_cli_and_api_never_reach_past_dispatch``
# assert an invariant that was FALSE for both surfaces it names, and it left the
# ``KNOWN_UI_ASYMMETRY`` reason excusing ``GetAutostartStatus`` from the API
# ("the headless API server does not manage the user's session autostart")
# unfalsifiable — the API answers that exact question by reaching
# ``platform.autostart()``, and no gate in the suite could see it.
#
# Each rule below was added because the version before it MISSED something real.
# Two intermediate counts (27, then 38) were produced and were both plausible:
#
#   1. ``self.<_app|app|_trcc>.x``  — the original.
#   2. a parameter annotated ``App`` — ``trcc_app.py`` 381/382/383/410/441/483,
#      where ``app`` is ``__init__``'s own parameter.
#   3. ``x = <App factory>(...)``   — recovered ``ui/gui/__init__.py`` ENTIRELY
#      and five of ``qtgui/app.py``'s eight (``app = build_qt_app(platform)``).
#   4. ``x = self._app``            — ``trcc_app.py:1053``'s
#      ``_app_local.cloud_themes`` inside a nested closure.
#
# The factories are ENUMERATED from their return annotations, never a hand-kept
# list of names.  Discriminating by BINDING and not by attribute name is load
# bearing: in ``ui/cli/*.py`` the name ``app`` is a module-level
# ``typer.Typer(...)``, so a name-blocklist (``command`` / ``add_typer`` /
# ``callback``) would silently break the day an App method is called ``command``.
#
# ``dispatch`` is the whole point and is never counted.
_APP_ATTRS = frozenset({"_app", "app", "_trcc"})

#: Functions whose return annotation is ``App`` — the only way a local name gets
#: bound to one.  Gated by ``test_app_factories_still_return_app`` below, so this
#: cannot rot into folklore.
_APP_FACTORIES = frozenset({"trcc", "_build_local_app", "get_app", "build_qt_app",
                            "compose"})

KNOWN_APP_REACHES: dict[str, int] = {
    # ── 2026-08-31: the collector gained rules 2-4 and the number went
    # 13 -> 39.  NOT ground given back — zero new code; 26 pre-existing
    # daemon-unsafe reaches that the one-rule collector could not see.  Each
    # newly-visible file is annotated with who owns it.
    #
    # cli/api: SEVEN sites appeared on 2026-08-31, against a docstring that
    # said "measured at zero".  Six had Commands that already existed and are
    # burned down the same day — autostart x2 -> GetAutostartStatus,
    # devices x2 -> ListDevices, devices x2 -> DeviceState (one shared
    # ``_ctx.resolution_for`` helper: the two CLI blocks were byte-identical
    # for 11 of 12 lines).  ONE remains, and it is not debt:
    # 2026-09-08: cli 0 -> 2, and this pair is NOT the failure mode the row
    # above is.  ``AppProxy.events`` EXISTS as of 0729d7db — a client that
    # holds one dispatches AND observes — so ``display export-video`` follows
    # a daemon-side encode from a terminal that is not doing the encoding.
    # Subscribing is half the bus, not a reach around it.
    "ui/cli/display.py": 2,          # .events x2 — follow an export's progress
    # gui/qtgui lifecycle — deliberately OUT of burn-down.  A GUI running as a
    # daemon *client* must not own app lifecycle, and an event stream over a
    # socket is a different problem from a data read.
    # 2026-09-04: gui 4 -> 2 and qtgui 8 -> 5.  ``App.start_session()`` gave
    # ``close()`` the partner it never had, so the four-call bring-up block
    # that was copy-pasted into run_daemon / run_gui / run_qtgui is ONE call
    # in one place.  Ground gained, not given back.
    # 2026-09-08: ZERO reaches now RAISE under AppProxy — every UI can run as
    # a daemon client without an AttributeError, which is what "the bus is
    # universal in reach" finally means.  The last two are gone:
    #   ui/api/display.py  platform.paths() -> GetPaths.  Proven: POST
    #     /devices/<k>/display/theme answered HTTP 500 in daemon mode and 200
    #     after.  The CodeQL barrier is untouched — the Path still comes from
    #     iterdir(), only the ROOTS moved to the bus.
    #   ui/gui/lcd_handler.py  app.renderer -> qimage_to_raw_rgb24, a
    #     module-level function in the Qt adapter.  Proven by driving the real
    #     screencast toggle: 39 AttributeErrors in ~7 s became 39 frames
    #     dispatched ok, one per 150 ms tick.
    # What REMAINS is events / start_session / discover_and_connect — all
    # implemented on AppProxy, none of them a state read.
    # 2026-09-08: the UI bus.  These two are the WHOLE point of it — the
    # lifecycle that was hand-written in run_daemon / run_gui / run_qtgui / the
    # API now lives once, on ``UserInterface.start``.  They are the same pair
    # already excused in ``ui/gui/__init__.py`` below, centralised: a face that
    # adopts the bus gives up its own copy, so this row exists to let the
    # others go DOWN.  ``compose`` joined ``_APP_FACTORIES`` in the same
    # change — without it the collector could not see ``app.close`` here at
    # all, and would have stopped counting exactly where App lifecycle
    # concentrates.
    # 2026-09-08: gui 2 -> 0 and qtgui 3 -> 1 as both Qt faces adopted the bus.
    # ``ui/gui/__init__.py`` is GONE from this ledger entirely — its
    # start_session/close pair is now the shared template's, and qtgui keeps
    # only ``events`` (an event subscription, not a state read).  This is the
    # trade the row below buys: 2 reaches in ONE reviewable file instead of 5
    # scattered across two.
    "ui/_base.py": 2,                # start_session / close, for every face
    "ui/gui/splash.py": 1,           # discover_and_connect — lifecycle
    # 2026-09-05: 5 -> 4.  The tray's ``minimize_on_close`` comes off
    # ``GetPlatformInfo``, the Query this file already dispatches ten lines
    # further down in ``_show_platform_info``.
    # 4 -> 3 the same day: ``app.first_run.is_first_run()`` picking the opening
    # panel was a STALE bypass, not a gap — ``GetFirstRunStatus`` exists and
    # this very file already dispatched it in ``_show_platform_info``.
    "ui/qtgui/app.py": 1,            # events (subscription, not a state read)
    # 11 -> 9 on 2026-08-30: UCThemeMask stopped being handed a Paths port and
    # a ContentStore.  It composed "which masks does this device have" out of
    # both; ``ListMasks`` had answered that all along for cli/api/qtgui.  What
    # unblocked it was completing the RESULT — ``FileEntry`` gained
    # ``is_custom``, the one field the panel still needed and the Command had
    # been discarding.  A UI reaches past the bus exactly when the Result is
    # short a field.
    # 9 -> 16 on 2026-08-31 by rules 2+4 alone (app.platform x5, app.events,
    # _app_local.cloud_themes) — visibility, not regression.
    # 16 -> 14 the same day: the About panel stopped being handed a Platform
    # port, and ``ensure_autostart`` takes the App and dispatches.  The
    # second of those was INVISIBLE to the old collector (rule 2).
    # 14 -> 13 on 2026-08-31: the LED panel asks ``ListMemorySlots``
    # instead of being handed ``platform.memory_info``.  The DISK half of
    # that same injection deliberately stays: its dropdown is sourced from
    # physical drives while the index it writes addresses nothing, so a
    # Query there would plumb a dead control (increment 4d).
    # 13 -> 8 on 2026-08-31: all five ``.devices`` reaches gone.  The
    # handlers take a device KEY and ask the bus — the swap
    # ``BaseHandler.__init__`` had carried a TODO for ("Phase 5 swaps it
    # for a key string so handlers can dispatch through the App without
    # holding device refs").  What remains is lifecycle + the screencast
    # RawFrame signature question, both deliberately out.
    # 8 -> 7 on 2026-08-31: the LED panel's disk dropdown is sourced from
    # ``ListDiskSensors`` — the THERMAL list the metric actually comes
    # from — so the panel is handed no Platform port at all.
    # 7 -> 6 on 2026-09-05: ``minimize_on_close`` is a field on
    # ``PlatformInfoResult`` now, beside ``no_devices_hint``, which this file
    # already dispatched for.  Platform identity belongs on the Query that IS
    # platform identity, not on a second reach.
    # 6 -> 5 the same day: the window stopped calling ``app.close()``.
    # ``run_gui``'s ``finally`` already did it unconditionally, so teardown ran
    # twice; qtgui had already made this exact split.
    # 5 -> 3: both ``platform.paths()`` reaches come off the SAME
    # ``GetPlatformInfo`` dispatch that already sat three lines above them —
    # ``config_dir`` and ``user_content_dir`` are fields on its Result.
    # ``UiStateStore`` now takes the config DIRECTORY rather than a Paths port,
    # which is all it ever used the port for.
    # 3 -> 2 on 2026-09-07: ``platform.sensors()`` is gone.  The window held a
    # live ``SensorEnumerator`` for one reason — to hand it to UCSystemInfo,
    # which passed it on to the sensor picker — so ONE reach was laundering a
    # port into two widgets.  Both take the App now and dispatch
    # ``GetSensorDashboard`` / ``SetSensorDashboard`` / ``ReadSensors``.
    # 2 -> 1 the same day: the cloud browser's download callback dispatches
    # ``DownloadCloudTheme`` instead of reaching ``cloud_themes.materialise``.
    # A real contract hole, not a stale bypass — ``LoadCloudTheme`` also
    # APPLIES, so substituting it would have started a theme the user has not
    # picked yet.  cli and api could not pre-download at all until now.
    # What is left is ``services.AudioCapture`` (screencast), which belongs
    # with the RawFrame signature question, not here.
    "ui/gui/trcc_app.py": 1,
    # led/_base.py and led_panel.py reached ZERO on 2026-08-31: the six LED
    # tabs take a ``LedSnapshotResult`` instead of a live ``LedDeviceSettings``.
    # Same rule as UCThemeMask before them — the Result was short four fields
    # (segment_on, clock_24h, week_sunday, memory_ratio), and a
    # panel holds a domain object exactly as long as the Result does not
    # answer it.
}

#: The CLI/API reaches, each tagged with the same ``scoped:`` / ``gap:``
#: convention ``KNOWN_UI_ASYMMETRY`` uses — ``scoped:`` is a deliberate
#: decision, ``gap:`` is debt with a named answer.  A ``gap`` is not permission
#: to leave it; it is a promise it is known.
#:
#: Seven appeared the moment the collector could see them, against a test that
#: had asserted zero since it was written.  Six had Commands that already
#: existed and were burned down the same day; this is the seventh.  Per-file
#: COUNTS live in ``KNOWN_APP_REACHES`` above, so the ratchet and its no-slack
#: twin force any future one down; this dict holds the reasons.
CLI_API_REACH_EXCEPTIONS: dict[str, str] = {
    "ui/cli/display.py": (
        "scoped: ``app.events`` is IMPLEMENTED on AppProxy (0729d7db), so "
        "unlike every other row here this one does not raise in daemon mode "
        "— it is how a terminal watches an encode happening inside the "
        "daemon.  Observing is the other half of the bus; the invariant is "
        "'do not read App STATE', and an event subscription is not state"
    ),
}


def _annotation_name(node: ast.expr | None) -> str | None:
    """The bare name of an annotation — ``App``, ``\"App\"``, ``trcc.App``."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value.strip("'\"")
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _is_request_state_stash(node: ast.expr) -> bool:
    """``request.app.state.trcc`` — how every FastAPI route reaches the App."""
    return (
        isinstance(node, ast.Attribute) and node.attr == "trcc"
        and isinstance(node.value, ast.Attribute) and node.value.attr == "state"
        and isinstance(node.value.value, ast.Attribute)
        and node.value.value.attr == "app"
        and isinstance(node.value.value.value, ast.Name)
        and node.value.value.value.id == "request"
    )


def _is_app_factory_call(node: ast.expr) -> bool:
    """A call to one of the ``-> App`` factories."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name):
        return func.id in _APP_FACTORIES
    return isinstance(func, ast.Attribute) and func.attr in _APP_FACTORIES


def _is_self_app(node: ast.expr) -> bool:
    """``self._app`` / ``self.app`` / ``self._trcc``."""
    return (
        isinstance(node, ast.Attribute) and node.attr in _APP_ATTRS
        and isinstance(node.value, ast.Name) and node.value.id == "self"
    )


class _AppReachVisitor(ast.NodeVisitor):
    """Collect reads of an App attribute other than ``dispatch``.

    Tracks, per scope, which local NAMES are bound to the App — by parameter
    annotation, by assignment from an ``-> App`` factory, from the FastAPI
    ``request.app.state.trcc`` stash, or by aliasing ``self._app``.  Nested
    scopes inherit the enclosing binding, which is what catches the closure in
    ``trcc_app.py:1053``.
    """

    def __init__(self) -> None:
        self._scopes: list[set[str]] = [set()]
        self.hits: list[tuple[int, str]] = []

    def _visit_scope(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        args = node.args
        bound = {
            a.arg
            for a in (*args.posonlyargs, *args.args, *args.kwonlyargs)
            if _annotation_name(a.annotation) == "App"
        }
        self._scopes.append(self._scopes[-1] | bound)
        self.generic_visit(node)
        self._scopes.pop()

    visit_FunctionDef = _visit_scope
    visit_AsyncFunctionDef = _visit_scope

    def visit_Assign(self, node: ast.Assign) -> None:
        if (
            len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and (
                _is_request_state_stash(node.value)
                or _is_app_factory_call(node.value)
                or _is_self_app(node.value)
            )
        ):
            self._scopes[-1].add(node.targets[0].id)
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr != "dispatch":
            value = node.value
            if isinstance(value, ast.Name) and value.id in self._scopes[-1]:
                self.hits.append((node.lineno, f"{value.id}.{node.attr}"))
            elif _is_self_app(value):
                self.hits.append((node.lineno, f"self.{value.attr}.{node.attr}"))
            elif _is_request_state_stash(value) or _is_app_factory_call(value):
                self.hits.append((node.lineno, f"<app>.{node.attr}"))
        self.generic_visit(node)


def _app_reaches() -> dict[str, list[tuple[int, str]]]:
    """Every App reach in ``ui/``, by file, as ``(lineno, text)``."""
    found: dict[str, list[tuple[int, str]]] = {}
    for path in (_SRC / "trcc" / "ui").rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        visitor = _AppReachVisitor()
        visitor.visit(ast.parse(path.read_text(encoding="utf-8")))
        if visitor.hits:
            found[path.relative_to(_SRC / "trcc").as_posix()] = visitor.hits
    return found


def _app_reach_counts() -> dict[str, int]:
    return {f: len(hits) for f, hits in _app_reaches().items()}


def test_app_factories_still_return_app() -> None:
    """``_APP_FACTORIES`` must name functions that really are ``-> App``.

    The list is the collector's only non-derived input.  If a factory is
    renamed or its annotation changes, every local name it binds silently
    stops counting — the exact failure rule 3 was added to fix.
    """
    actual = {
        node.name
        for path in (_SRC / "trcc").rglob("*.py")
        if "__pycache__" not in path.parts
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and _annotation_name(node.returns) == "App"
    }
    missing = _APP_FACTORIES - actual
    assert not missing, (
        "_APP_FACTORIES names functions that no longer return App — the "
        f"collector is blind to whatever they bind: {sorted(missing)}"
    )


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


def test_cli_and_api_reach_only_the_recorded_exception() -> None:
    """The two programmatic UIs ask the bus — bar what the record allows.

    This test used to say "They were measured at zero, so there is no baseline
    to burn down and any reach at all is a regression."  **They were never at
    zero.**  It shared a collector that matched ``self.<app-attr>.x`` only,
    while the API reaches through ``request.app.state.trcc`` and the CLI through
    ``app_obj = get_app()`` — so it asserted an invariant it could not test, and
    passed for every day it existed.

    Seven sites appeared the moment the collector could see them.  It is still
    an invariant, not a ratchet — just one with a written record instead of an
    unexamined zero.  Counts are ratcheted by ``KNOWN_APP_REACHES``.
    """
    reaches = _app_reaches()
    dirty = {
        f: hits for f, hits in reaches.items()
        if f.startswith(("ui/cli/", "ui/api/"))
        and f not in CLI_API_REACH_EXCEPTIONS
    }
    assert not dirty, (
        "The CLI/API dispatch Commands and read Results — keep it that way. "
        "Every line below reads App STATE, which the AppProxy a daemon "
        "client holds does not have (``dispatch`` and ``events`` are all it "
        "implements):\n"
        + "\n".join(
            f"  {f}:{line}  {text}"
            for f, hits in sorted(dirty.items()) for line, text in hits
        )
    )


def test_every_cli_api_reason_is_tagged() -> None:
    """``scoped:`` (a decision) or ``gap:`` (debt) — never untagged prose.

    Same rule ``KNOWN_UI_ASYMMETRY`` carries, for the same reason: a reviewer
    must be able to tell a deliberate exception from outstanding work without
    re-deriving the judgement.
    """
    for name, reason in CLI_API_REACH_EXCEPTIONS.items():
        assert reason.startswith(("scoped:", "gap:")), (
            f"{name}: reason must start with 'scoped:' or 'gap:', got {reason!r}"
        )


def test_recorded_cli_api_exceptions_are_real() -> None:
    """A recorded exception must still BE a reach, or the reason is fiction.

    Same failure mode as ``KNOWN_UI_ASYMMETRY``: a decision nobody re-reads
    expires silently.  If the barrier at ``api/display.py`` is ever converted,
    this fails and the entry must go.
    """
    reaches = _app_reaches()
    phantom = sorted(set(CLI_API_REACH_EXCEPTIONS) - set(reaches))
    assert not phantom, (
        "Recorded CLI/API exception no longer reaches past dispatch — delete "
        f"the entry, the win is already made: {phantom}"
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
# The ONE survivor is not a breach: ``trcc.json`` in ``services/settings.py``
# names a file in ``config_dir`` that merely SHARES a string with a theme
# member.  Different files, same name; ``ThemeDir`` would be the wrong owner.
# It is listed at its real count rather than exempted, so if it grows a second
# the gate still notices.
#
# The other survivor is gone, and how it went is worth keeping.  This comment
# used to say there were two and that both were "NOT breaches and never will
# be", naming the second outright: "the legacy app ``config.json`` the debug
# report reads".  That description was exactly right and the conclusion was
# wrong -- ``trcc report`` had no business reading LEGACY's settings file, and
# was printing it to reporters under "## Settings" while the app ran on
# ``trcc.json``.  The gate asked "is this string owned by the right constant?",
# answered "different file, fine", and never asked whether the file was the
# right one to open.  A check inspected the defect, named it in prose, and
# blessed it.  See ``test_diagnostics.py`` for what now gates it.
KNOWN_LAYOUT_LITERALS: dict[str, int] = {
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


# ── A UI may not reach around the bus by IMPORTING an adapter ───────────────
#
# The reach ratchet above counts ``self._app.<attr>``.  It is blind to the other
# way past the bus: importing a concrete adapter and calling it.  That is worse,
# not milder — an attribute read at least goes through the App, while a direct
# import bypasses it entirely and cannot be served by a daemon.  It hid
# ``uc_about`` calling ``detect_installer`` while ``GetPlatformInfo`` already
# carried ``install_method``.
#
# Composition roots are the legitimate exception and CLAUDE.md says so: "the
# composition roots (CLI, GUI, API) wire concrete implementations".  Building
# the App is exactly where a concrete ``QtRenderer`` or ``current_platform``
# belongs.  Everything else in a UI must ask the bus.
#
# Keyed by (path, resolved target) rather than by FILE, because
# ``ui/cli/main.py`` holds BOTH kinds: its root callback legitimately wires
# ``configure_logging`` and ``current_platform``, and its ``api`` command
# reaches ``get_lan_ip`` for a startup banner.  A file-level allowlist would
# wave the second one through forever.

#: Wiring a concrete implementation while building the App.  PERMANENT.
_UI_ADAPTER_COMPOSITION_ROOTS: frozenset[tuple[str, str]] = frozenset({
    ("trcc/ui/api/main.py", "trcc.adapters.render.qt"),
    # 2026-09-08: ``ApiUI.compose`` on the UI bus — the same headless renderer
    # wiring, at the same moment, now expressed once as a face's composition
    # step.  ``api/main.py`` keeps its own because ``build_app(trcc=None)``
    # still composes a default App for callers that pass no App.
    ("trcc/ui/_uis.py", "trcc.adapters.render.qt"),
    ("trcc/ui/qapp.py", "trcc.adapters.render.qt"),
    ("trcc/ui/gui/__init__.py", "trcc.adapters.system"),
    ("trcc/ui/cli/main.py", "trcc.adapters.infra.logging"),
})

#: Real breaches, to burn down.  Delete an entry when its call site moves to the
#: bus; a stale entry FAILS, so a fix cannot leave cruft that re-permits it.
KNOWN_UI_ADAPTER_IMPORTS: frozenset[tuple[str, str]] = frozenset({
    # 2026-09-07: BOTH sys-info entries deleted.  The dashboard layout at
    # ``<config_dir>/system_config.json`` is on the bus now
    # (``GetSensorDashboard`` / ``SetSensorDashboard``), and the App owns the
    # persistence — so cli / api / qtgui can read a file that until then only
    # the desktop GUI could see, and the GUI reaches it the same way they do.
    # A startup banner ("API reachable at http://<ip>:<port>").  Defensible at
    # a launch site, but it is still system information the ``Platform`` port
    # could answer, so it stays visible rather than being called a root.
    ("trcc/ui/cli/main.py", "trcc.adapters.infra.network"),
    # 2026-09-08: ``qimage_to_raw_rgb24`` — the gui's screencast tick holds a
    # QImage and ``SendScreencastFrame`` wants a RawFrame.  ``ui/gui`` IS the
    # Qt adapter family, so reusing the Qt adapter's conversion is not a layer
    # jump; reaching ``app.renderer`` for it WAS, and raised under
    # TRCC_DAEMON=1 (39 AttributeErrors in ~7 s of driven screencast).
    # Deliberately an import rather than a copy: duplicating the scanline
    # stride handling would have scored BETTER here — this audit counts
    # imports, not duplication — and been worse code.
    ("trcc/ui/gui/lcd_handler.py", "trcc.adapters.render.qt"),
    # 2026-09-14 → 2026-09-18: ``trcc_app.py → trcc.adapters.screencast`` sat
    # here for the screencast CAPTURE source, with the right reason -- the
    # screen being captured belongs to the session the WINDOW is in, which
    # under TRCC_DAEMON=1 is not the one that owns USB -- and the wrong
    # mechanism: the window imported the adapter composer itself.  The port
    # for that reason already existed: the gui launcher builds the host
    # Platform, the UI bus holds it, and ``Platform.screen_capture()`` on THAT
    # object is the window's own session.  The window now takes it there, and
    # this row is gone.
})


def _ui_adapter_imports() -> set[tuple[str, str]]:
    """Every ``trcc.adapters`` import made from a UI, as (path, target)."""
    found: set[tuple[str, str]] = set()
    for path in (_SRC / "trcc" / "ui").rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(_SRC).as_posix()
        module = _module_name(path)
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom):
                target = _resolve_import(node, module)
                if target.startswith("trcc.adapters"):
                    found.add((rel, target))
    return found


def test_no_new_ui_adapter_imports() -> None:
    """A UI must not grow a new direct adapter import — ask the bus instead."""
    allowed = _UI_ADAPTER_COMPOSITION_ROOTS | KNOWN_UI_ADAPTER_IMPORTS
    new = _ui_adapter_imports() - allowed
    assert not new, (
        "UI imports an adapter directly, bypassing the Command bus — a "
        "daemon-mode client cannot do this:\n"
        + "\n".join(f"  {p} → {t}" for p, t in sorted(new))
    )


def test_ui_adapter_import_baseline_has_no_slack() -> None:
    """A fixed breach must be removed from the list, locking the win in."""
    stale = KNOWN_UI_ADAPTER_IMPORTS - _ui_adapter_imports()
    assert not stale, (
        "These UI adapter imports are gone — delete them from "
        "KNOWN_UI_ADAPTER_IMPORTS:\n"
        + "\n".join(f"  {p} → {t}" for p, t in sorted(stale))
    )


# =========================================================================
# UI network I/O
#
# The adapter-import audit above cannot see this one.  It counts imports of
# ``trcc.adapters``, so a UI that reimplements HTTP with the STDLIB scores
# CLEANER than one importing the port — the same trap already noted in
# KNOWN_UI_ADAPTER_IMPORTS ("this audit counts imports, not duplication").
#
# That is how two GUI panels kept their own ``urllib`` download code straight
# through the cutover while cli / api / qtgui went to the bus.  Measured
# 2026-09-11, both were DEAD: ``uc_about``'s sat behind an ``app is None``
# fallback production cannot reach, and ``uc_theme_mask``'s behind an
# ``is_local`` flag every tile hardcodes to True.  The second pointed at
# ``czhorde/tr/zt<res>/`` endpoints that appear NOWHERE in the C# 2.1.6 oracle
# (0 occurrences, against 69 for the theme ``/tr/bj`` family) and return 404
# today.  Both are gone; this keeps them gone.
# =========================================================================

#: Modules that open a socket.  A UI asks the bus; an adapter behind a port
#: does the I/O, so one online check covers every face at once.
_NETWORK_ROOTS: frozenset[str] = frozenset({
    "urllib", "http", "socket", "ssl", "ftplib", "telnetlib", "smtplib",
    "requests", "httpx", "aiohttp", "urllib3",
})

#: Real breaches, to burn down.  EMPTY — and it starts empty because the two
#: that existed were deleted rather than grandfathered.  A stale entry FAILS,
#: so this cannot quietly re-permit one.
KNOWN_UI_NETWORK_IMPORTS: frozenset[tuple[str, str]] = frozenset()


def _ui_network_imports() -> set[tuple[str, str]]:
    """Every network-module import made from a UI, as (path, target).

    Both statement forms, because the dead code used one of each: a top-level
    ``from urllib.request import urlopen`` and a function-body ``import
    urllib.request``.  A collector that walked only ``ImportFrom`` — as the
    adapter audit above does — would have missed half of it.
    """
    found: set[tuple[str, str]] = set()
    for path in (_SRC / "trcc" / "ui").rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(_SRC).as_posix()
        module = _module_name(path)
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom):
                targets = [_resolve_import(node, module)]
            elif isinstance(node, ast.Import):
                targets = [alias.name for alias in node.names]
            else:
                continue
            for target in targets:
                if target.split(".", 1)[0] in _NETWORK_ROOTS:
                    found.add((rel, target))
    return found


def test_no_new_ui_network_imports() -> None:
    """A UI must not do its own network I/O — ask the bus instead.

    Not style: a capability a UI implements itself exists for that ONE face.
    The cloud-mask download lived in the desktop GUI and therefore in no other
    UI, and — because it never went through a port — no injected fake could
    reach it and no test ever ran it.
    """
    new = _ui_network_imports() - KNOWN_UI_NETWORK_IMPORTS
    assert not new, (
        "UI does its own network I/O, bypassing the Command bus — a "
        "daemon-mode client cannot do this, and no injected fake can test "
        "it:\n"
        + "\n".join(f"  {p} → {t}" for p, t in sorted(new))
    )


def test_ui_network_baseline_has_no_slack() -> None:
    """A fixed breach must leave the list, locking the win in."""
    stale = KNOWN_UI_NETWORK_IMPORTS - _ui_network_imports()
    assert not stale, (
        "These UI network imports are gone — delete them from "
        "KNOWN_UI_NETWORK_IMPORTS:\n"
        + "\n".join(f"  {p} → {t}" for p, t in sorted(stale))
    )


def test_selftest_ui_network_collector_catches_both_import_forms() -> None:
    """The collector must have teeth for ``import x`` AND ``from x import y``.

    Pinned because the audit it sits beside walks only ``ImportFrom``, and the
    dead code this gate replaces used the other form inside a function body.
    """
    source = (
        "from urllib.request import urlopen\n"
        "def f():\n"
        "    import http.client\n"
        "    return urlopen, http.client\n"
    )
    targets = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom):
            found = [node.module or ""]
        elif isinstance(node, ast.Import):
            found = [a.name for a in node.names]
        else:
            continue
        targets.update(t for t in found if t.split(".", 1)[0] in _NETWORK_ROOTS)
    assert targets == {"urllib.request", "http.client"}


def test_composition_root_exemptions_all_still_exist() -> None:
    """An exemption for an import that no longer exists is a hole.

    Same rule as the quarantine list: a stale permanent exemption would
    silently re-permit that (path, target) pair if the file ever imported it
    again for a different, illegitimate reason.
    """
    stale = _UI_ADAPTER_COMPOSITION_ROOTS - _ui_adapter_imports()
    assert not stale, (
        "Composition-root exemptions that match no real import — remove "
        "them:\n" + "\n".join(f"  {p} → {t}" for p, t in sorted(stale))
    )


# ── Every capability belongs to every UI → tests/test_ui_parity.py ─────────
#
# ``KNOWN_SINGLE_CLIENT_COMMANDS`` and its tests lived here from 2026-08-28 to
# 2026-08-30.  They asked "which Commands does only one UI reach?", which is UI
# PARITY and not a layer boundary — and ``test_ui_parity`` had been asking a
# narrower version of the same question, with written reasons, since
# 2026-07-12.  Two records of one rule drift, and these had: they shared 4 of 21
# names, and one reason had gone false without anything failing.
#
# They are now ONE record — ``KNOWN_UI_ASYMMETRY`` — which stores the UI reach
# per Command, derives single-client / CLI-only / API-only from it, and asserts
# the recorded reach against reality so a reason cannot expire in silence.
#
# This file keeps what it is named for: layer imports, OS sniffing, filesystem
# I/O, gui reaches past dispatch, and Gate A (a UI importing an adapter, which
# IS an import boundary).


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


# ── dev/ must not do the app's job ──────────────────────────────────────────
#
# A dev tool exists to fake a device and read back what the app did with it.
# Handler lifecycle — building one, dropping one, choosing which device is on
# screen — is the app's own job, and three tools had taken it over by poking
# ``TRCCApp``'s private bookkeeping directly.  When ``_add_handler`` changed
# shape to take a ``DeviceState`` Result instead of a live ``Device``
# (``0c3df980``, 2026-08-31) all three broke at once, for EVERY wire, and
# stayed broken for 84 commits: the mock GUI's variant panel presented nothing,
# and ``audit_present`` reported 132 of 132 variants as PRODUCT failures when
# the failure was its own.  4793 tests saw none of it, because none of them
# drive ``dev/``.
#
# So the gate is not "does dev/ still work" — it is the invariant that made it
# rot: a dev tool may INSPECT the window (reading ``_handlers`` to assert what
# the app built is the whole point of an audit), but it may not DRIVE handler
# lifecycle.  Swapping the faked device is Commands on the real bus; putting a
# device on screen is ``uc_device.device_selected``, the same public signal a
# sidebar click emits.  Both survive any refactor of the bookkeeping below.
_APP_JOB_CALLS = frozenset({"_add_handler", "_remove_handler", "_activate_device"})
_APP_JOB_ASSIGNS = frozenset({"_active_key"})

_DEV = Path(__file__).resolve().parents[1] / "dev"

#: dev-file → count of app-job reaches.  Burned to empty on 2026-09-10; a new
#: entry here means a tool took the app's job back.
KNOWN_DEV_APP_JOB_REACHES: dict[str, int] = {}


class _AppJobVisitor(ast.NodeVisitor):
    """Collect every site where a ``dev/`` file drives handler lifecycle."""

    def __init__(self) -> None:
        self.hits: list[tuple[int, str]] = []

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in _APP_JOB_CALLS:
            self.hits.append((node.lineno, f"{func.attr}()"))
        self.generic_visit(node)

    def _visit_assign(self, node: ast.Assign | ast.AugAssign) -> None:
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            if isinstance(target, ast.Attribute) and target.attr in _APP_JOB_ASSIGNS:
                self.hits.append((node.lineno, f"{target.attr} ="))
        self.generic_visit(node)

    visit_Assign = _visit_assign
    visit_AugAssign = _visit_assign


def _dev_app_job_reaches() -> dict[str, list[tuple[int, str]]]:
    """Every app-job reach under ``dev/``, by file, as ``(lineno, text)``."""
    found: dict[str, list[tuple[int, str]]] = {}
    for path in _DEV.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        visitor = _AppJobVisitor()
        visitor.visit(ast.parse(path.read_text(encoding="utf-8")))
        if visitor.hits:
            found[path.relative_to(_DEV).as_posix()] = visitor.hits
    return found


def test_no_dev_tool_does_the_apps_job() -> None:
    """A dev tool fakes a device and reads back — it must not drive lifecycle."""
    counts = {f: len(h) for f, h in _dev_app_job_reaches().items()}
    risen = {f: (KNOWN_DEV_APP_JOB_REACHES.get(f, 0), n) for f, n in counts.items()
             if n > KNOWN_DEV_APP_JOB_REACHES.get(f, 0)}
    assert not risen, (
        "A dev tool is doing the app's job again — this is what broke three "
        "tools silently for 84 commits and made audit_present report its own "
        "breakage as 132 product bugs.  Swap the device with Commands "
        "(summon_variant) and select it with uc_device.device_selected "
        "(select_device) instead:\n"
        + "\n".join(f"  {f}: {was} → {now}" for f, (was, now) in risen.items())
    )


def test_dev_app_job_baseline_has_no_slack() -> None:
    """A tool that gives the job back must lower the baseline, so it stays given."""
    counts = {f: len(h) for f, h in _dev_app_job_reaches().items()}
    stale = {f: (want, counts.get(f, 0))
             for f, want in KNOWN_DEV_APP_JOB_REACHES.items()
             if counts.get(f, 0) < want}
    assert not stale, (
        "dev/ app-job reaches went DOWN — lower KNOWN_DEV_APP_JOB_REACHES to "
        "lock the win in:\n"
        + "\n".join(f"  {f}: {want} → {now}" for f, (want, now) in stale.items())
    )


def test_selftest_dev_app_job_collector_has_teeth() -> None:
    """Break the rule four ways on purpose; the collector must see all four.

    Without this the gate could be green because it detects nothing — which is
    exactly how the original breakage survived a 4793-test suite.
    """
    banned = (
        "window._add_handler(state)",
        "window._remove_handler(key)",
        "window._activate_device(key)",
        "window._active_key = ''",
    )
    for src in banned:
        visitor = _AppJobVisitor()
        visitor.visit(ast.parse(src))
        assert visitor.hits, f"collector is blind to: {src}"

    # Inspection and the sanctioned path must NOT be flagged, or the gate would
    # push tools away from the very shape it is steering them toward.
    allowed = (
        "handler = window._handlers.get(key)",
        "was = window._active_key",
        "result = summon_variant(app, vid, pid, pm=pm, sub=sub, fbl=fbl)",
        "window.uc_device.device_selected.emit({'path': key})",
    )
    for src in allowed:
        visitor = _AppJobVisitor()
        visitor.visit(ast.parse(src))
        assert not visitor.hits, f"collector wrongly flags inspection: {src}"


# ── Home resolution belongs to the system adapters, nowhere else ────────────
#
# ``adapters/system/`` IS the layer that turns "this OS" into concrete
# directories -- it implements the ``Paths`` port, so ``Path.home()`` there is
# the job.  Everywhere else it is a bypass: it hardcodes Linux's layout
# (``config_dir()`` is ``%APPDATA%\trcc`` on Windows and
# ``Application Support`` on macOS), and, worse, it needs no injection, so
# nothing distinguishes the real app from a unit test.
#
# Both halves were live.  ``adapters/device/led.py`` built its probe cache at
# ``Path.home() / ".trcc" / "led_probe_cache.json"``, and MEASURED on
# 2026-09-10, running five LED test files wrote a fake ``pm=208 MAGIC_QUBE``
# entry into the developer's OWN cache -- the file a second launch trusts
# INSTEAD of a handshake, because the LED firmware answers only once per power
# cycle.  A test run could redefine a real device's identity.
# ``adapters/infra/sysinfo_config.py`` had the same default one ``load()``
# away from renaming a file in the user's config dir.
#
# The devices now receive a resolved directory (``Device.set_state_dir``) and
# ``SysInfoConfig`` requires its path, so both bypasses are unrepresentable
# rather than merely unused.  The survivors are listed at their real counts:
#
#   * ``ipc.py``          -- the single-instance lock under ``~/.cache``, a
#                            runtime path that is not app state at all.
#   * ``infra/logging.py`` -- ``LAST_RESORT_LOG``, used when the Paths port
#                            itself could not be built.  Deliberate.
#   * ``ui/cli/shell.py``  -- the XDG state fallback for shell history.
KNOWN_HOME_CALLS: dict[str, int] = {
    "trcc/ipc.py": 2,
    "trcc/adapters/infra/logging.py": 1,
    "trcc/ui/cli/shell.py": 1,
}


def _home_call_counts() -> dict[str, int]:
    """Per-file ``Path.home()`` calls outside the system adapters."""
    counts: dict[str, int] = {}
    for path in sorted((_SRC / "trcc").rglob("*.py")):
        rel = str(path.relative_to(_SRC))
        if "adapters/system/" in rel:      # the layer that owns home resolution
            continue
        n = sum(1 for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "home"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "Path")
        if n:
            counts[rel] = n
    return counts


def test_no_new_path_home_outside_the_system_adapters() -> None:
    """New code asks the ``Paths`` port; it does not guess at ``~``."""
    counts = _home_call_counts()
    risen = {f: (KNOWN_HOME_CALLS.get(f, 0), n) for f, n in counts.items()
             if n > KNOWN_HOME_CALLS.get(f, 0)}
    assert not risen, (
        "New Path.home() outside adapters/system/ — take the directory from "
        "the Paths port (a device receives one via set_state_dir):\n"
        + "\n".join(f"  {f}: {was} → {now}" for f, (was, now) in risen.items())
    )


def test_path_home_baseline_has_no_slack() -> None:
    """Removing a bypass must lower the baseline, locking the win in."""
    counts = _home_call_counts()
    stale = {f: (want, counts.get(f, 0))
             for f, want in KNOWN_HOME_CALLS.items()
             if counts.get(f, 0) < want}
    assert not stale, (
        "Path.home() calls went DOWN — lower KNOWN_HOME_CALLS to lock it in:\n"
        + "\n".join(f"  {f}: {want} → {now}" for f, (want, now) in stale.items())
    )


# ── One way to run a Command: the bus ───────────────────────────────────────
#
# `App.dispatch` is the single logging chokepoint -- entry with the command's
# full repr, outcome, and a WARNING when a Result is not ok.  A Command that
# calls `Other(...).execute(app)` directly skips all three, so an inner command
# that fails leaves NOTHING in the log.  22 sites did that; they now dispatch.
#
# The 12 survivors are the ones whose caller already logs the inner outcome
# itself (`SleepDevice` failing is a benign "blank skipped" at DEBUG, and
# routing it through the bus would turn that into a WARNING in every report).
# Listed at their real count so a thirteenth fails.
#
# -1 on 2026-09-23: the thirteenth was `OrientedThemeTarget`, which called
# `ResolveThemeDirectories(...).execute(app)` to answer a question the core
# already answers on `OrientationChanged`.  Both its callers were Qt skins
# re-deciding a rotation the App had just decided, so the Query went with them.
KNOWN_DIRECT_EXECUTE: dict[str, int] = {
    "trcc/core/commands/device.py": 5,
    "trcc/core/commands/theme.py": 7,
}


def _direct_execute_counts() -> dict[str, int]:
    """Per-file `X(...).execute(app)` calls -- i.e. around the bus."""
    counts: dict[str, int] = {}
    for path in sorted((_SRC / "trcc").rglob("*.py")):
        rel = str(path.relative_to(_SRC))
        if rel == "trcc/app.py":          # dispatch itself calls cmd.execute
            continue
        n = sum(1 for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "execute"
                and len(node.args) == 1
                and isinstance(node.args[0], ast.Name)
                and node.args[0].id == "app")
        if n:
            counts[rel] = n
    return counts


def test_no_new_command_runs_around_the_bus() -> None:
    """A new Command-calls-Command must go through `app.dispatch`."""
    counts = _direct_execute_counts()
    risen = {f: (KNOWN_DIRECT_EXECUTE.get(f, 0), n) for f, n in counts.items()
             if n > KNOWN_DIRECT_EXECUTE.get(f, 0)}
    assert not risen, (
        "New `X(...).execute(app)` — use `app.dispatch(X(...))` so the run is "
        "logged; skip the bus only when the caller logs the outcome itself:\n"
        + "\n".join(f"  {f}: {was} → {now}" for f, (was, now) in risen.items())
    )


def test_direct_execute_baseline_has_no_slack() -> None:
    """Moving one onto the bus must lower the baseline."""
    counts = _direct_execute_counts()
    stale = {f: (want, counts.get(f, 0))
             for f, want in KNOWN_DIRECT_EXECUTE.items()
             if counts.get(f, 0) < want}
    assert not stale, (
        "Direct execute calls went DOWN — lower KNOWN_DIRECT_EXECUTE:\n"
        + "\n".join(f"  {f}: {want} → {now}" for f, (want, now) in stale.items())
    )


# ── #166: linux.py must IMPORT on Windows ──────────────────────────────────


def test_linux_platform_module_has_no_linux_only_toplevel_imports() -> None:
    """`trcc.adapters.system.linux` is imported on every OS, so its
    Linux-only stdlib must stay inside functions (#166).

    v9.7.0 crashed on Windows because this module imported `fcntl` at module
    scope: `PLATFORMS` populates by side-effect import, so loading the registry
    loaded every OS module, and one `import fcntl` took the whole app down on
    a machine that has no such module.  The fix (two lazy imports) has been in
    the tree UNGATED — `#166` appears in `src/` and in no test.
    """
    import ast

    linux_only = {"fcntl", "termios", "pwd", "grp"}
    src = (_SRC / "trcc" / "adapters" / "system" / "linux.py").read_text()
    tree = ast.parse(src)
    offenders: list[str] = []
    for node in tree.body:                     # TOP LEVEL only — nested is fine
        if isinstance(node, ast.Import):
            offenders += [a.name for a in node.names if a.name in linux_only]
        elif isinstance(node, ast.ImportFrom) and node.module in linux_only:
            offenders.append(node.module)
    assert not offenders, (
        f"linux.py imports {offenders} at module scope; the registry imports "
        "this module on Windows too, so it must import there (#166).  Move it "
        "inside the function that needs it.")


# =========================================================================
# The OBSERVE half — the driving port nobody was counting
# =========================================================================
#
# Everything above, and every parity number this project has quoted, measures
# ``dispatch(cmd) -> Result``: 144 Commands and Queries.  That is HALF the
# driving surface.  The other half is the EventBus — 39 Event types, push — and
# it is equally universal, crossing the daemon boundary through
# ``AppProxy.events``.  Nothing counted it, so a UI that dispatched everything
# and observed nothing scored as complete.

#: Events ``ui/gui`` observes and ``ui/qtgui`` does not.  **A real parity gap**,
#: and invisible to the Command count: measured 2026-09-20 both skins observe
#: exactly 15 of 39, which reads as parity and is not — they share only 11.
#:
#: Ratcheted as a SET, not a count, because the count is the thing that lied.
MISSING_IN_QTGUI = {
    "BrightnessChanged",
    "ScreencastStarted",
    "ScreencastStopped",
    "SystemSuspending",
}

#: Event types ``BusBridge`` never forwards, so no Qt widget can observe them
#: however much it wants to.  A missing WIRE, distinct from a capability that is
#: offered and declined.  May not grow.
MAX_UNBRIDGED_EVENTS = 17


def test_qtgui_observes_what_gui_observes() -> None:
    """Retiring ``ui/gui`` means qtgui must hear everything gui hears.

    An Event is reached two ways and BOTH count: named outright (qtgui's
    shape — imported, or a typed handler) or via its ``BusBridge`` Qt signal
    (gui's shape, where the type name appears nowhere). Counting names alone
    scores gui 3 of 39 instead of 15.
    """
    reach = ui_contract.event_reach()
    gui = {n for n, uis in reach.items() if "gui" in uis}
    qtgui = {n for n, uis in reach.items() if "qtgui" in uis}
    gap = gui - qtgui
    assert gap <= MISSING_IN_QTGUI, (
        f"qtgui stopped observing {sorted(gap - MISSING_IN_QTGUI)} that gui "
        f"still does — a new parity gap on the half the Command count cannot "
        f"see"
    )
    assert gap == MISSING_IN_QTGUI, (
        f"qtgui now observes {sorted(MISSING_IN_QTGUI - gap)} — remove it from "
        f"MISSING_IN_QTGUI so the ground is not given back"
    )


def test_unbridged_events_do_not_grow() -> None:
    """An Event no ``BusBridge`` signal carries is unreachable from any widget.

    Not the same finding as "bridged and nobody connects": the first is a
    missing wire, the second is a choice. The tool splits them for that reason.
    """
    unbridged, _declined = ui_contract.unheard_split(ui_contract.event_reach())
    assert len(unbridged) <= MAX_UNBRIDGED_EVENTS, (
        f"{len(unbridged)} Event type(s) reach no Qt skin, over the ceiling of "
        f"{MAX_UNBRIDGED_EVENTS} — a new event was published with no bridge "
        f"signal, so no widget can ever see it:\n  " + "\n  ".join(unbridged)
    )
    assert len(unbridged) >= MAX_UNBRIDGED_EVENTS, (
        f"only {len(unbridged)} unbridged — lower MAX_UNBRIDGED_EVENTS to "
        f"{len(unbridged)}"
    )
