#!/usr/bin/env python3
"""Count functions in ``src/trcc`` that emit no log line.

**Why this is a gate and not a style note.**  Users send us ``trcc report``,
which pastes the log file.  That paste is the whole diagnosis for hardware we
do not own and cannot reproduce on.  A function with no log line is therefore
not "untidy" — it is a bug report we cannot answer, and a round-trip asking
someone to reproduce with a flag.

Exclusions, each for a CAUSE rather than for convenience:

* **abstract methods and stubs** — no body ran, so nothing happened to report.
* **dunders the logger itself calls while formatting** (``__repr__``,
  ``__str__``, ``__eq__``, ``__len__``, …).  Logging inside these recurses:
  the logger formats its arguments, which calls ``__repr__``, which logs,
  which formats.  This is a technical impossibility, not a preference.

Everything else counts.  A property getter counts.  ``__init__`` counts.

    PYTHONPATH=src python3 dev/tools/logging_coverage.py            # summary
    PYTHONPATH=src python3 dev/tools/logging_coverage.py --list     # name them
    PYTHONPATH=src python3 dev/tools/logging_coverage.py --area ui  # one area
"""
from __future__ import annotations

import ast
import collections
import sys
import tempfile
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src" / "trcc"

_LOG_CALLS = frozenset({
    "info", "debug", "warning", "error", "exception", "critical", "log",
})

#: Logging emitters that are module-level FUNCTIONS, not logger methods, and so
#: are called by bare name — invisible to the ``ast.Attribute`` test below.
#:
#: ``core.logs.trace`` is the project's TRACE emitter.  TRACE (level 5) is not in
#: stdlib, so there is no ``logger.trace``; it takes the logger as its first
#: argument and short-circuits on ``isEnabledFor``, which is the whole reason it
#: exists.  Without this set a function whose only log line is a TRACE line
#: counted as SILENT — the ratchet demanding a log line from a function that
#: already had one, and the only fix being to stop using the helper.
#:
#: ``core.logs.recurring_failure`` is the same shape for a per-poll failure: it
#: takes the logger first and emits on EVERY call — a traceback the first time,
#: a per-frame line after (#312).  Three sensor helpers whose only log line it is
#: counted as silent the day they stopped writing a traceback per poll.
#: ``recurring_warning`` is its WARNING twin (2026-09-29): ``ipc._to_wire``
#: runs per frame and warns once per type, then per-frame.
_LOG_FUNCTIONS = frozenset({"trace", "recurring_failure", "recurring_warning"})

#: Expressions that actually hold a ``logging.Logger``.  The METHOD NAME ALONE
#: is not evidence — ``QMessageBox.warning(...)`` opens a modal dialog and
#: argparse's ``parser.error(...)`` exits the process, yet a name-only test
#: counts both as a log line.  That direction is the dangerous one: the ratchet
#: only ever moves DOWN, so a false positive permanently lowers the bar and is
#: never noticed again, because the number only looks better.
#:
#: Measured across ``src/`` on 2026-09-10 — these are the only receivers used
#: with a logging method name, plus exactly one ``QMessageBox.warning`` that a
#: name-only test would have accepted.  ``sink`` is ``app.dispatch``'s
#: ``frame_log if ... else log`` alias; ``log`` on an attribute covers
#: ``self.log = logging.getLogger(f"{__name__}.{key}")``.
_LOGGER_RECEIVERS = frozenset({"log", "frame_log", "logger", "_log", "sink"})

#: Invoked by the logging machinery itself while formatting a record — a log
#: call inside one of these recurses until the stack ends.
_RECURSION_RISK = frozenset({
    "__repr__", "__str__", "__format__", "__eq__", "__hash__",
    "__len__", "__iter__", "__next__", "__contains__", "__bool__",
})

#: Same impossibility, one level up: ``logging`` calls these ON A HANDLER,
#: FORMATTER or FILTER while it is handling a record, so a log call inside one
#: emits a record, which runs them again, forever.  The ratchet would otherwise
#: demand a log line in a function where one hangs the app.
#:
#: Every entry is proven by construction, never argued — see the measurements
#: below and the self-tests in ``tests/test_logging_coverage.py``.  Which class
#: each entry affects is measured BEFORE the name is added; the qualifier that
#: keeps the name from leaking elsewhere is :data:`_FORMATTER_BASES`.
_FORMATTER_HOOKS = frozenset({
    "format", "formatTime", "formatException", "filter",
    # ``logging`` calls these while HANDLING a record, one step before it
    # formats one, so the same impossibility applies.  Measured rather than
    # argued -- entries provoked by a SINGLE emitted record, each method
    # carrying one log line, one trial per process so a crash cannot mask its
    # neighbours (``sys.setrecursionlimit(200)``):
    #
    #     emit             166   flush           427
    #     shouldRollover   142   _open           409
    #     doRollover         5   close             1
    #
    # A first pass read ``_open`` as safe at 1 entry -- an artifact of a trial
    # whose file never rolled over, so ``_open`` ran once at construction and
    # never again.  Forcing real rollovers moved it to 409.
    "emit", "shouldRollover", "_open", "flush",
    # ``doRollover`` is exempt for a SECOND, independent reason: it does not
    # recurse (measured, 5 entries), but it runs under the cross-process
    # rollover lock.  ``flock`` keeps no recursion count -- proven by asking a
    # peer process: take LOCK_EX twice on one fd, release once, and the peer
    # ACQUIRES.  So a log line here re-enters ``emit``, whose ``finally``
    # releases the lock while the rotation is still half-done, letting a peer
    # rename the file being rotated.  Nothing under the lock may log.
    "doRollover",
    # The handler's own lock hooks, called by ``emit`` on both sides of
    # ``super().emit`` — so they are the record path, and they are what takes
    # the lock nothing under may log through.
    "_acquire", "_release",
})

#: Qualified by the ENCLOSING CLASS, because these are ordinary method names:
#: ``format``, ``flush`` and ``_open`` all belong on plenty of classes that
#: have nothing to do with logging, and exempting them by name alone would
#: hide real silent functions.
#:
#: Matched EXACTLY, against the logging base classes themselves.  It used to be
#: a suffix test -- ``b.endswith(("Handler", "Formatter", "Filter"))`` -- whose
#: own comment claimed the enclosing class was the qualifier.  It was not:
#: measured, ``LCDHandler(BaseHandler)`` and ``LEDHandler(BaseHandler)`` match
#: that suffix, and they are the GUI's per-device handlers, not logging ones.
#: Nothing was wrongly exempt at the time (neither declares any hook in this
#: set), so the suffix test was correct by luck.  Widening the set to the
#: record-handling path is what would have spent that luck: ``_open`` and
#: ``flush`` are entirely plausible on a device handler, and either would have
#: gone silently uncounted under a rule written for ``logging``.
_FORMATTER_BASES = frozenset({
    "Handler", "Filter", "Formatter",
    "StreamHandler", "FileHandler", "RotatingFileHandler", "MemoryHandler",
    "logging.Handler", "logging.Filter", "logging.Formatter",
    "logging.StreamHandler", "logging.FileHandler",
    "logging.handlers.RotatingFileHandler",
    "logging.handlers.MemoryHandler",
    # This tree's own logging handlers, so their subclasses qualify too.
    "RenderOnceRotatingFileHandler",
    "_SharedRotatingFileHandler",
})


def _record_path(tree: ast.AST) -> dict[int, ast.FunctionDef | ast.AsyncFunctionDef]:
    """Every function ``logging`` runs while handling a record, by node id.

    Two shapes, one qualifier (:data:`_FORMATTER_BASES`):

    * a hook METHOD on a logging class -- ``class H(StreamHandler): def emit``;
    * a function ATTACHED onto one -- ``logging.StreamHandler.emit = f``.

    The second shape is not hypothetical.  ``__main__._safe_stream_emit`` is
    attached that way on Windows; a rule that only looked inside class bodies
    counted it as silent, a bulk pass (``e078aadd``) gave it a log line, and
    every Windows record then re-entered the handler holding the msvcrt lock:
    ~9 s per record, measured on the VM, shipped v9.10.0 through v9.10.4.
    """
    functions = {n.name: n for n in ast.walk(tree)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    on_path: dict[int, ast.FunctionDef | ast.AsyncFunctionDef] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and any(
                ast.unparse(b) in _FORMATTER_BASES for b in node.bases):
            on_path |= {id(c): c for c in node.body
                        if isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and c.name in _FORMATTER_HOOKS}
        elif (isinstance(node, ast.Assign) and isinstance(node.value, ast.Name)
              and (fn := functions.get(node.value.id)) is not None
              and any(isinstance(t, ast.Attribute)
                      and t.attr in _FORMATTER_HOOKS
                      and ast.unparse(t.value) in _FORMATTER_BASES
                      for t in node.targets)):
            on_path[id(fn)] = fn
    return on_path


def _exempt(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True if the rule does not apply to *fn* at all."""
    return fn.name in _RECURSION_RISK or _is_stub(fn) or _is_abstract(fn)


def _countable(tree: ast.AST):
    """Yield every function the rule applies to."""
    on_path = _record_path(tree)
    for fn in ast.walk(tree):
        if (isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
                and id(fn) not in on_path and not _exempt(fn)):
            yield fn


def _receiver_is_logger(value: ast.expr) -> bool:
    """True if *value* is an expression that holds a ``logging.Logger``.

    ``log`` / ``frame_log`` / ``sink`` (a bare name), ``self.log`` (an
    attribute, from ``self.log = logging.getLogger(...)``), or a direct
    ``logging.getLogger(...)`` call.  Anything else is some other object that
    merely has a method named ``warning`` or ``error``.
    """
    if isinstance(value, ast.Name):
        return value.id in _LOGGER_RECEIVERS
    if isinstance(value, ast.Attribute):        # self.log, cls._log
        return value.attr in _LOGGER_RECEIVERS
    if isinstance(value, ast.Call):             # logging.getLogger(__name__)
        func = value.func
        return ((isinstance(func, ast.Attribute) and func.attr == "getLogger")
                or (isinstance(func, ast.Name) and func.id == "getLogger"))
    return False


def _emits_log(fn: ast.AST) -> bool:
    """True if *fn* really emits a log record.

    Both halves are checked — the method name AND what it is called on — so an
    object that merely shares a logger's method names cannot pass.
    """
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id in _LOG_FUNCTIONS:
            return True
        if (isinstance(func, ast.Attribute) and func.attr in _LOG_CALLS
                and _receiver_is_logger(func.value)):
            return True
    return False


def _is_stub(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True if the body is only a docstring / ``pass`` / ``...``."""
    body = [
        s for s in fn.body
        if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))
    ]
    return not body or all(isinstance(s, ast.Pass) for s in body)


def _is_abstract(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(
        (isinstance(d, ast.Name) and d.id == "abstractmethod")
        or (isinstance(d, ast.Attribute) and d.attr == "abstractmethod")
        for d in fn.decorator_list
    )


def _trees(root: Path | None):
    """Yield ``(relative path, AST)`` for every parseable module under *root*.

    *root* exists so :func:`gate` can point the tool at a fixture — the same
    seam ``class_census.census`` and ``dup_bodies.clusters`` already take.  A
    tool that can only ever read the live tree cannot be proven against a
    known answer.
    """
    base = root or _SRC
    for path in sorted(base.rglob("*.py")):
        try:
            yield path.relative_to(base), ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue


def silent_functions(root: Path | None = None) -> list[str]:
    """Every countable function with no log call, as ``path::name``."""
    return [f"{rel}::{fn.name}" for rel, tree in _trees(root)
            for fn in _countable(tree) if not _emits_log(fn)]


def countable_total(root: Path | None = None) -> int:
    """How many functions the rule applies to at all."""
    return sum(1 for _rel, tree in _trees(root) for _fn in _countable(tree))


def logging_on_the_record_path(root: Path | None = None) -> list[str]:
    """Functions ``logging`` runs while handling a record that log themselves.

    The exemption only stops the ratchet DEMANDING a log line there; this is
    the half that forbids one.  Must be empty: a log call on the record path
    re-enters the handler that is running it — recursion, or on Windows a
    second ``msvcrt.locking`` on a lock this process holds, which retries for
    ~9 s and raises.
    """
    return [f"{rel}::{fn.name}" for rel, tree in _trees(root)
            for fn in _record_path(tree).values() if _emits_log(fn)]


# =========================================================================
# --gate — prove the tool before trusting its number
# =========================================================================

#: Every arm here is a rule this module argues for in prose, plus the two
#: near-misses its own comments record.  A ratchet asserts this tool's OUTPUT;
#: only a gate asserts its CORRECTNESS, and a miscount ratchets forever.
_FIXTURE_A = '''
import abc
import logging

log = logging.getLogger(__name__)


def speaks():
    log.info("something happened")


def silent():
    return 1


def via_self_attr(self):
    self.log.debug("bound logger on an attribute")


def via_direct_call():
    logging.getLogger(__name__).info("inline getLogger")


def looks_like_logging(box):
    """NOT a log call: a QMessageBox also has .warning().

    This exact false positive shipped once and lowered the ratchet
    permanently, because a name-only match counted it as coverage.
    """
    box.warning("parent", "title", "text")


class Port(abc.ABC):
    @abc.abstractmethod
    def contract(self): ...


class Stub:
    def only_pass(self):
        pass

    def only_docstring(self):
        """Nothing ran."""

    def __repr__(self):
        return "<Stub>"


class MyFormatter(logging.Formatter):
    def format(self, record):
        return str(record)


class LCDHandler(BaseHandler):
    """NOT a logging handler — the GUI's per-device handler.

    The exemption used to be a suffix test on the base name, which this
    class matched.  Its own comment claimed the enclosing class qualified
    it; measured, it did not.  ``format`` here must still be counted.
    """

    def format(self, value):
        return str(value)


def _attached_emit(self, record):
    """Attached onto a logging class from outside: on the record path."""
    log.debug("re-enters the handler running it")


logging.StreamHandler.emit = _attached_emit


def _attached_elsewhere(self, frame):
    return frame


Renderer.emit = _attached_elsewhere
'''

_FIXTURE_B = '''
def elsewhere():
    return 1
'''


def gate() -> int:
    """Re-prove the tool against known answers.  Offline and instant."""
    checks: list[tuple[str, bool]] = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "pkg"
        root.mkdir()
        (root / "a.py").write_text(_FIXTURE_A, encoding="utf-8")
        (root / "b.py").write_text(_FIXTURE_B, encoding="utf-8")
        silent = {s.split("::")[-1] for s in silent_functions(root)}
        counted = countable_total(root)
        logging_on_path = logging_on_the_record_path(root)

    checks.append(("a function that logs is NOT silent",
                   "speaks" not in silent))
    checks.append(("a function that does not log IS silent",
                   "silent" in silent))
    checks.append(("self.log.debug counts (attribute receiver)",
                   "via_self_attr" not in silent))
    checks.append(("logging.getLogger(...).info counts (call receiver)",
                   "via_direct_call" not in silent))
    checks.append(("box.warning is NOT a log call (the QMessageBox bug)",
                   "looks_like_logging" in silent))
    checks.append(("@abstractmethod is exempt", "contract" not in silent))
    checks.append(("a pass-only stub is exempt", "only_pass" not in silent))
    checks.append(("a docstring-only stub is exempt",
                   "only_docstring" not in silent))
    checks.append(("__repr__ is exempt (the logger invokes it)",
                   "__repr__" not in silent))
    checks.append(("Formatter.format IS exempt",
                   sum(1 for s in silent if s == "format") == 1))
    checks.append(("a NON-logging class named *Handler is NOT exempt",
                   "format" in silent))
    checks.append(("a function in a SEPARATE module is found",
                   "elsewhere" in silent))
    checks.append(("a function attached onto a NON-logging class is counted",
                   "_attached_elsewhere" in silent))
    checks.append(("a log line on the record path is reported — exactly the "
                   "attached emit, not the silent Formatter.format",
                   logging_on_path == ["a.py::_attached_emit"]))
    # Hand-counted from the rule: speaks, silent, via_self_attr,
    # via_direct_call, looks_like_logging, LCDHandler.format,
    # _attached_elsewhere, elsewhere.  The six exempt are the abstract one,
    # two stubs, __repr__, Formatter.format and _attached_emit.  (A first
    # draft said 8 before _attached_* existed — the gate caught the arithmetic,
    # which is the same service it did for function_census minutes earlier.)
    checks.append(("countable_total excludes the exempt", counted == 8))

    for label, ok in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
    failed = [label for label, ok in checks if not ok]
    if failed:
        print(f"\n{len(failed)} gate check(s) FAILED — do not trust this "
              f"tool's output until they pass.")
        return 1
    print(f"\nAll {len(checks)} gate checks passed.")
    return 0


def main(argv: list[str]) -> int:
    if "--gate" in argv:
        return gate()

    silent = silent_functions()
    total = countable_total()
    area = ""
    if "--area" in argv:
        area = argv[argv.index("--area") + 1]
        silent = [s for s in silent if s.startswith(area)]

    print(f"countable functions : {total}")
    print(f"  with logging      : {total - len(silent_functions())} "
          f"({100 * (total - len(silent_functions())) / total:.0f}%)")
    print(f"  SILENT            : {len(silent_functions())} "
          f"({100 * len(silent_functions()) / total:.0f}%)")

    if "--list" in argv or area:
        print()
        for name in silent:
            print(f"  {name}")
    else:
        print()
        by_area: collections.Counter[str] = collections.Counter(
            s.split("/")[0] for s in silent_functions()
        )
        for a, n in by_area.most_common():
            print(f"    {a:14} {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
