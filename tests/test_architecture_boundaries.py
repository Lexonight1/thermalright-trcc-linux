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
KNOWN_IMPORT_BREACHES: frozenset[tuple[str, str]] = frozenset({
    # services type concrete adapters — to be closed by core port ABCs (step 5).
    ("trcc/services/data_install.py", "trcc.adapters.repo.data_install"),
    ("trcc/services/cloud_theme.py", "trcc.adapters.theme.cloud"),
})

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


# ── Gate self-tests (the watchmen) ───────────────────────────────────────────
# A boundary gate with a logic bug that silently stops detecting is worse than
# no gate — everything goes green and breaches slip through unseen.  These feed
# the scanners synthetic known-bad / known-good fixtures and assert the engine
# still has teeth.  If detection ever regresses to "never flags", these fail.

def _imports_in(source: str, module: str = "trcc.core.commands.system") -> list[str]:
    collector = _ImportCollector(module)
    collector.visit(ast.parse(source))
    return [tgt for _ln, tgt in collector.found]


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
    assert not _is_forbidden_target("trcc.services.theme")


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
