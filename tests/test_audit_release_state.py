"""The C# audit docs must say which release they describe, and be right about it.

Every `dev/decompiler/AUDIT_*.md`/`BEHAVIOR_*.md` opened with "TRCC 2.1.6" for
months. None were written against it — they describe a decompile whose
`AssemblyVersion` is 2.0.3.0, extracted four months before the 2.1.6 installer
existed. Nothing caught it because a version label is prose: free to write, and
checked by nobody. Meanwhile the miners were repointed at the real 2.1.6 tree, so
the generated artifacts and the hand-written audits quietly described different
programs, and every line citation in them pointed into the wrong file.

Two tiers, because only one of them can run everywhere:

* **Tier 1 — the label.** Pure text: every doc records its release in a generated
  state block, and no doc asserts a release anywhere else. This is the defect
  class that started it and it needs nothing but the repo, so it runs in CI.
* **Tier 2 — the citations.** Needs the decompiles, which are not in the repo
  (proprietary, ~87k lines, extracted locally). Skipped where they are absent
  rather than silently passing — a gate that cannot see its subject must say so.

Deliberately NOT a CI step in `ci.yml`: `audit_release.py --check` exits 1 when
it finds fewer than two decompiles, which is every CI runner, so wiring it there
would fail every build. A skipping test is the honest shape.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "dev" / "decompiler"))

import audit_release  # noqa: E402  # pyright: ignore[reportMissingImports]
from core.csharp import DECOMPILE_ROOT  # noqa: E402  # pyright: ignore[reportMissingImports]

_DOCS = audit_release.docs()
_TREES = audit_release.discover(DECOMPILE_ROOT.parent)
_NEEDS_DECOMPILE = pytest.mark.skipif(
    len(_TREES) < 2,
    reason=f"needs two decompiles to compare; found {len(_TREES)} under "
           f"{DECOMPILE_ROOT.parent}",
)


def test_audit_docs_exist() -> None:
    """The corpus is there at all — a passing suite over zero docs proves nothing."""
    assert len(_DOCS) >= 20, f"expected the audit corpus, found {len(_DOCS)} docs"


@pytest.mark.parametrize("doc", _DOCS, ids=lambda p: p.name)
def test_doc_records_which_release_it_describes(doc: Path) -> None:
    """Each doc carries a state block naming its origin and what it addresses."""
    state = audit_release.State.read(doc.read_text())
    assert state is not None, (
        f"{doc.name} has no audit-state block — run: "
        f"python3.12 dev/decompiler/audit_release.py --rebase"
    )
    assert state.origin and state.addresses


@pytest.mark.parametrize("doc", _DOCS, ids=lambda p: p.name)
def test_doc_names_no_release_outside_its_state_block(doc: Path) -> None:
    """Prose may not assert a release; only the generated block may.

    After a re-anchor a doc is a hybrid — prose from the release it was read
    from, line numbers addressing the one we build against — so any single
    version in a title is false for half of it. That is exactly how the original
    "TRCC 2.1.6" label came to be wrong.
    """
    prose = audit_release._STATE_RE.sub("", doc.read_text())
    named = sorted(set(audit_release._VERSION_ANY.findall(prose)))
    assert not named, (
        f"{doc.name} names TRCC {', '.join(named)} in prose. Only the "
        f"audit-state block may name a release."
    )


@_NEEDS_DECOMPILE
@pytest.mark.parametrize("doc", _DOCS, ids=lambda p: p.name)
def test_citations_resolve_in_the_release_the_doc_addresses(doc: Path) -> None:
    """A doc claiming to address a release must actually land there.

    Bounded to citations into methods that did NOT change between the releases:
    a changed method's citation is deliberately left at its old address and
    reported as pending instead. Exceptions come from the doc's recorded
    `known-bad` list — recomputing "was this already broken?" would wave through
    a citation corrupted to line 9999, because a bogus line fails everywhere.
    """
    state = audit_release.State.read(doc.read_text())
    assert state is not None
    by_version = {t.version: t for t in _TREES}
    current = next((t for t in _TREES if t.path == DECOMPILE_ROOT), _TREES[-1])
    if state.addresses != current.version:
        pytest.skip(f"{doc.name} addresses {state.addresses}, not {current.version}")

    fails = [f for f in audit_release.unresolved(doc, by_version[state.origin], current)
             if audit_release._fail_key(f) not in state.known_bad]
    assert not fails, (
        f"{doc.name} says it addresses TRCC {current.release} but "
        f"{len(fails)} citation(s) into unchanged methods do not land there: "
        f"{', '.join(fails[:5])}"
    )
