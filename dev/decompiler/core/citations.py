"""Read line citations out of the audit docs — the one place that knows how we
cite C#.

Four citation styles grew organically across the 21 `AUDIT_*.md`/`BEHAVIOR_*.md`
files, and the coverage meter only understood two of them.  So
``AUDIT_LED_CORE.md`` (358 lines of line-prefixed code fences) and
``AUDIT_VIDEO.md`` (prose ``(line 311)``) scored **zero** citations and their
methods read as DARK — a formatting accident reported as an audit gap.  One
parser, all four styles, used by every reader.

The valuable part is not the line number but the **anchor**: what the doc claims
is *at* that line.  Two of the styles carry one —

* ``method`` — the doc names the method: ``- `FormInitDc` (`:135-209`) kills …``
* ``quote``  — the doc quotes the statement verbatim in a fence: ``4361:  delegateForm?.Invoke(…)``

An anchored citation is self-checking: point it at any decompile and ask whether
the claim holds there.  That is what turns "which version is this doc?" from an
argument into a measurement (``audit_provenance.py``), and it needs no reference
tree to compare against — the doc is its own reference.

Unanchored (``bare``) citations still count for coverage; they just cannot vote
on provenance.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Iterator

# ``Foo.cs:123`` / ``Foo.cs 123`` / ``Foo.cs L123`` — file and line together.
# ``\d{1,5}``, not ``\d{2,5}``: a two-digit floor silently DROPPED every citation
# into the first nine lines of a file.  Two existed — a class declaration at
# ``UCScreenLED.cs:8`` and the wire component's entry point at
# ``ReadWriteAsync.cs:5`` — and neither failed anything; they simply were not
# citations, so one binary could not exceed 10/11 coverage no matter what was
# written about it.  Measured before widening: exactly those two appear, and no
# other text in the corpus becomes a citation.
_EXPLICIT = re.compile(r"([A-Za-z0-9_]+\.cs)[:\sL]*?(\d{1,5})(?:\s*[-–]\s*(\d{1,5}))?")
# Any .cs mention — binds the styles that name the file in a section header.
_CSFILE = re.compile(r"([A-Za-z0-9_]+\.cs)")
# ``(`:123`)`` / `` `:123-456` `` — line only, file from the nearest .cs above.
_BARE = re.compile(r"[`(]:(\d{2,5})(?:\s*[-–]\s*(\d{2,5}))?")
# ``(line 311)`` — prose reference.
_PROSE = re.compile(r"\(line (\d{2,5})\)")
# ``4361:\t\tdelegateForm?.Invoke(…)`` — a line-numbered quote inside a fence.
_FENCED = re.compile(r"^\s*(\d{2,5}):(.*)$")
# The two shapes that actually name what a citation points at.  Anchoring is
# restricted to these.  Taking *any* backticked identifier on the line instead
# lifts whatever happens to be quoted first — in AUDIT_LED_SEGMENT that is the
# field `ledPosition1`, which was then asserted to live at six unrelated lines
# and missed in every tree, reading as drift when it was a parser artefact.  A
# false anchor is worse than none: it votes.
#
# Subject of a bullet — ``- `FormInitDc` (`:135-209`) …``, ``- **`Foo`** …``,
# ``- `ReadShareMemory(int n=0)` (`:114-120`) …``:
_SUBJECT = re.compile(r"^\s*(?:[-*+]|\d+\.)\s+\**`([A-Za-z_]\w*)\s*(?:\([^`]*\))?`")
# Appositive — the citation first, the method named after it:
# ``- `Form1.cs:319-357` — `OnPowerModeChanged`. …``
_APPOSITIVE = re.compile(r"`[A-Za-z0-9_]+\.cs:[\d\s,-]+`[^`\w]{0,6}`([A-Za-z_]\w*)")

# C# keywords and other words that appear on every other line: as an anchor they
# are satisfied by any tree, so they carry no evidence and are skipped.
_NOT_A_NAME = frozenset({
    "case", "if", "else", "for", "foreach", "while", "switch", "return", "new",
    "true", "false", "null", "this", "base", "int", "byte", "bool", "string",
    "void", "var", "public", "private", "static", "class", "default", "break",
})

_FENCE = "```"
# Shorter than this and a "quote" is a brace or a stray token that matches
# everywhere — it would vote for every tree equally, so it votes for none.
_MIN_QUOTE = 8


@dataclass(frozen=True)
class Citation:
    """One doc→C# reference: what it points at, and what it claims is there."""

    doc: str            # doc basename, e.g. "BEHAVIOR_FORMCZTV.md"
    file: str           # C# basename, e.g. "FormCZTV.cs"
    line: int
    kind: str           # "method" | "quote" | "bare"
    anchor: str = ""    # method name, or the verbatim statement; "" when bare

    @property
    def anchored(self) -> bool:
        """Can this citation be checked against a decompile on its own?"""
        return self.kind != "bare"

    def holds_in(self, lines: list[str], window: int) -> bool:
        """Is the doc's claim about this line true in this source file?

        ``window`` lines of slack either side: a citation has done its job if a
        reader following it arrives at the statement, not necessarily at the
        exact offset it had when the doc was written.
        """
        if self.line > len(lines):
            return False
        near = lines[max(0, self.line - 1 - window):self.line + window]
        if self.kind == "method":
            return any(self.anchor in raw for raw in near)
        return any(_agrees(self.anchor, raw) for raw in near)


def _normalise(text: str) -> str:
    """A quoted statement, stripped of the annotation the doc added to it."""
    return re.sub(r"\s+", " ", text.split("//")[0]).strip()


def _agrees(quoted: str, source: str) -> bool:
    """Does a doc's quote describe this source line?

    Not equality.  The audits annotate what they quote — a trailing ``// cyan``,
    a follow-on statement pulled onto one line, an elided receiver — so exact
    matching rejects faithful quotes and reports the doc as drifted.  A prefix
    relation in either direction, long enough not to fire on ``else if (``,
    accepts the annotation without accepting a coincidence.
    """
    a, b = _normalise(quoted), _normalise(source)
    if not a or not b:
        return False
    shared = a[:len(b)] if len(a) >= len(b) else b[:len(a)]
    return (a.startswith(b) or b.startswith(a)) and len(shared.replace(" ", "")) >= 12


@dataclass(frozen=True)
class Hit:
    """A citation as it sits in the doc: which numbers, at which offsets.

    ``parse`` wants the citation; ``rewrite`` wants the character offsets of the
    line numbers so it can move them.  Both come from one walk of the file --
    two walks would be two definitions of "what a citation looks like", and they
    would drift the first time a new style is added.
    """

    file: str
    line: int
    kind: str
    anchor: str
    spans: tuple[tuple[int, int], ...]   # offsets of each number, in the raw line


def _hits(raw: str, current: str | None, in_fence: bool
          ) -> tuple[list[Hit], str | None]:
    """Every citation on one doc line, plus the file binding for the next line."""
    if in_fence:
        if current and (m := _FENCED.match(raw)):
            text = m.group(2).strip()
            if len(text.replace(" ", "")) >= _MIN_QUOTE:
                return [Hit(current, int(m.group(1)), "quote", text,
                            _spans(m, 1))], current
        return [], current

    # The subject is read BEFORE `current` moves on to this line's file, so that
    # ``- `Foo` (`Bar.cs:12`)`` anchors to Foo and not to Bar.  Only the FIRST
    # citation on the line gets it: a bullet documenting one method routinely
    # cites several lines inside it (`:135-209` … then `:143-145` for a step
    # within), and the name only belongs to the first.
    m = _SUBJECT.match(raw) or _APPOSITIVE.search(raw)
    subject = m.group(1) if m and m.group(1) not in _NOT_A_NAME else ""

    out: list[Hit] = []
    explicit = list(_EXPLICIT.finditer(raw))
    for m in explicit:
        anchor = subject if subject and subject != m.group(1) else ""
        out.append(Hit(m.group(1), int(m.group(2)),
                       "method" if anchor else "bare", anchor, _spans(m, 2, 3)))
        subject = ""

    if files := _CSFILE.findall(raw):
        current = files[-1]
    if not current or explicit:
        return out, current

    for m in list(_BARE.finditer(raw)):
        out.append(Hit(current, int(m.group(1)),
                       "method" if subject else "bare", subject, _spans(m, 1, 2)))
        subject = ""
    for m in list(_PROSE.finditer(raw)):
        out.append(Hit(current, int(m.group(1)),
                       "method" if subject else "bare", subject, _spans(m, 1)))
        subject = ""
    return out, current


def _spans(m: re.Match[str], *groups: int) -> tuple[tuple[int, int], ...]:
    """Offsets of the named groups that matched — the numbers a rewrite may move.

    The caller names them because only the caller knows: group 1 is the FILE in
    ``_EXPLICIT`` and the LINE in ``_BARE``.  Inferring "numbers start at group 2"
    silently left the start of every ``(`:227-231`)`` range unmoved while moving
    its end, turning a citation into a range spanning two releases.
    """
    return tuple(m.span(g) for g in groups if m.group(g))


def walk(doc: Path, text: str | None = None) -> Iterator[tuple[str, list[Hit]]]:
    """Each line of the doc with the citations on it — the single state machine.

    ``text`` overrides what is on disk, so a proposed rewrite can be inspected
    before it is committed.  Verifying only what has already been written is how
    a bad rebase got persisted and then had to be recovered from git.
    """
    current: str | None = None
    in_fence = False
    for raw in (text if text is not None else doc.read_text()).splitlines():
        if raw.lstrip().startswith(_FENCE):
            in_fence = not in_fence
            yield raw, []
            continue
        hits, current = _hits(raw, current, in_fence)
        yield raw, hits


def parse(doc: Path, text: str | None = None) -> list[Citation]:
    """Every citation in one audit doc, in source order."""
    return [Citation(doc.name, h.file, h.line, h.kind, h.anchor)
            for _, hits in walk(doc, text) for h in hits]


def rewrite(doc: Path, move: Callable[[Hit, int], int | None],
            text: str | None = None) -> str:
    """The doc with every line number `move` accepts replaced by what it returns.

    `move` is called per number (a range moves both ends) and returns None to
    leave it alone — which is how a re-anchor refuses to touch a citation whose
    method actually changed.
    """
    out: list[str] = []
    for raw, hits in walk(doc, text):
        edits: list[tuple[int, int, str]] = []
        for hit in hits:
            for start, end in hit.spans:
                old = int(raw[start:end])
                if (new := move(hit, old)) is not None and new != old:
                    edits.append((start, end, str(new)))
        for start, end, text in sorted(edits, reverse=True):
            raw = raw[:start] + text + raw[end:]
        out.append(raw)
    return "\n".join(out) + "\n"


def documented(doc: Path) -> frozenset[str]:
    """The methods this doc claims to describe — the subject of each bullet.

    Not the same as "methods it cites": a doc cites several lines inside one
    method, and cites methods in passing that it does not document.  What a
    reader is entitled to trust is what a bullet is *about*, so that is what gets
    checked for staleness.
    """
    return frozenset(
        m.group(1) for raw in doc.read_text().splitlines()
        if (m := _SUBJECT.match(raw)) and m.group(1) not in _NOT_A_NAME
    )


def files_mentioned(doc: Path) -> frozenset[str]:
    """Every C# file the doc names anywhere — its subject matter.

    Wider than the files its citations parsed to: a doc that opens by naming two
    sources and then cites bare line numbers under one heading binds them all to
    whichever file was mentioned last.  The mentioned set is what a method-named
    citation may legitimately be checked against.
    """
    return frozenset(_CSFILE.findall(doc.read_text()))


def parse_all(docs: Iterable[Path]) -> list[Citation]:
    """Citations across many docs."""
    return [c for doc in sorted(docs) for c in parse(doc)]


def by_file(cites: Iterable[Citation]) -> dict[str, set[int]]:
    """Cited line numbers per C# basename — what the coverage meter wants."""
    out: dict[str, set[int]] = {}
    for c in cites:
        out.setdefault(c.file, set()).add(c.line)
    return out
