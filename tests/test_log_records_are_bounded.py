"""A log record may not grow with its payload.

THE RULE gives every function a log line, and the file keeps DEBUG at every
verbosity, because ``trcc report`` is the entire diagnosis for hardware we do
not own.  Both are worth keeping.  What neither can mean is that a function's
``bytes`` parameter goes into the record WHOLE — ``%s`` on ``bytes`` is
``repr``, which escapes every byte at a measured 2.87x, so one record becomes a
copy of the framebuffer:

    320x240   rgb565     153,600 B  ->  0.45 MB per record
    1920x462  rgb565   1,774,080 B  ->  4.9  MB per record
    1600x720  rgb565   2,304,000 B  ->  6.3  MB per record

The rotating file is 1 MB x 5.  On most panels **one frame record exceeds the
whole rotation budget**, so every frame rotates the log and nothing one-shot
survives long enough to be reported.

It happened.  ``e078aadd`` (2026-09-15, *"every surviving file gets its log
line — 1258 silent to 345"*) satisfied the coverage ratchet mechanically,
adding ``log.debug("<func>: <param>=%s", <param>)`` to functions whose
parameter is a display frame.  Eight days later reporter #220 — asked for a
desktop-scaling repro, and cooperating fully — uploaded two reports in which
**98% of every byte was eleven ``_build_frame_type2`` records**, spanning one
second.  The ``screen '...': devicePixelRatio=...`` line the whole request
existed to capture had rotated away before he ran ``trcc report``.  He
described it without knowing: *"constantly writing to trcc.log through to
trcc.log.5, the whole time."*

So the gate that exists to make reports diagnosable is what made them
undiagnosable, and no test could see it: the ratchet counts SILENT functions,
and these are the opposite of silent.

**The scope is DERIVED, from annotations** -- a parameter annotated ``bytes`` /
``bytearray`` / ``memoryview`` (or a union of one) may not be interpolated bare.
Nothing here lists a call site or a parameter name, so the next mechanical
coverage pass widens this gate instead of slipping past it.  ``Blob(x)`` is an
``ast.Call``, not a bare ``Name``, so the fix is what the gate looks for.

**What it deliberately does not catch:** a LOCAL holding bytes, and an
unannotated parameter.  Neither is derivable from the AST without type
inference, and a gate that guessed by NAME is the thing this file replaces --
``_build_packet(payload: LedPayload)`` is a domain object, and a name-based
rule flagged it (pyright caught that during the fix).  Annotations are the
honest scope.

MUTATION CHECK -- unwrap any ``Blob(...)`` at a confirmed site, e.g.
``log.debug("_write_frame: frame=%s", frame)`` in ``bulk_lcd.py``, and
``test_no_log_record_interpolates_a_whole_buffer`` must name that file, line
and parameter.  Confirmed to fail before this file was committed.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from trcc.core.logs import BLOB_HEAD_BYTES, Blob

_SRC = Path(__file__).resolve().parents[1] / "src" / "trcc"

_LOG_METHODS = frozenset({"debug", "info", "warning", "error",
                          "exception", "critical", "log"})
#: Annotations whose value is a buffer whose size is the payload's size.
_BUFFER_TYPES = frozenset({"bytes", "bytearray", "memoryview"})


def _is_buffer_annotation(node: ast.expr | None) -> bool:
    """True for ``bytes``, ``bytes | None``, ``Optional[bytes]`` and friends."""
    if node is None:
        return False
    if isinstance(node, ast.Name):
        return node.id in _BUFFER_TYPES
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value.split("|")[0].strip() in _BUFFER_TYPES
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        return (_is_buffer_annotation(node.left)
                or _is_buffer_annotation(node.right))
    if isinstance(node, ast.Subscript):          # Optional[bytes]
        return _is_buffer_annotation(node.slice)
    return False


def _buffer_params(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    a = fn.args
    return {
        arg.arg
        for arg in (*a.posonlyargs, *a.args, *a.kwonlyargs,
                    *(x for x in (a.vararg, a.kwarg) if x is not None))
        if _is_buffer_annotation(arg.annotation)
    }


def _violations() -> list[tuple[str, int, str]]:
    """Every ``log.X(..., <buffer param>)`` in the shipping tree."""
    found: list[tuple[str, int, str]] = []
    for path in sorted(_SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not (buffers := _buffer_params(fn)):
                continue
            for node in ast.walk(fn):
                if not (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Attribute)
                        and node.func.attr in _LOG_METHODS):
                    continue
                for arg in node.args[1:]:
                    if isinstance(arg, ast.Name) and arg.id in buffers:
                        found.append((
                            str(path.relative_to(_SRC.parent)),
                            node.lineno, arg.id,
                        ))
    return found


def test_no_log_record_interpolates_a_whole_buffer() -> None:
    bad = _violations()
    assert not bad, (
        "these log calls put a whole binary payload in the record — one frame "
        "can exceed the entire 1 MB x 5 rotation budget, which is how #220's "
        "reporter sent us two reports that were 98% framebuffer.  Wrap the "
        "argument: log.debug(..., Blob(x)).\n  "
        + "\n  ".join(f"{f}:{ln}  {name}" for f, ln, name in bad)
    )


def _scanned() -> list[tuple[str, str]]:
    """``(file, function)`` for every function the gate above can see."""
    return [
        (str(p.relative_to(_SRC.parent)), fn.name)
        for p in _SRC.rglob("*.py")
        for fn in ast.walk(ast.parse(p.read_text(encoding="utf-8")))
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
        and _buffer_params(fn)
    ]


def test_the_scan_is_not_empty_and_reaches_the_wire() -> None:
    """The gate SKIPS a function with no annotated buffer parameter.

    Without a floor, dropping type hints would shrink the scope toward zero and
    leave the gate green while measuring nothing.  A FLOOR is what makes that
    fail: naming only the three wire files was too weak to catch it — removing
    one annotation from ``bulk_lcd._write_frame`` left the FILE in the set
    (its siblings still declare ``bytes``) and the check passed, so it claimed
    more than it proved.  Measured at 72 functions across 26 files.
    """
    scanned = _scanned()
    assert len(scanned) >= 60, (
        f"only {len(scanned)} functions declare a bytes parameter (was 72) — "
        f"the scope is collapsing, so the gate above is proving less than it "
        f"looks like it is"
    )
    files = {f for f, _ in scanned}
    for wire in ("trcc/adapters/device/hid_lcd.py",
                 "trcc/adapters/device/bulk_lcd.py",
                 "trcc/adapters/device/ly_lcd.py"):
        assert wire in files, (
            f"{wire} declares no bytes-annotated parameter — the scan is not "
            f"reaching the wire adapters, so it is measuring nothing"
        )


# ── the rendering itself ────────────────────────────────────────────────


@pytest.mark.parametrize("size", [0, 1, BLOB_HEAD_BYTES - 1,
                                  BLOB_HEAD_BYTES, 153_600, 2_304_000])
def test_rendering_is_bounded_whatever_the_payload(size: int) -> None:
    """The whole point: the record's size does not depend on the payload's."""
    rendered = str(Blob(bytes(size)))
    assert len(rendered) <= 80, rendered
    if size > BLOB_HEAD_BYTES:
        assert str(size) in rendered, rendered


def test_rendering_keeps_enough_to_recognise_a_frame() -> None:
    """A magic number has to survive, or the bound costs us the diagnosis.

    Both arms: a real payload is far past the head budget and renders as hex,
    while a short one renders as itself — the magic is legible either way,
    which is the only property that matters at a wire boundary.
    """
    jpeg = b"\xff\xd8\xff\xe0" + bytes(150_000)
    assert "ff d8 ff e0" in str(Blob(jpeg))

    type2 = b"\xda\xdb\xdc\xdd\x02\x00" + bytes(153_600)
    assert "da db dc dd" in str(Blob(type2))

    assert r"\xff\xd8" in str(Blob(b"\xff\xd8short"))


def test_a_short_payload_stays_readable_as_itself() -> None:
    """For a device path the BYTES are the diagnosis; hex would be a downgrade."""
    assert str(Blob(b"/dev/hidraw0")) == "b'/dev/hidraw0'"
    assert str(Blob(b"")) == "b''"

# ── the same defect one level up: a dataclass that carries a buffer ─────


def _dataclasses_exposing_a_buffer() -> list[tuple[str, str, str]]:
    """``(file, class, field)`` for every dataclass whose repr renders bytes."""
    found: list[tuple[str, str, str]] = []
    for path in sorted(_SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for cls in ast.walk(tree):
            if not isinstance(cls, ast.ClassDef):
                continue
            decorators = [ast.unparse(d) for d in cls.decorator_list]
            if not any("dataclass" in d for d in decorators):
                continue
            # A hand-written __repr__ (or repr=False on the whole class) owns
            # the question itself -- Blob is exactly that case.
            if "repr=False" in " ".join(decorators) or any(
                isinstance(n, ast.FunctionDef) and n.name == "__repr__"
                for n in cls.body
            ) or any(
                isinstance(n, ast.Assign)
                and any(getattr(t, "id", "") == "__repr__" for t in n.targets)
                for n in cls.body
            ):
                continue
            for node in cls.body:
                if not (isinstance(node, ast.AnnAssign)
                        and isinstance(node.target, ast.Name)
                        and _is_buffer_annotation(node.annotation)):
                    continue
                if node.value is not None and "repr=False" in ast.unparse(node.value):
                    continue
                found.append((str(path.relative_to(_SRC.parent)),
                              cls.name, node.target.id))
    return found


def test_no_dataclass_renders_a_buffer_into_its_repr() -> None:
    """``%s`` on an OBJECT is a log record too.

    Found by Phase 2 of the fix above, not by reasoning: with every bytes
    PARAMETER bounded, a driven run of the real mock GUI still produced a
    **230 KB** record -- ``_handshake_detail: result=HandshakeResult(...)``,
    whose generated repr embeds ``raw_response`` whole.  The annotation gate
    cannot see it: the parameter is a ``HandshakeResult``, not ``bytes``.

    Fixing the dataclass fixes every call site at once, present and future,
    which is why this arm exists instead of eight more wrapped arguments.
    ``SendFrame.data`` is the sharpest case -- ``App.dispatch`` logs
    ``dispatch %r`` on every Command.
    """
    bad = _dataclasses_exposing_a_buffer()
    assert not bad, (
        "these dataclasses put a whole buffer in their repr, so ANY log call "
        "that renders the object renders the payload.  Declare the field "
        "``field(repr=False)`` and log ``Blob(x)`` where the bytes are wanted."
        "\n  " + "\n  ".join(f"{f}:{c}.{fld}" for f, c, fld in bad)
    )
