"""God classes may only decrease — the ratchet.

**Why this is enforced rather than remembered.**  ``ui/qtgui`` was measured
at ZERO classes over 25 implemented methods on 2026-09-15.  Two days later
``SystemPanel`` was at 28 — six unrelated concerns in one widget — and
nothing failed, because nothing was watching.  Every gate in this repo
asserts logging, imports, types or docs; none of them had an opinion about a
class growing without bound.

Like :mod:`tests.test_logging_coverage` it cannot start green: eleven
classes are over the line today.  Failing on all of them would put CI
permanently red, which is how a gate gets ignored.  So it is a **ratchet**:

* a class crosses the line -> the count rises -> **fail**
* a class is decomposed or removed -> the count falls -> **fail**, asking you
  to lower :data:`MAX_GOD_CLASSES` so the ground gained cannot be given back

The weight is methods that have an IMPLEMENTATION; ``dev/tools/class_census.py``
documents why an ``@abstractmethod`` does not count and what counting raw
methods would have done to the two ports.  The threshold is passed
explicitly from here, so it cannot be raised quietly in the tool.
"""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "dev" / "tools"))

import class_census  # noqa: E402

#: The one number.  LOWER IT when a class comes off the list; never raise it.
#:
#: 12 -> 11 on 2026-09-18: ``SystemPanel`` (28 methods, six concerns in one
#: widget) became ``panels/system/`` — one ``SystemBox`` per concern and a
#: two-method host — the same split ``panels/led/`` applies to LED control.
#: It was qtgui's only entry on this list, and a 65% outlier in its own skin.
MAX_GOD_CLASSES = 11

#: Passed explicitly so the gate's meaning lives HERE, not in the tool.
THRESHOLD = 25


def test_no_new_god_classes() -> None:
    """A class crossing 25 implemented methods fails until it is split."""
    found = class_census.god_classes(THRESHOLD)
    assert len(found) <= MAX_GOD_CLASSES, (
        f"{len(found) - MAX_GOD_CLASSES} new class(es) at or over {THRESHOLD} "
        "implemented methods — split the concerns into their own classes "
        "(``panels/led/`` and ``panels/system/`` are the worked examples):\n"
        + "\n".join(f"  {c}" for c in found)
    )


def test_god_class_baseline_has_no_slack() -> None:
    """A decomposition must lower the number, or the ground can be re-taken."""
    found = class_census.god_classes(THRESHOLD)
    assert len(found) >= MAX_GOD_CLASSES, (
        f"God classes went DOWN — {MAX_GOD_CLASSES - len(found)} fewer than "
        f"recorded.  Lower MAX_GOD_CLASSES to {len(found)} in "
        "tests/test_god_classes.py so the win is locked in."
    )


# =========================================================================
# Self-tests — the measuring device, not the measurement
# =========================================================================
#
# Five instrument bugs were found in one session on 2026-09-12, each in a
# gate whose subject was fine.  A census is only as good as what it can see,
# so these drive the collector over sources written to provoke it.


def _module(tmp_path: Path, name: str, source: str) -> Path:
    root = tmp_path / "pkg"
    root.mkdir(exist_ok=True)
    (root / f"{name}.py").write_text(textwrap.dedent(source), encoding="utf-8")
    return root


def test_selftest_abstract_methods_do_not_count(tmp_path: Path) -> None:
    """A 30-method PORT is a contract, not a god class.

    Counting raw methods put ``ContentStore`` (26/26 abstract) and
    ``Platform`` (25/24) at the top of the list, which would have made the
    gate demand an ISP change to the domain in the name of decomposition.
    """
    body = "\n".join(
        f"    @abstractmethod\n    def m{i}(self) -> None: ...\n"
        for i in range(30)
    )
    root = _module(tmp_path, "port", "from abc import ABC, abstractmethod\n\n"
                                     "class Port(ABC):\n" + body)

    assert class_census.god_classes(THRESHOLD, root) == []
    weighed = {c.name: c for c in class_census.census(root)}
    assert weighed["Port"].methods == 0
    assert weighed["Port"].abstract == 30


def test_selftest_a_concrete_class_of_the_same_width_is_caught(
    tmp_path: Path,
) -> None:
    """The counterpart: without the decorator, the same 30 methods DO count.

    An exclusion that excluded everything would pass the test above while
    making the gate blind, so the two are asserted as a pair.
    """
    body = "\n".join(f"    def m{i}(self) -> None: pass\n" for i in range(30))
    root = _module(tmp_path, "fat", "class Fat:\n" + body)

    found = class_census.god_classes(THRESHOLD, root)
    assert [c.name for c in found] == ["Fat"]
    assert found[0].methods == 30


def test_selftest_an_unparseable_file_raises_rather_than_skipping(
    tmp_path: Path,
) -> None:
    """A skipped file is a gate that silently shrank its own scope.

    The file a census cannot read is exactly the one being edited, so a
    ``try: ... except SyntaxError: continue`` would go blind precisely when
    it matters.  ``_role_port_members`` collecting only ``@abstractmethod``
    names took three gates blind at once with the suite green; this is the
    same shape, pre-empted.
    """
    root = _module(tmp_path, "broken", "class Oops(:\n    pass\n")

    with pytest.raises(SyntaxError):
        class_census.census(root)


def test_selftest_nested_and_async_methods_are_weighed(tmp_path: Path) -> None:
    """``ast.walk`` must reach a class defined inside another class or a
    function, and ``async def`` is a method like any other — a collector that
    matched only top-level ``FunctionDef`` would under-count both."""
    root = _module(tmp_path, "nested", """
        class Outer:
            class Inner:
                async def a(self) -> None: pass
                def b(self) -> None: pass

        def factory():
            class Local:
                def c(self) -> None: pass
            return Local
    """)

    weighed = {c.name: c.methods for c in class_census.census(root)}
    assert weighed == {"Outer": 0, "Inner": 2, "Local": 1}
