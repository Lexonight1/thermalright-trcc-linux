"""Logging coverage may only improve — the ratchet.

**Why this is enforced rather than remembered.**  Users diagnose through
``trcc report``, which pastes the log file.  For hardware we do not own that
paste IS the diagnosis, so a function with no log line is a bug report we
cannot answer.  The rule is therefore: *every function, new or old, gets a log
line* — and a rule nobody can fail is a rule that rots.

It cannot start green: 1451 of 3074 countable functions were silent when this
landed.  Failing on all of them would put CI permanently red, which is how a
gate gets ignored.  So it is a **ratchet**:

* add a silent function -> the count rises -> **fail**
* give a silent function a log line -> the count falls -> **fail**, asking you
  to lower :data:`MAX_SILENT` so the ground you gained cannot be lost

The number lives here, in the test, so every diff that moves it shows the
direction of travel.  It should only ever go down.

Exclusions are in ``dev/tools/logging_coverage.py`` and each has a cause:
abstract methods and stubs never ran, and dunders the logger invokes while
formatting a record (``__repr__``, ``__len__``, …) would recurse forever.
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "dev" / "tools"))

import logging_coverage  # noqa: E402  # pyright: ignore[reportMissingImports]

#: Silent functions as of 2026-08-21 (landed at 1451; -5 by deleting six dead
#: functions, -7 by promoting eleven ``Platform`` members to ``@abstractmethod``
#: — an abstract method has no body, so it stops being countable, and the seven
#: silent ones among them stopped being excuses).  LOWER THIS as coverage
#: improves; never raise it.  Worst areas now:
#: ui 681, adapters 420, services 122, core 101.
#: 1344 -> 1343 on 2026-09-01: retiring ``SetDiskIndex`` deleted its silent
#: ``execute``.  A removal lowers this exactly as a fix does — the ratchet
#: asserts BOTH directions, so ground given back is a failure either way.
#: 1343 -> 1342 on 2026-09-02: ``DisconnectDevice.execute`` got the entry log
#: THE RULE requires, in the pass that gave it the shutdown blank.  It was the
#: one device-release path that reported nothing about why it did what it did.
#: 1342 -> 1341 the same day: ``ResetDevice.execute`` likewise, when it stopped
#: being a copy of DisconnectDevice and became a real disconnect-reconnect-
#: restore cycle whose three steps each need to be readable in a report.
#: 1341 -> 1340 on 2026-09-02: ``ListLedStyles.execute`` reported nothing while
#: it was telling users an 8-of-12 wrong answer, so the line names the zone
#: counts it resolved rather than that it ran.
#: 1340 -> 1339 the same day: ``TRCCApp._create_i18n_overlays`` builds the whole
#: About pane and the language picker and said nothing, so a report could not
#: show which language the labels were rendered in.
#: 1334 -> 1327 on 2026-09-08 with the video-export port.  Five came from
#: DELETING silent code rather than logging it: gui's hand-rolled
#: ``ExportWorker`` (its ``run``/``_do_export`` said nothing about an ffmpeg
#: failure) and qtgui's ``_ExportThread`` both went, replaced by one runner
#: whose every branch logs.  A duplicate implementation is silent twice.
#: The other two are the runner's own helpers, logged as they were written.
#: 1327 -> 1326 on 2026-09-08 with the video-export port (see above).
#: 1326 -> 1325 on 2026-09-09: the record-handling path joined the exemption
#: list, which removed ``__main__._SafeRotatingFileHandler.doRollover`` from
#: the countable set.  Nothing gained a log line — a function the rule could
#: never have been satisfied in stopped being counted, exactly as the
#: ``ClassContextFilter.filter`` entry did before it.  The ratchet asserts BOTH
#: directions, so this must come down with it.
#: 1325 -> 1324 the same day: ``_entry.main`` gained the entry log THE RULE
#: asks for, in the pass that gave the console script the startup-crash
#: buffering ``python -m trcc`` already had.  It was the one dispatch every
#: packaged install goes through, and it said nothing.
#: 1321 -> 1306 on 2026-09-12: the gui and qtgui region-select overlays were
#: the same drag interaction written twice, both of them silent.  Collapsing
#: them onto ``DragSelectOverlay`` deleted one copy outright and the surviving
#: one was written with the log lines THE RULE asks for.
#: 1306 -> 1305 the same day: ``ScreenCastPanel._get_aspect_ratio`` was a
#: silent table lookup.  Deriving the ratio from the panel geometry the bus
#: already hands it gave it the branch logs THE RULE asks for — it now says
#: when no device has been seen, and warns when one reports a 0x0 panel.
#: 1305 -> 1262 the same day: the sensor ports stopped DEMANDING every quantity
#: from every backend.  43 method bodies across 28 backends existed only to
#: write ``return None`` and say "I cannot" — countable, silent, and carrying
#: no information a reporter could use.  The port now answers None by default
#: and a backend that has no such sensor simply does not override it, so all 43
#: are gone.  They are not replaced by 43 silent base methods: the defaults are
#: docstring-only, which the stub rule above correctly excludes — no body ran,
#: so nothing happened to report.
#: 1306 -> 1305 the same day: ``ScreenCastPanel._get_aspect_ratio`` was a
#: silent table lookup; deriving the ratio from the panel geometry gave it the
#: branch log THE RULE asks for (it now says when there is no device yet).
#: 342 -> 341 on 2026-09-20: ``UCAbout.eventFilter`` stopped being silent.
#: The pass that fixed the xdist worker segfault replaced the self-installed
#: filter with a weak-referencing ``_ToolTipFilter``, and the new body says
#: which widget it is decorating and when its owner has already gone rather
#: than dropping the event without a word (``71ec930b``).  Measured by
#: diffing ``logging_coverage.py --list`` across the commit: it is the ONLY
#: name that left the silent set.
_SRC_ROOT = Path(__file__).resolve().parents[1] / "src" / "trcc"

MAX_SILENT = 340


def test_logging_coverage_only_improves() -> None:
    silent = logging_coverage.silent_functions()
    total = logging_coverage.countable_total()
    actual = len(silent)

    assert actual <= MAX_SILENT, (
        f"{actual - MAX_SILENT} new function(s) with no logging "
        f"({actual} silent of {total}).\n"
        f"Every function gets a log line — users diagnose through "
        f"`trcc report`, which pastes the log; a silent function is a bug "
        f"report we cannot answer.\n"
        f"List them:  PYTHONPATH=src python3 dev/tools/logging_coverage.py --list"
    )

    assert actual >= MAX_SILENT, (
        f"Logging coverage improved — {MAX_SILENT - actual} function(s) gained "
        f"a log line ({actual} silent of {total}).\n"
        f"Lower MAX_SILENT to {actual} in tests/test_logging_coverage.py so the "
        f"improvement cannot be lost."
    )


def test_recursion_risk_dunders_are_excluded_for_a_reason() -> None:
    """The exclusion list is a technical constraint, not a convenience.

    A log call inside ``__repr__`` recurses: the logger formats its arguments,
    which calls ``__repr__``, which logs.  Pinned so nobody 'tidies' the list
    into something arbitrary.
    """
    assert "__repr__" in logging_coverage._RECURSION_RISK
    assert "__len__" in logging_coverage._RECURSION_RISK
    # Things that are NOT recursion risks must not hide in there.
    for name in ("__init__", "__enter__", "__exit__", "__call__",
                 "__getitem__", "__init_subclass__"):
        assert name not in logging_coverage._RECURSION_RISK, (
            f"{name} is not invoked by log formatting — it must be counted"
        )


# =========================================================================
# The record-handling path — exempt because a log line there hangs the app
# =========================================================================


def _countable_names(src: str) -> set[str]:
    """Names the ratchet would demand a log line in, for a snippet."""
    import ast
    return {fn.name for fn in logging_coverage._countable(ast.parse(src))}


_A_LOGGING_HANDLER = '''
class SharedLogHandler(RenderOnceRotatingFileHandler):
    def __init__(self, filename, **kw):
        self._fd = os.open(str(filename) + ".lock", os.O_CREAT)
        super().__init__(filename, **kw)
    def emit(self, record):
        self._acquire(); super().emit(record)
    def shouldRollover(self, record):
        return os.stat(self.baseFilename).st_size > self.maxBytes
    def _open(self):
        stream = super()._open(); self._ino = 1; return stream
    def flush(self):
        super().flush()
    def doRollover(self):
        super().doRollover()
    def close(self):
        os.close(self._fd); super().close()
'''

#: The GUI's per-device handlers.  Their base name ENDS IN "Handler" but they
#: are not logging handlers, and the exemption must not reach them.
_A_GUI_DEVICE_HANDLER = '''
class LCDHandler(BaseHandler):
    def emit(self, frame):
        self._device.send(frame)
    def flush(self):
        self._queue.clear()
    def _open(self):
        self._device.connect()
    def format(self, theme):
        return theme.name.upper()
'''


def test_the_record_handling_path_is_exempt_on_a_logging_handler() -> None:
    """A log line in any of these recurses until the stack ends.

    Measured, entries provoked by ONE emitted record: emit 166, shouldRollover
    142, _open 409, flush 427.

    ``doRollover`` is exempt on a second, independent ground: it does NOT
    recurse (5 entries), but it runs under the cross-process rollover lock, and
    ``flock`` keeps no recursion count — take LOCK_EX twice on one fd, release
    once, and a peer process acquires.  A log line there re-enters ``emit``,
    whose ``finally`` drops the lock mid-rotation.

    ``close`` (1 entry) is off both paths, so THE RULE still applies to it.
    """
    countable = _countable_names(_A_LOGGING_HANDLER)

    for name in ("emit", "shouldRollover", "_open", "flush", "doRollover"):
        assert name not in countable, (
            f"{name} runs on the record-handling path or under the rollover "
            f"lock — a log line there hangs or corrupts rotation"
        )
    for name in ("__init__", "close"):
        assert name in countable, (
            f"{name} runs on neither path (measured), so it takes the log "
            f"line THE RULE requires"
        )


def test_the_exemption_does_not_reach_a_non_logging_handler() -> None:
    """``LCDHandler(BaseHandler)`` is a device handler, not a logging one.

    The qualifier used to be ``base.endswith("Handler")``, which these match.
    Nothing was wrongly exempt then, because neither declared any hook in the
    set — but widening it to ``_open`` / ``flush`` would have spent that luck
    silently, since both are ordinary methods on a device handler.
    """
    countable = _countable_names(_A_GUI_DEVICE_HANDLER)

    assert countable == {"emit", "flush", "_open", "format"}, (
        "the logging exemption leaked onto a non-logging class — every one of "
        "these is a real function that must carry a log line"
    )


def test_formatter_bases_are_matched_exactly_not_by_suffix() -> None:
    """A suffix test is what let the qualifier reach ``BaseHandler``."""
    assert "BaseHandler" not in logging_coverage._FORMATTER_BASES
    assert "Handler" in logging_coverage._FORMATTER_BASES
    assert "RenderOnceRotatingFileHandler" in logging_coverage._FORMATTER_BASES


# =========================================================================
# Bare-name emitters — a log line the Attribute test cannot see
# =========================================================================


def test_bare_name_trace_counts_as_logging() -> None:
    """``core.logs.trace`` is a log line, even though it is not ``log.trace``.

    TRACE is level 5 and not in stdlib, so there is no ``logger.trace`` method;
    the helper takes the logger as its first argument and short-circuits on
    ``isEnabledFor``.  It is therefore called by BARE NAME, which the
    ``ast.Attribute`` test is blind to — so a function whose only log line was a
    TRACE line counted as silent, and the ratchet demanded a log line from a
    function that already had one.  The only ways to satisfy it were to stop
    using the helper or to add a second, redundant log call.

    Caught for real on 2026-09-10 by ``BaseDevice._trace_reply``, the first
    ``trace()`` call site in ``src/``.
    """
    import ast

    fn = ast.parse(
        "def f(self, resp):\n"
        "    trace(logging.getLogger(__name__), 'raw %s', resp.hex())\n"
    ).body[0]
    assert logging_coverage._emits_log(fn), (
        "a bare-name trace() call is a log line — the ratchet must see it")


def test_an_arbitrary_bare_call_is_not_mistaken_for_logging() -> None:
    """The bare-name allowance must stay a short, deliberate list.

    If ``_emits_log`` accepted any bare call it would report near-total
    coverage while seeing nothing — the failure mode a ratchet exists to
    prevent, and the one that would be hardest to notice, because the number
    would only ever look better.
    """
    import ast

    for call in ("compute(x)", "print(x)", "traceback.format_exc()", "str(x)"):
        fn = ast.parse(f"def f(x):\n    {call}\n").body[0]
        assert not logging_coverage._emits_log(fn), (
            f"{call} is not a log line — counting it would inflate coverage")


def test_log_functions_stays_a_deliberate_allowlist() -> None:
    """Pinned so nobody widens it without meaning to."""
    assert set(logging_coverage._LOG_FUNCTIONS) == {"trace"}, (
        "adding a name here lowers the silent count without adding a log line "
        "anywhere — say why in the commit, and lower MAX_SILENT to match")


# =========================================================================
# The receiver matters — a method name alone is not a log line
# =========================================================================


def _emits(src: str) -> bool:
    """Does the ratchet consider this one-function snippet to have logged?"""
    import ast
    return logging_coverage._emits_log(ast.parse(src).body[0])


def test_a_non_logger_with_a_logger_method_name_is_not_a_log_line() -> None:
    """``QMessageBox.warning(...)`` opens a dialog.  It is not a log record.

    This is the dangerous direction.  The ratchet only ever moves DOWN, so a
    false positive permanently lowers the bar and is never noticed again —
    the number only looks better.  A silent function that happens to pop a
    dialog, exit via ``parser.error``, or call ``.info()`` on a parsed
    response would have counted as covered.

    ``src/`` really does contain a ``QMessageBox.warning`` (``trcc_app.py``,
    ``notify_device_failures``); it survived a name-only test purely because
    that function also calls ``log.warning``.
    """
    assert not _emits(
        "def f(self):\n    QMessageBox.warning(self, 'Device connection', body)\n")
    assert not _emits("def f(p):\n    p.error('bad flag')\n")
    assert not _emits("def f(r):\n    r.info('field')\n")
    assert not _emits("def f(x):\n    x.debug('not a logger')\n")


def test_every_real_logger_shape_in_the_tree_still_counts() -> None:
    """Each way ``src/`` actually holds a logger must keep counting."""
    for snippet in (
        "def f():\n    log.info('x')\n",                       # module logger
        "def f():\n    frame_log.debug('x')\n",                # per-frame logger
        "def f():\n    logger.warning('x')\n",
        "def f(self):\n    self.log.debug('x')\n",             # per-device logger
        "def f(self):\n    sink.log(level, 'x')\n",            # dispatch alias
        "def f():\n    logging.getLogger(__name__).info('x')\n",
        "def f(self, r):\n    trace(logging.getLogger(__name__), 'raw %s', r)\n",
    ):
        assert _emits(snippet), f"a real log line stopped counting: {snippet!r}"


def test_logger_receivers_matches_what_the_tree_actually_uses() -> None:
    """Self-audit: no unrecognised receiver may carry a logging method name.

    Keeps the allowlist honest in BOTH directions.  A new logger alias would
    otherwise be silently invisible (its functions counted silent, demanding
    redundant log lines), and a new non-logger — the next ``QMessageBox`` —
    would be silently counted as logging.  Either way the failure is quiet,
    which is exactly what a ratchet must not permit.
    """
    import ast
    import collections

    #: Receivers that carry a logging METHOD NAME but are not loggers.  Each is
    #: a deliberate acknowledgement, not an allowance — none of them count.
    not_loggers = {"QMessageBox"}

    found: collections.Counter[str] = collections.Counter()
    for path in (_SRC_ROOT).rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in logging_coverage._LOG_CALLS):
                continue
            value = node.func.value
            if isinstance(value, ast.Name):
                found[value.id] += 1
            elif isinstance(value, ast.Attribute):
                found[value.attr] += 1

    known = set(logging_coverage._LOGGER_RECEIVERS) | not_loggers
    unknown = {r: n for r, n in found.items() if r not in known}
    assert not unknown, (
        "a receiver with a logging method name is neither a known logger nor "
        "an acknowledged non-logger — decide which, then add it to "
        "_LOGGER_RECEIVERS (it logs) or to not_loggers here (it does not):\n"
        + "\n".join(f"  {r}: {n} call(s)" for r, n in sorted(unknown.items()))
    )


# =========================================================================
# A log line may not grow with the data it describes
#
# MEASURED 2026-09-18 on a live 5.5 MB log: FOUR lines were 90% of every byte
# written, and all four logged a CONTAINER they had been handed rather than
# what the call did.  ``aggregator._store``, whose whole job is to put one
# number into a dict, logged the WHOLE dict -- 4.37 MB over 5,035 lines, 867
# bytes to record one reading, climbing as the dict filled through a sweep.
#
# The cost is not disk.  The rotating ring is 1 MB x 5, so it turned over
# every ~90 seconds, which means ``trcc report`` after any incident older
# than a minute held nothing but the last seconds of sensor polls.  The log
# was erasing the evidence it exists to keep.
#
# A structural gate was measured and rejected: 75 call sites format an
# unbounded container, and most are right -- a command's argument list is
# useful and short.  The invariant is the RENDERED SIZE, so that is what is
# gated here, on the paths that actually run hot.
# =========================================================================

#: A one-shot line can be long; these fire per reading, per render or per
#: poll.  Anything over this is a container being dumped.
MAX_HOT_LINE_BYTES = 200


def _hot_lines() -> list[str]:
    """Drive the hot log paths with realistic data; return what they wrote."""
    import io
    import logging as _logging

    from trcc.adapters.sensors.aggregator import _store
    from trcc.adapters.sensors.psutil_sources import ComputedIo
    from trcc.services.overlay import resolve_overlay_elements

    buf = io.StringIO()
    handler = _logging.StreamHandler(buf)
    handler.setFormatter(_logging.Formatter("%(name)s: %(message)s"))
    root = _logging.getLogger()
    root.addHandler(handler)
    previous = root.level
    root.setLevel(_logging.DEBUG)
    # The frame family is ENABLED here, and that is the point.  This gate gets
    # its answer from the RENDERED SIZE on paths that run hot, which has
    # nothing to do with which family carries the line -- an oversized line
    # costs most exactly when a reporter turns the family on to diagnose
    # something.  It was silenced until 2026-09-19, when the three hot
    # emitters below moved onto it (they were 56 of the sensor tick's 57
    # records); that silencing would have made this gate blind to the very
    # lines it was written for, and its control test said so.
    frame = _logging.getLogger("trcc.frame")
    previous_frame = frame.level
    frame.setLevel(_logging.DEBUG)
    try:
        readings: dict[str, float] = {}
        for i, key in enumerate([
            "cpu:temp", "cpu:usage", "cpu:freq", "cpu:power",
            "memory:used", "memory:available", "memory:percent",
            "gpu:primary:temp", "gpu:primary:usage", "gpu:primary:power",
            "disk:read", "disk:write", "disk:activity",
            "net:up", "net:down", "fan:cpu", "fan:gpu",
        ]):
            _store(readings, key, float(i))

        io_source = ComputedIo()
        io_source._poll_disk(readings, 1.0)
        io_source._poll_net(readings, 1.0)

        theme = {
            "name": "Theme1", "width": 320, "height": 320,
            "elements": [
                {"type": "metric", "x": 74, "y": 250, "metric": "cpu:temp",
                 "format": "{value:.0f}C", "name": "Microsoft YaHei",
                 "size": 36.0, "bold": True, "italic": False,
                 "color": "#808080", "show_unit": True}
                for _ in range(7)
            ],
        }
        resolve_overlay_elements(theme, None)
    finally:
        root.removeHandler(handler)
        root.setLevel(previous)
        # Restored, unlike the level this replaced: WARNING happened to be the
        # family's default, so leaking it was harmless.  DEBUG is not — it
        # would un-silence the frame family for every later test in this worker.
        frame.setLevel(previous_frame)
    return [line for line in buf.getvalue().splitlines() if line.strip()]


def test_a_hot_log_line_does_not_carry_the_whole_collection() -> None:
    """The per-reading, per-poll and per-render lines stay small."""
    oversized = [(len(line), line[:160]) for line in _hot_lines()
                 if len(line) > MAX_HOT_LINE_BYTES]

    assert not oversized, (
        f"a hot log line is over {MAX_HOT_LINE_BYTES} bytes, which means it "
        "is formatting a collection instead of what the call did — that is "
        "what turned the log over every 90 seconds and left `trcc report` "
        "with no evidence:\n"
        + "\n".join(f"  {n} B: {text}…" for n, text in oversized)
    )


def test_the_hot_paths_really_do_log(monkeypatch) -> None:
    """The gate above passes trivially if nothing logs at all.

    Its own control: the driver must produce the lines it is measuring, or
    an emitter that fell silent would read as a win.
    """
    lines = _hot_lines()

    assert sum("_store:" in line for line in lines) >= 17, lines[:5]
    assert any("_poll_disk:" in line for line in lines), lines[:5]
    assert any("_poll_net:" in line for line in lines), lines[:5]
    assert any("resolve_overlay_elements:" in line for line in lines), lines[:5]
