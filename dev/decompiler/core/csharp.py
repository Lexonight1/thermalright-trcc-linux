"""Read decompiled C# source — the one place in the miner that knows C# syntax.

Both miners need the same primitive: *give me the body of method X*.
``extract_device_spec`` mines byte tables out of it; ``map_branches`` mines
decision points.  Each grew its own regex, and they disagreed: ``map_branches``
anchored on the bare method name, so it matched the first CALL SITE
(``ImageToJpg`` at FormCZTV.cs:2180) instead of the definition (:2646) and
brace-matched an unrelated block — reporting "0 branches" for the encoder that
holds the whole resolution→header decision chain.  A wrong match is
indistinguishable from a clean miss, which is precisely the failure this tooling
exists to prevent.

So lookup lives here, once, anchored to a DEFINITION (access modifier + return
type + name); a call site can never match.  Pure functions over text, zero I/O —
``ports.py`` places extraction logic in ``core`` and this is that layer.

Limits, stated rather than hidden: brace matching is naive (it does not skip
braces inside string literals or comments), and a definition must carry an
access modifier.  Both hold across this decompile; neither is guaranteed for
arbitrary C#.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

# The decompile every miner reads.  ONE definition: the three readers each
# spelled this differently (one of them hardcoded /home/ignorant), so when the
# real 2.1.6 tree was re-extracted they would have drifted apart -- and the
# tree they all pointed at turned out to predate the 2.1.6 installer by four
# months, which silently invalidated every conclusion drawn from it.
# Override with ``TRCC_DECOMPILE`` to audit a different version.
DECOMPILE_ROOT = Path(
    os.environ.get("TRCC_DECOMPILE")
    or Path.home() / "Downloads/TRCC_2.1.6_decompiled"
)

@cache
def decompile_text(root: Path | None = None) -> str:
    """The WHOLE decompile as one string — every ``.cs`` in the tree, joined.

    ``ilspycmd <exe>`` emits one giant file; ``ilspycmd -p <exe>`` emits a
    project tree.  Readers that scan for *every* occurrence of a method want
    the former's semantics regardless of which form is on disk: 2.1.6 defines
    ``FormCZTVInit`` TWICE — ``TRCC.CZTV/FormCZTV.cs`` and the new
    ``TRCC.LCD/FormLCD.cs`` — and pointing such a reader at a single per-file
    ``.cs`` silently drops the second one.  Joining the tree reproduces the
    single-file form exactly, so a reader gets the same answer either way.

    ``root`` may be the project directory or an already-single-file dump;
    defaults to :data:`DECOMPILE_ROOT`.  Cached — the 2.1.6 tree is ~2.5 MB
    across 62 files and several readers want it in one run.
    """
    target = root or DECOMPILE_ROOT
    if target.is_file():
        return target.read_text(errors="replace")
    return "\n".join(p.read_text(errors="replace")
                     for p in sorted(target.rglob("*.cs")))


# An access modifier + optional return type + name — i.e. a DEFINITION, never a
# call.  The return type is optional so CONSTRUCTORS match: `public Form1()` has
# none, so requiring one made every ctor invisible, and a doc citing `- `Form1`
# (Form1.cs:272)` looked like a citation into nothing.
_SIGNATURE = r"\b(?:private|public|internal|protected)\s+(?:[\w<>\[\],\s]+?\s+)?"
_DEFINITION_RE = re.compile(rf"{_SIGNATURE}(\w+)\s*\(")


@dataclass(frozen=True)
class Method:
    """One method's brace-balanced body, located back to the decompile."""

    name: str
    body: str
    line: int            # 1-based line of the definition (provenance)
    body_line: int       # 1-based line of the body's opening brace

    def lines(self) -> list[tuple[int, str]]:
        """The body as (absolute-line-number, text) pairs."""
        return [(self.body_line + i, raw)
                for i, raw in enumerate(self.body.splitlines())]


class CSharpSource:
    """A decompiled C# file, addressable by method definition."""

    def __init__(self, text: str, name: str = "<text>") -> None:
        self._text = text
        self.name = name

    @classmethod
    def read(cls, path: Path) -> CSharpSource:
        return cls(path.read_text(errors="replace"), path.name)

    @property
    def text(self) -> str:
        return self._text

    @staticmethod
    def definition_at(line: str) -> str | None:
        """The method name if ``line`` opens a definition, else None."""
        m = _DEFINITION_RE.search(line)
        return m.group(1) if m else None

    def method_names(self) -> list[str]:
        """Every method defined in the file, in source order."""
        return _DEFINITION_RE.findall(self._text)

    def methods(self) -> list[Method]:
        """Every method with its body, in source order — overloads included.

        ``method(name)`` finds the FIRST definition of a name, which is the
        right answer when you know what you are looking for and the wrong one
        for a file that overloads (``SetMyNumeral`` has several).  Scanning
        definitions in order keeps each overload distinct, which is what
        anything comparing two releases method-by-method needs.
        """
        out: list[Method] = []
        for m in _DEFINITION_RE.finditer(self._text):
            brace = self._text.find("{", m.end() - 1)
            if brace < 0 or (end := self._close(brace)) < 0:
                continue
            out.append(Method(
                name=m.group(1),
                body=self._text[brace:end + 1],
                line=self._line_of(m.start()),
                body_line=self._line_of(brace),
            ))
        return out

    def method_at(self, line: int) -> Method | None:
        """The method whose body contains ``line`` (1-based), if any."""
        for m in self.methods():
            if m.body_line <= line <= m.body_line + m.body.count("\n"):
                return m
        return None

    def method(self, name: str) -> Method | None:
        """The named method's body, or None if it has no definition here."""
        m = re.search(rf"{_SIGNATURE}{re.escape(name)}\s*\(", self._text)
        if not m:
            return None
        brace = self._text.find("{", m.start())
        if brace < 0:
            return None
        end = self._close(brace)
        if end < 0:
            return None
        return Method(
            name=name,
            body=self._text[brace : end + 1],
            line=self._line_of(m.start()),
            body_line=self._line_of(brace),
        )

    def _close(self, brace: int) -> int:
        """Index of the ``}`` closing the block opened at ``brace``, or -1."""
        depth = 0
        for i in range(brace, len(self._text)):
            if self._text[i] == "{":
                depth += 1
            elif self._text[i] == "}":
                depth -= 1
                if depth == 0:
                    return i
        return -1

    def _line_of(self, index: int) -> int:
        return self._text.count("\n", 0, index) + 1
