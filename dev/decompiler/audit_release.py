#!/usr/bin/env python3
"""Keep the audit docs honest about which release of the C# they describe.

Every `AUDIT_*.md`/`BEHAVIOR_*.md` used to open with "TRCC 2.1.6".  None were
written against it: they describe a tree whose `AssemblyVersion` is 2.0.3.0 and
whose `TRCC.exe` predates the 2.1.6 installer by four months.  Nobody noticed for
months because a version label is prose — it costs nothing to write and nothing
checks it.

Two facts about a doc are easy to confuse and must not be:

* **origin** — the release it was *read from*.  History; measured once, then
  frozen, because the evidence for it (citation line numbers) is destroyed the
  moment the citations are re-anchored.
* **addresses** — the release its line numbers *point at* now.  Changes when
  ``--rebase`` runs.

Both are recorded in a generated block in the doc.  Inferring `addresses` from
the citations instead of recording it is not a shortcut, it is a bug: the first
version of this tool did exactly that, so re-running ``--rebase`` re-applied the
offset to already-moved citations and silently corrupted them
(``BmpToThemeFile`` 972 -> 1000 -> 1492).  A recorded addressing space makes a
second run a no-op by construction.

    python3.12 dev/decompiler/audit_release.py             # status table
    python3.12 dev/decompiler/audit_release.py --rebase    # move citations onto the current tree
    python3.12 dev/decompiler/audit_release.py --check     # CI gate
    python3.12 dev/decompiler/audit_release.py --worklist worklist.json

Re-anchoring raises the coverage meter, and that rise is NOT new understanding —
it is the same understanding, correctly addressed.  Say so when reporting it.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from core.citations import (
    Citation, Hit, documented, files_mentioned, parse, rewrite,
)
from core.csharp import DECOMPILE_ROOT, CSharpSource, Method
from core.releases import BOILERPLATE, FilePair, Tree, discover

DEC = Path(__file__).resolve().parent
# The master index carries the long-form provenance section by hand; it is an
# index, not an audit, and it legitimately names more than one release.
INDEX = "AUDIT_INDEX.md"

BEGIN = ("<!-- audit-state: origin={origin} addresses={addresses}"
         " known-bad={known_bad}")
END = "<!-- /audit-state -->"
_STATE_RE = re.compile(
    r"<!-- audit-state:\s*origin=(?P<origin>[\d.]+)\s+addresses=(?P<addresses>[\d.]+)"
    r"(?:\s+known-bad=(?P<known_bad>\S*))?"
    r".*?" + re.escape(END) + r"\n*", re.S)
# A version asserted in prose.  After a rebase a doc is a hybrid — 2.0.3 prose at
# 2.1.6 addresses — so no single version in a title can be true.  The state block
# is the only place allowed to name one.
#
# Detection and removal are deliberately different patterns.  Only a parenthetical
# suffix — ``(TRCC 2.1.6 decompile)`` — can be deleted without reading the
# sentence around it; a bare mid-sentence mention is reported for a human to fix.
# One combined pattern with a trailing ``[^)]*`` ate to end-of-line whenever there
# was no closing paren, which would have silently truncated prose.
_VERSION_ANY = re.compile(r"\bTRCC (\d+\.\d+\.\d+)")
_VERSION_PAREN = re.compile(r"\s*\((?:the\s+)?TRCC \d+\.\d+\.\d+[^)\n]{0,40}\)")
# ``UCScreenLED.cs` (10,141 lines)`` — a size the doc states about its source.
_DECLARED = re.compile(r"([A-Za-z0-9_]+\.cs)`?\s*\((\d[\d,]*)\s*lines")
# A citation lands if it is within this many lines of its target: loose enough to
# survive an edit above it, tight enough that a reader arrives at the right place.
WINDOW = 8


@dataclass(frozen=True)
class State:
    """What a doc records about its own release — read from it, not guessed."""

    origin: str
    addresses: str
    known_bad: tuple[str, ...] = ()
    """Citations that were already mis-anchored before any rebase touched them.

    RECORDED, not recomputed.  Recomputing "was this already broken?" asks
    whether the citation also fails against the origin tree — and a citation
    corrupted to line 9999 fails against every tree, so it would be waved
    through as pre-existing.  A mutation test caught exactly that.  Written down
    once, the list is an explicit, reviewable exception; anything not on it is
    new breakage and fails.
    """

    @classmethod
    def read(cls, text: str) -> State | None:
        m = _STATE_RE.search(text)
        if not m:
            return None
        raw = m.group("known_bad") or ""
        return cls(m.group("origin"), m.group("addresses"),
                   tuple(x for x in raw.split(";") if x))


class Locator:
    """Where a citation points, and whether that is checkable at all.

    ONE answer, shared by the mover and the verifier.  They started with an
    answer each — the mover searching every file the doc mentions, the verifier
    only the parsed one — so the verifier condemned citations the mover had
    correctly declined to touch, and the run failed on its own output.

    It also decides what is *not* checkable. The citation parser works on text
    alone, so it will happily anchor a bullet to `myDeviceMode` or `num`: a
    backticked identifier in the right position that is a variable, not a method.
    Only the release layer knows the difference, so the demotion happens here —
    an anchor that names no method in any file the doc mentions is treated as
    unanchored rather than asserted about.
    """

    def __init__(self, source: Tree, target: Tree, also: frozenset[str]) -> None:
        self._source, self._target, self._also = source.files(), target.files(), also
        self._pairs: dict[str, FilePair] = {}
        self._types: dict[str, set[str]] = {}

    def pair(self, filename: str) -> FilePair:
        if filename not in self._pairs:
            self._pairs[filename] = FilePair.read(self._source.get(filename),
                                                  self._target.get(filename))
        return self._pairs[filename]

    def _candidates(self, hit: Hit | Citation) -> list[str]:
        if hit.kind != "method":
            return [hit.file]
        return [hit.file, *sorted(self._also - {hit.file})]

    def names(self, filename: str) -> set[str]:
        pair = self.pair(filename)
        return {m.name for m in pair.source.values()} | {m.name for m in pair.target.values()}

    def types(self, filename: str) -> set[str]:
        """Type names declared in *filename* — read from the target release."""
        if filename not in self._types:
            path = self._target.get(filename)
            self._types[filename] = (
                CSharpSource(path.read_text(errors="replace")).types() if path else set())
        return self._types[filename]

    def covers(self, filename: str, anchor: str, line: int) -> bool:
        """Does a method named *anchor* in *filename* span *line*?"""
        for (name, _), m in self.pair(filename).target.items():
            if name == anchor and m.line <= line <= m.body_line + len(m.body.splitlines()) - 1:
                return True
        return False

    def is_method(self, hit: Hit | Citation) -> bool:
        """Does this citation's anchor name a real method in a file it could be in?

        A TYPE name is not one, even though a same-named method exists: every
        class with a constructor puts its own name in ``names()``.  Prose reading
        "constructed only by `FormLCD` (`FormLCD.cs:4757`)" means the class, but
        the anchor resolves to the constructor — 35 lines at 852 — so citations
        anywhere else in the 5,000-line class were condemned as not landing.
        The text cannot say which was meant, so this declines to assert, exactly
        as it already does for a variable that is not a method at all.
        Measured: 60 of 1,088 method-kind citations across 17 docs.
        """
        if hit.kind != "method" or not hit.anchor:
            return False
        cands = self._candidates(hit)
        if any(hit.anchor in self.types(f) for f in cands):
            return False
        return any(hit.anchor in self.names(f) for f in cands)

    def find(self, hit: Hit | Citation, line: int) -> tuple[Method, Method] | None:
        """The (source, target) method pair this line sits in, if it is unchanged."""
        wanted = hit.anchor if hit.kind == "method" and hit.anchor else None
        for name in self._candidates(hit):
            if (found := self.pair(name).at(line, wanted)) is not None:
                return found
        # The citation names a method but no file has it spanning that line — it
        # may still sit inside an unnamed neighbour, which is the ordinary case
        # for a citation into the middle of a body.
        if wanted:
            for name in self._candidates(hit):
                if (found := self.pair(name).at(line)) is not None:
                    return found
        return None


class Rebase:
    """Moves a doc's citations from the release it addresses to another one."""

    def __init__(self, locator: Locator) -> None:
        self._where = locator
        self.moved = self.refused = 0

    def move(self, hit: Hit, line: int) -> int | None:
        """The line's address in the target release, or None if it must not move.

        Offset-preserving, and only for a method whose body is byte-identical:
        moving a citation into a method that CHANGED would leave a pointer that
        looks current while the prose beside it describes code that is gone —
        worse than an obviously stale number, because nothing signals it.
        """
        found = self._where.find(hit, line)
        if found is None:
            self.refused += 1
            return None
        source, target = found
        self.moved += 1
        return target.line + (line - source.line)


def restate_sizes(text: str, tree: Tree) -> str:
    """Update the "(N lines)" a doc states about its sources to the new release."""
    files = tree.files()

    def swap(m: re.Match[str]) -> str:
        if (path := files.get(m.group(1))) is None:
            return m.group(0)
        n = len(path.read_text(errors="replace").splitlines())
        return m.group(0).replace(m.group(2), f"{n:,}")

    return _DECLARED.sub(swap, text)


def pending(doc: Path, source: Tree, target: Tree) -> list[str]:
    """Methods this doc documents whose behaviour changed and was not re-read.

    The load-bearing number: it is what a reader must NOT trust, and it shrinks
    as re-audited entries land, so it is recomputed every run rather than stored.
    """
    subjects = documented(doc)
    changed: set[str] = set()
    for name in files_mentioned(doc) - BOILERPLATE:
        if name not in source.files() or name not in target.files():
            continue
        pair = FilePair.read(source.files()[name], target.files()[name])
        changed |= set(pair.status()["changed"]) | set(pair.status()["gone"])
    return sorted(subjects & changed)


def unresolved(doc: Path, source: Tree, target: Tree,
               text: str | None = None) -> list[str]:
    """Citations that should land in the target release but do not.

    The invariant for a doc that CLAIMS to address `target`; meaningless for one
    that does not, whose citations point elsewhere by design.  Only citations
    into UNCHANGED methods are held to it — a changed method's citation is
    deliberately left at its old address and reported as pending instead.

    This is the check that turns "I verified six samples by eye" into a
    guarantee, and the one that catches a double-applied rebase: after that, a
    doc says it addresses 2.1.6 while its citations land nowhere.
    """
    where = Locator(source, target, files_mentioned(doc))
    files, cache, bad = target.files(), {}, []
    for cite in parse(doc, text):
        if not where.is_method(cite):
            continue
        candidates = [cite.file, *sorted(files_mentioned(doc) - {cite.file})]
        stale = any(cite.anchor in set(where.pair(f).status()["changed"])
                    | set(where.pair(f).status()["gone"]) for f in candidates)
        if stale:
            continue
        for name in candidates:
            if (path := files.get(name)) is None:
                continue
            if path not in cache:
                cache[path] = path.read_text(errors="replace").splitlines()
            # Two proxies for "this citation still points into its method", and a
            # citation satisfying EITHER is anchored.  The window asks whether the
            # method's name appears within WINDOW lines -- which a citation cites
            # the signature satisfies, and one pointing deep into a long body
            # cannot: `OnPaint` cited 200 lines past its signature reads as
            # unresolved though it is squarely inside.  Spanning is the truer
            # question but not sufficient alone, because a citation to a
            # signature sits one line ABOVE the body.  Measured over the corpus:
            # the union resolves 7 more citations and regresses none, since a
            # union only ever adds passes.
            if (cite.holds_in(cache[path], WINDOW)
                    or where.covers(name, cite.anchor, cite.line)):
                break
        else:
            bad.append(f"{cite.file}:{cite.line} `{cite.anchor}`")
    return bad


def block(state: State, stale: list[str], trees: dict[str, Tree]) -> str:
    """The generated state block — the only place a release is named."""
    origin, addresses = trees[state.origin], trees[state.addresses]
    if state.origin == state.addresses:
        head = f"**Audited against TRCC {origin.release}.**"
    else:
        head = (f"**Audited against TRCC {origin.release}; citations re-anchored "
                f"to TRCC {addresses.release}.**")
    if stale:
        names = ", ".join(f"`{n}`" for n in stale[:12])
        more = f" (+{len(stale) - 12} more)" if len(stale) > 12 else ""
        tail = (f"\n> {len(stale)} method(s) documented here changed in "
                f"TRCC {addresses.release} and have NOT been re-read: {names}{more}"
                f" — read those entries as TRCC {origin.release} history.")
    else:
        tail = (f"\n> Every method it documents is byte-identical in "
                f"TRCC {addresses.release}.")
    return (
        BEGIN.format(origin=state.origin, addresses=state.addresses,
                     known_bad=";".join(state.known_bad) or "none") + " -->\n"
        f"> {head}{tail}\n"
        f"> [`{INDEX}`]({INDEX}#provenance)\n"
        f"{END}\n"
    )


def stamped(text: str, body: str) -> str:
    """`text` with its state block inserted or refreshed under the title."""
    text = _STATE_RE.sub("", text)
    title, _, rest = text.partition("\n")
    return f"{title}\n\n{body}\n{rest.lstrip(chr(10))}"


def resolves(doc: Path, tree: Tree) -> tuple[int, int]:
    """(citations that land, citations testable) for one doc against one tree.

    A named method is looked for in every file the doc mentions, not only the one
    its citation parsed to: line-only citations bind to the nearest `.cs` above
    them, and in a doc covering two files that binding lands on the wrong one.
    """
    files, cache = tree.files(), {}
    also = files_mentioned(doc)
    hold = testable = 0
    for cite in parse(doc):
        if cite.kind == "bare":
            continue
        candidates = [cite.file, *sorted(also - {cite.file})] \
            if cite.kind == "method" else [cite.file]
        paths = [p for name in candidates if (p := files.get(name))]
        if not paths:
            continue
        testable += 1
        for path in paths:
            if path not in cache:
                cache[path] = path.read_text(errors="replace").splitlines()
            if cite.holds_in(cache[path], WINDOW):
                hold += 1
                break
    return hold, testable


def measure_origin(doc: Path, trees: list[Tree]) -> Tree | None:
    """Which release this doc was read from, for a doc that has no record yet.

    Preferred channel is the sizes it states about its own sources — exact
    (10,141 is 2.0.3, 10,143 is 2.1.6) and, unlike line citations, untouched by a
    rebase.  "All of them, exactly": releases share files that never changed, so
    a near-miss tree still scores, and only matching every stated size identifies
    the one that was read.

    Nine docs state no sizes, so the fallback is the citations themselves.  That
    is sound HERE and only here: this function runs for a doc with no state
    block, which is precisely a doc that has never been rebased, so its line
    numbers still address the release it was written from.  Reading origin from
    citations *after* a rebase is the bug this tool exists to prevent — which is
    why the answer is recorded the moment it is taken.
    """
    claims = {f: int(n.replace(",", "")) for f, n in _DECLARED.findall(doc.read_text())}
    exact = []
    for tree in trees:
        files = tree.files()
        comparable = {f: n for f, n in claims.items() if f in files}
        if comparable and all(
                len(files[f].read_text(errors="replace").splitlines()) == n
                for f, n in comparable.items()):
            exact.append(tree)
    if len(exact) == 1:
        return exact[0]

    scores = {t.version: resolves(doc, t) for t in trees}
    rates = {v: (h / n) for v, (h, n) in scores.items() if n >= 5}
    if not rates or max(rates.values()) < 0.5:
        return None
    best = max(rates.values())
    winners = [v for v, r in rates.items() if abs(r - best) < 1e-9]
    # A tie means the cited files did not change between releases; the oldest
    # tree that explains the citations is the one they were read from.
    return next(t for t in trees if t.version == min(winners))


def _fail_key(failure: str) -> str:
    """A stable identity for a broken citation: file + method, never the line.

    Keyed on the line number, a recorded exception stops matching itself the
    moment a rebase moves the citation — `UCScreenImage.cs:507` became `:702`
    and the doc's own known-bad entry no longer covered it.  The pair that does
    not move is which method, in which file.
    """
    head, _, rest = failure.partition(" ")
    return f"{head.rsplit(':', 1)[0]}::{rest.strip('`')}"


def docs() -> list[Path]:
    return sorted(p for p in list(DEC.glob("AUDIT_*.md")) + list(DEC.glob("BEHAVIOR_*.md"))
                  if p.name != INDEX)


def worklist(target: Tree, source: Tree) -> dict:
    """Every behaviour-bearing method a re-audit still has to read.

    Two kinds, and the second is the one an audit-shaped tool loses: methods that
    changed inside a covered file, and files **no doc mentions at all** —
    omitting those would silently drop `FormLCD`, 5,082 lines and the largest gap.
    """
    covered = {f for doc in docs() for f in files_mentioned(doc)} - BOILERPLATE
    out: dict[str, dict[str, list[str]]] = {}
    for name in sorted(covered):
        if name not in source.files() or name not in target.files():
            continue
        st = FilePair.read(source.files()[name], target.files()[name]).status()
        if any(st[k] for k in ("changed", "gone", "new")):
            out[name] = {k: sorted(set(v)) for k, v in st.items() if k != "identical"}
    for name in sorted(set(target.files()) - covered - BOILERPLATE):
        st = FilePair.read(None, target.files()[name]).status()
        if st["new"]:
            out[name] = {"unaudited": sorted(set(st["new"]))}
    return out


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Keep the audit docs honest about which C# release they describe.")
    ap.add_argument("--rebase", action="store_true",
                    help="move citations onto the current decompile and restamp")
    ap.add_argument("--check", action="store_true",
                    help="exit non-zero if a state block is stale, a version is "
                         "claimed in prose, or a citation fails to resolve")
    ap.add_argument("--worklist", type=Path, metavar="JSON",
                    help="write the methods a re-audit still has to read")
    args = ap.parse_args()

    trees = discover(DECOMPILE_ROOT.parent)
    if len(trees) < 2:
        print(f"Found {len(trees)} decompile(s) under {DECOMPILE_ROOT.parent}; "
              f"need at least two to compare.")
        return 1
    current = next((t for t in trees if t.path == DECOMPILE_ROOT), trees[-1])
    by_version = {t.version: t for t in trees}
    print("Decompiles: " + ", ".join(f"{t.label} ({t.short})" for t in trees))
    print(f"Current:    {current.label}  [core/csharp.py DECOMPILE_ROOT]\n")

    print(f"{'doc':<32}{'origin':>8}{'addresses':>11}{'moved':>7}{'pending':>9}  state")
    print("-" * 78)
    stale_blocks, claims, broken, preexisting = [], [], [], []
    totals = Counter()
    for doc in docs():
        original = doc.read_text()
        state = State.read(original)
        if state is None:
            origin = measure_origin(doc, trees)
            if origin is None:
                print(f"{doc.name:<32}{'?':>8}{'?':>11}{'—':>7}{'—':>9}  "
                      f"cannot measure origin — states no source sizes")
                continue
            # Bootstrap: the doc has never been touched, so NOW is the only
            # honest moment to record which of its citations were already
            # mis-anchored.  After a rewrite there is no "before" left to ask.
            state = State(origin.version, origin.version,
                          tuple(sorted({_fail_key(f) for f in
                                        unresolved(doc, origin, origin, original)})))

        body_text, moved = original, 0
        if args.rebase and state.addresses != current.version:
            rb = Rebase(Locator(by_version[state.addresses], current,
                                files_mentioned(doc)))
            body_text = restate_sizes(rewrite(doc, rb.move, original), current)
            moved = rb.moved
            state = State(state.origin, current.version, state.known_bad)

        stale = pending(doc, by_version[state.origin], current)
        body = block(state, stale, by_version)
        text = stamped(body_text, body)
        if args.rebase:
            text = _VERSION_PAREN.sub("", text)

        # Verify the CANDIDATE, then write.  Verifying what is already on disk is
        # how a bad rebase got persisted and had to be recovered from git.  The
        # invariant binds only a doc claiming to address the current release; one
        # still addressing an older one points elsewhere by design.  Exceptions
        # come from the RECORDED known-bad list, never recomputed.
        fails: list[str] = []
        if state.addresses == current.version:
            fails = [f for f in unresolved(doc, by_version[state.origin], current, text)
                     if _fail_key(f) not in state.known_bad]
        if fails:
            broken.append((doc.name, fails))
        elif args.rebase:
            doc.write_text(text)

        preexisting.extend((doc.name, f) for f in state.known_bad)
        if not args.rebase and body not in original:
            stale_blocks.append(doc.name)
        if bad := _VERSION_ANY.findall(_STATE_RE.sub("", doc.read_text())):
            claims.append((doc.name, sorted(set(bad))))
        totals["moved"] += moved
        totals["pending"] += len(stale)
        print(f"{doc.name:<32}{by_version[state.origin].release:>8}"
              f"{by_version[state.addresses].release:>11}{moved or '—':>7}"
              f"{len(stale) or '—':>9}  "
              f"{'re-anchored' if state.origin != state.addresses else 'as written'}")
    print("-" * 78)
    print(f"{'TOTAL':<32}{'':>8}{'':>11}{totals['moved']:>7}{totals['pending']:>9}")

    if args.worklist:
        oldest = min(trees, key=lambda t: t.version)
        data = worklist(current, oldest)
        args.worklist.write_text(json.dumps(data, indent=1))
        counts = Counter()
        for entry in data.values():
            for kind, names in entry.items():
                counts[kind] += len(names)
        print(f"\nWorklist -> {args.worklist}: "
              + ", ".join(f"{v} {k}" for k, v in counts.most_common()))

    if preexisting:
        by_doc = Counter(name for name, _ in preexisting)
        print(f"\nPRE-EXISTING  {len(preexisting)} citation(s) were already "
              f"mis-anchored before any rebase — a documentation error, not a "
              f"transform error: "
              + ", ".join(f"{d} ({n})" for d, n in by_doc.most_common()))
        for name, fail in preexisting[:6]:
            print(f"    {name}: {fail}")

    for name, fails in broken:
        print(f"\nBROKEN {name}: {len(fails)} citation(s) into unchanged methods "
              f"do not resolve in {current.label}: {', '.join(fails[:5])}")
    for name, bad in claims:
        print(f"\nCLAIM  {name}: prose names TRCC {', '.join(bad)}. Only the state "
              f"block may name a release — a re-anchored doc is a hybrid.")
    if stale_blocks:
        print(f"\nSTALE  {len(stale_blocks)} doc(s) have a missing or out-of-date "
              f"state block: {', '.join(stale_blocks)}")
    if broken:
        print("\nFAIL — citations do not resolve. This is the double-rebase "
              "signature; restore the docs from git rather than re-running.")
        return 1
    if args.check and (stale_blocks or claims):
        print("\nFAIL — run `audit_release.py --rebase`.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
