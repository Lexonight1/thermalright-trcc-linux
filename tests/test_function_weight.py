"""Heavy functions may only decrease — two ratchets.

**Why this is enforced rather than remembered.**  ``test_god_classes`` watches
how many methods a class carries and is therefore blind to what those methods
CONTAIN: ``uc_led_control._setup_ui`` is **585 lines** and no gate in this repo
had an opinion about it.  ``class_census`` also walks ``ClassDef`` only, so a
module-level function is outside its scope entirely — and ``format_metric``
(30 decisions) and ``ipc._coerce`` are both module-level.

**Two ratchets, because the axes are not substitutes.**  Measured 2026-09-19,
five functions trip both; 22 are only long and 2 are only branchy.  A length
gate cannot see ``format_metric`` at 70 lines with 30 decisions, and a decision
gate cannot see the 585-line builder with 16.  ``dev/tools/function_census.py``
documents what counts as a decision and why a comprehension counts as its loop
and nothing more.

Like :mod:`tests.test_god_classes` and :mod:`tests.test_logging_coverage` these
cannot start green, so they are **ratchets**:

* a function crosses a line -> the count rises -> **fail**
* a function is decomposed -> the count falls -> **fail**, asking you to lower
  the baseline so the ground gained cannot be given back

**A ratchet does not demand the cleanup, and that matters here.**  Four of the
long functions live in ``ui/gui``, which ``project_plan_the_remaining_gap``
says to DELETE rather than decompose — *"the most expensive possible
mistake"*.  Baselining at today's count costs them nothing, stops the skin
growing NEW ones, and tightens for free when it goes.  A carve-out would
instead be permanent blindness if the deletion slips.

**Two functions on these lists are deliberate LEAVEs**, recorded in
``memory/project_function_weight_verdicts.md`` after reading every candidate:
``build_frame`` IS the render pipeline (linear, and the cutover already paid
for fragmenting it — #136), and ``_coerce`` IS recursive type dispatch.  The
baseline counts them precisely because it does not demand they change.
"""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "dev" / "tools"))

import function_census  # noqa: E402  # pyright: ignore[reportMissingImports]

#: LOWER IT when a function comes off the list; never raise it.
MAX_LONG_FUNCTIONS = 16

#: LOWER IT when a function comes off the list; never raise it.
MAX_BRANCHY_FUNCTIONS = 8

#: Passed explicitly so each gate's meaning lives HERE, not in the tool.
LINE_THRESHOLD = 140
DECISION_THRESHOLD = 18


def test_no_new_long_functions() -> None:
    """A function crossing 140 lines fails until it is split."""
    found = function_census.too_long(LINE_THRESHOLD)
    assert len(found) <= MAX_LONG_FUNCTIONS, (
        f"{len(found) - MAX_LONG_FUNCTIONS} new function(s) over "
        f"{LINE_THRESHOLD} lines — extract the phases it already has "
        "(`LoadTheme.execute` is the worked example: load, persist, "
        "overrides, mask, guards, screencast, media, video, send):\n"
        + "\n".join(f"  {f}" for f in sorted(found, key=lambda r: -r.lines))
    )


def test_no_new_branchy_functions() -> None:
    """A function at or over 18 decisions fails until its paths come down."""
    found = function_census.too_branchy(DECISION_THRESHOLD)
    assert len(found) <= MAX_BRANCHY_FUNCTIONS, (
        f"{len(found) - MAX_BRANCHY_FUNCTIONS} new function(s) at or over "
        f"{DECISION_THRESHOLD} decisions — every one is a path someone has to "
        "reason about and test.  If the branches are dispatching on a VALUE, "
        "the fix is a table, not a helper (`format_metric` is that shape):\n"
        + "\n".join(f"  {f}" for f in found)
    )


def test_long_function_baseline_has_no_slack() -> None:
    """A decomposition must lower the number, or the ground can be re-taken."""
    found = function_census.too_long(LINE_THRESHOLD)
    assert len(found) >= MAX_LONG_FUNCTIONS, (
        f"Long functions went DOWN — {MAX_LONG_FUNCTIONS - len(found)} fewer "
        f"than recorded.  Lower MAX_LONG_FUNCTIONS to {len(found)} in "
        "tests/test_function_weight.py so the win is locked in."
    )


def test_branchy_function_baseline_has_no_slack() -> None:
    """Same, for the decision axis."""
    found = function_census.too_branchy(DECISION_THRESHOLD)
    assert len(found) >= MAX_BRANCHY_FUNCTIONS, (
        f"Branchy functions went DOWN — {MAX_BRANCHY_FUNCTIONS - len(found)} "
        f"fewer than recorded.  Lower MAX_BRANCHY_FUNCTIONS to {len(found)} in "
        "tests/test_function_weight.py so the win is locked in."
    )


def test_the_two_axes_are_not_substitutes() -> None:
    """Both ratchets must exist, and this is why.

    If one axis were a superset of the other, the repo would be carrying a
    redundant gate — and the honest response would be to delete one.  This
    pins the measurement that says they are independent, so a future reader
    does not have to take the docstring's word for it.
    """
    long_ = {(f.path, f.lineno) for f in function_census.too_long(LINE_THRESHOLD)}
    branchy = {(f.path, f.lineno)
               for f in function_census.too_branchy(DECISION_THRESHOLD)}
    assert long_ - branchy, "every long function is also branchy — drop a gate"
    assert branchy - long_, "every branchy function is also long — drop a gate"


# =========================================================================
# Self-tests — the measuring device, not the measurement
# =========================================================================


def _module(tmp_path: Path, name: str, source: str) -> Path:
    root = tmp_path / "pkg"
    root.mkdir(exist_ok=True)
    (root / f"{name}.py").write_text(textwrap.dedent(source), encoding="utf-8")
    return root


def test_selftest_a_nested_function_is_not_charged_to_its_parent(
    tmp_path: Path,
) -> None:
    """A factory must not be weighed for code already weighed on its own row.

    ``ui/api/main.py::build_app`` defines two middlewares and an endpoint
    inline; charging their branches to the factory read 9 decisions where the
    factory's own body has 3, and put it on a list it does not belong on.
    """
    root = _module(tmp_path, "factory", """
        def build():
            def endpoint(x):
                if x:
                    return 1
                elif x is None:
                    return 2
                return 3
            return endpoint
    """)
    weighed = {f.name: f.decisions for f in function_census.census(root)}
    assert weighed["build"] == 0, "the nested def's branches leaked upward"
    assert weighed["endpoint"] == 2


def test_selftest_a_comprehension_counts_as_its_loop_only(tmp_path: Path) -> None:
    """Its filters must NOT each count as a branch.

    Counting them inflated ``ipc._coerce`` and ``auto_map`` and ranked compact
    functional code alongside genuinely tangled branching — the opposite of
    what the decision axis is for.
    """
    root = _module(tmp_path, "comp", """
        def f(xs):
            return [x for x in xs if x if x > 1 if x < 9]
    """)
    weighed = {f.name: f.decisions for f in function_census.census(root)}
    assert weighed["f"] == 1


def test_selftest_a_long_body_with_no_branches_is_caught(tmp_path: Path) -> None:
    """The 585-line builder shape: enormous, and almost branch-free.

    A decision gate alone is blind to it, which is the whole argument for
    carrying two ratchets.
    """
    root = _module(tmp_path, "builder",
                   "def build():\n" + "    x = 1\n" * 200)
    found = function_census.too_long(140, root)
    assert [f.name for f in found] == ["build"]
    assert found[0].decisions == 0
    assert function_census.too_branchy(18, root) == []


def test_selftest_a_short_dense_body_is_caught(tmp_path: Path) -> None:
    """The ``format_metric`` shape: 70 lines, 30 decisions.

    The counterpart of the test above — a length gate alone cannot see it.
    """
    body = "".join(f"    if x == {i}:\n        return {i}\n" for i in range(20))
    root = _module(tmp_path, "dense", "def pick(x):\n" + body)
    found = function_census.too_branchy(18, root)
    assert [f.name for f in found] == ["pick"]
    assert function_census.too_long(140, root) == []


def test_selftest_module_level_functions_are_weighed(tmp_path: Path) -> None:
    """``class_census`` walks ClassDef and cannot see these at all.

    ``format_metric`` and ``ipc._coerce`` are both module-level, and both are
    on the decision list.
    """
    root = _module(tmp_path, "top", """
        def loose(x):
            if x: return 1
            return 0

        class Holder:
            def method(self, x):
                if x: return 1
                return 0
    """)
    weighed = {f.name for f in function_census.census(root)}
    assert weighed == {"loose", "Holder.method"}


def test_selftest_an_unparseable_file_raises_rather_than_skipping(
    tmp_path: Path,
) -> None:
    """A skipped file is a gate that silently shrank its own scope."""
    root = _module(tmp_path, "broken", "def oops(:\n    pass\n")
    with pytest.raises(SyntaxError):
        function_census.census(root)
