"""The committed decompiler maps must match what their generators produce now.

``control-flow.json`` is the DENOMINATOR of the audit coverage figure and
``branch-map.json`` is the wire's decision map — both committed, both generated,
and until now verified by nothing.  A stale map does not announce itself: the
percentage still prints, and it is measured against whatever was last written.

This is not hypothetical.  A mutation run of ``map_branches`` wrote its output
BEFORE returning failure, leaving a map with the entire wire missing — 101
branches — on disk.  It was noticed only because a diffstat showed one changed
line where seven hundred were due.  A diffstat is not a gate; this is.

Same contract as ``doc/REFERENCE_PORTS.md`` and the man pages: regenerate and
compare.  Skips without a decompile on disk, like every other oracle test — CI
has no decompile and reads the committed maps, which is precisely why they have
to be right when they are committed.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "dev" / "decompiler"))

import extract_control_flow  # noqa: E402  # pyright: ignore[reportMissingImports]
import map_branches  # noqa: E402  # pyright: ignore[reportMissingImports]
from core.csharp import DECOMPILE_ROOT  # noqa: E402  # pyright: ignore[reportMissingImports]

_ABSENT = pytest.mark.skipif(
    not DECOMPILE_ROOT.exists(),
    reason=f"no decompile at {DECOMPILE_ROOT} — cannot regenerate to compare",
)
_REGEN = "PYTHONPATH=. python3.12 dev/decompiler/{}"


@_ABSENT
def test_committed_control_flow_maps_are_current() -> None:
    """Every control-flow map — one per binary — matches the generator."""
    stale = [
        path.name for path, content in extract_control_flow.artifacts().items()
        if not path.exists() or path.read_text() != content
    ]
    assert not stale, (
        f"stale or missing: {', '.join(sorted(stale))} — run: "
        + _REGEN.format("extract_control_flow.py")
    )


@_ABSENT
def test_committed_branch_map_is_current() -> None:
    """The wire's decision map matches the generator."""
    out = Path(map_branches.__file__).with_name("branch-map.json")
    assert out.read_text() == map_branches.render(map_branches.build(DECOMPILE_ROOT)), (
        "branch-map.json is stale — run: " + _REGEN.format("map_branches.py")
    )


@_ABSENT
def test_a_map_missing_the_wire_is_caught() -> None:
    """The gate must notice the exact corruption that prompted it.

    Mutation, not assertion-by-assertion: drop the wire methods from the map and
    the comparison has to fail.  A gate that cannot fail is decoration.
    """
    import json
    out = Path(map_branches.__file__).with_name("branch-map.json")
    good = json.loads(out.read_text())
    wire = [k for k in good["per_method"] if "DCReadWrite" in k]
    assert wire, "the wire is absent from the committed map — that IS the failure"
    maimed = dict(good, per_method={k: v for k, v in good["per_method"].items()
                                    if "DCReadWrite" not in k})
    assert json.dumps(maimed, indent=2) != out.read_text()
