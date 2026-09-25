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
from core.citations import walk  # noqa: E402  # pyright: ignore[reportMissingImports]
from core.csharp import DECOMPILE_ROOT  # noqa: E402  # pyright: ignore[reportMissingImports]

_DOCS = audit_release.docs()
_TREES = audit_release.discover(DECOMPILE_ROOT.parent)


def test_audit_docs_exist() -> None:
    """The corpus is there at all — a passing suite over zero docs proves nothing."""
    assert len(_DOCS) >= 20, f"expected the audit corpus, found {len(_DOCS)} docs"


@pytest.mark.parametrize("doc", _DOCS, ids=lambda p: p.name)
def test_doc_records_which_release_it_describes(doc: Path) -> None:
    """Each doc carries a state block naming its origin and what it addresses."""
    state = audit_release.State.read(doc.read_text(encoding="utf-8"))
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
    prose = audit_release._STATE_RE.sub("", doc.read_text(encoding="utf-8"))
    named = sorted(set(audit_release._VERSION_ANY.findall(prose)))
    assert not named, (
        f"{doc.name} names TRCC {', '.join(named)} in prose. Only the "
        f"audit-state block may name a release."
    )


@pytest.mark.parametrize("doc", _DOCS, ids=lambda p: p.name)
def test_citations_resolve_in_the_release_the_doc_addresses(doc: Path) -> None:
    """A doc claiming to address a release must actually land there.

    Bounded to citations into methods that did NOT change between the releases:
    a changed method's citation is deliberately left at its old address and
    reported as pending instead. Exceptions come from the doc's recorded
    `known-bad` list — recomputing "was this already broken?" would wave through
    a citation corrupted to line 9999, because a bogus line fails everywhere.
    """
    state = audit_release.State.read(doc.read_text(encoding="utf-8"))
    assert state is not None
    if not _TREES:
        pytest.skip(f"no decompile under {DECOMPILE_ROOT.parent}")
    by_version = {t.version: t for t in _TREES}
    current = next((t for t in _TREES if t.path == DECOMPILE_ROOT), _TREES[-1])
    if state.addresses != current.version:
        pytest.skip(f"{doc.name} addresses {state.addresses}, not {current.version}")

    # The precondition is this doc's ORIGIN release, not a tree COUNT.  It used
    # to be ``skipif(len(_TREES) < 2, "needs two decompiles to compare")``, which
    # is a different claim and a misleading one: it reads as "add a decompile and
    # these run", when adding any second decompile makes them FAIL with
    # ``KeyError`` on the origin version.  What they need is the specific tree
    # each doc was read from.  ``origin`` is history and never moves, so a
    # rebase does NOT make it self-heal: the 2.0.3 tree was deleted on
    # 2026-08-18 and every one of these skipped until it was re-extracted as an
    # origin-only tree on 2026-09-25 -- the rebase onto 2.1.8 cannot be verified
    # without it.  Absent, this reports UNRUNNABLE-and-why rather than a count.
    if (origin := by_version.get(state.origin)) is None:
        pytest.skip(
            f"{doc.name} was read from TRCC {state.origin}, which is not on disk "
            f"under {DECOMPILE_ROOT.parent} (only {', '.join(t.label for t in _TREES)}). "
            f"Comparing citations needs the origin release — decompile it to "
            f"{DECOMPILE_ROOT.parent}/TRCC_<release>_decompiled."
        )

    fails = [f for f in audit_release.unresolved(doc, origin, current)
             if audit_release._fail_key(f) not in state.known_bad]
    assert not fails, (
        f"{doc.name} says it addresses TRCC {current.release} but "
        f"{len(fails)} citation(s) into unchanged methods do not land there: "
        f"{', '.join(fails[:5])}"
    )


def test_only_docs_citing_a_decompile_are_owned_by_the_tool() -> None:
    """A native audit cites Ghidra addresses, not file:line, so the tool must leave it alone.

    It used to restamp every doc, and on 2026-09-25 replaced AUDIT_SCSI.md's own
    provenance note with "every method it documents is byte-identical" -- for a
    binary the tool cannot read.  Pinned both ways: every owned doc cites
    something, and every doc left out cites nothing.
    """
    corpus = sorted(p for p in list(audit_release.DEC.glob("AUDIT_*.md"))
                    + list(audit_release.DEC.glob("BEHAVIOR_*.md"))
                    if p.name != audit_release.INDEX)
    left_out = [p.name for p in corpus if p not in _DOCS]
    assert "AUDIT_SCSI.md" in left_out
    assert all(audit_release.parse(p) for p in _DOCS)
    assert not any(audit_release.parse(audit_release.DEC / n) for n in left_out)




def test_a_citation_into_a_changed_method_refuses_to_move() -> None:
    """The rebase moves a citation with ITS method, never with a neighbour.

    `BEHAVIOR_FORMCZTV.md` cites `ReadSystemConfiguration` at 4642 -- its 2.0.3
    address, left there because the method changed.  In the 2.1.6 tree that line
    sits inside a different, unchanged method, and a "whatever spans the line"
    fallback carried the citation along with it to 4742: nowhere, in any release.
    Eleven citations moved that way on 2026-09-25 before this was caught.
    """
    by_release = {t.release: t for t in _TREES}
    if not {"2.1.6", "2.1.8"} <= by_release.keys():
        pytest.skip("needs the TRCC 2.1.6 and 2.1.8 decompiles on disk")
    doc = audit_release.DEC / "BEHAVIOR_FORMCZTV.md"
    rebase = audit_release.Rebase(audit_release.Locator(
        by_release["2.1.6"], by_release["2.1.8"], {"FormCZTV.cs"}))
    cite = next(h for _, hits in walk(doc, "- `ReadSystemConfiguration` "
                                                   "(FormCZTV.cs:4642) — x\n")
                for h in hits)
    assert rebase.move(cite, 4642) is None
