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

# The C# release we port.  ONE constant -- the path below, the identity check,
# the gate and CLAUDE.md all derive from it, so "which release?" has exactly one
# answer and changing releases is a one-line edit here.
#
# It is a constant rather than a comment because a version label is prose: free
# to write and checked by nobody.  For ten weeks every miner, test and audit doc
# read a 2.0.3 tree while calling it 2.1.6 -- the installer for the real one had
# been on disk since 2026-06-05 -- and the whole oracle suite ran GREEN against
# the wrong program (measured: 163 passed).  A reporter (#224) was told his
# hardware was equally broken on Windows on the strength of that reading.
ORACLE_RELEASE = "2.1.6"
# The ``AssemblyVersion`` literal the decompile must carry -- the tree's own
# statement of what it is, as opposed to what its directory is named.  The old
# tree was NAMED 2.1.6 in the docs and said 2.0.3 inside; only this discriminates.
ORACLE_VERSION = f"{ORACLE_RELEASE}.0"

# The program we port, as the binary itself declares it (``AssemblyProduct``).
#
# The installer ships FOUR managed/native binaries and only one of them is TRCC:
# ``USBLCDNEW.dll`` declares ``AssemblyProduct("USBLCDNEW")`` and its own
# independent ``AssemblyVersion("2.3.0.0")``.  Discovery used to key a decompile
# purely on "has a Properties/AssemblyInfo.cs", so decompiling the wire component
# made it appear as a RELEASE -- "TRCC 2.3.0", a release that has never existed --
# and every release-comparison tool would then diff a component against the
# application.  A component version is data ABOUT a part, not an identity of the
# whole, which is why this discriminates on product rather than on version.
ORACLE_PRODUCT = "TRCC"

# The decompile every miner reads.  ONE definition: the three readers each
# spelled this differently (one of them hardcoded /home/ignorant), so when the
# real tree was re-extracted they would have drifted apart.
# Override with ``TRCC_DECOMPILE`` to audit a different version -- doing so is
# what ``tests/test_oracle_version.py`` exists to catch when it is not deliberate.
DECOMPILE_ROOT = Path(
    os.environ.get("TRCC_DECOMPILE")
    or Path.home() / f"Downloads/TRCC_{ORACLE_RELEASE}_decompiled"
)

_ASSEMBLY_VERSION = re.compile(r'AssemblyVersion\("([\d.]+)"\)')


def assembly_version(root: Path | None = None) -> str | None:
    """The ``AssemblyVersion`` the tree at *root* declares, or ``None``.

    The tree's identity, read from the tree -- never inferred from its directory
    name, which is exactly the inference that let a 2.0.3 decompile be cited as
    2.1.6 for ten weeks.  ``None`` means "no decompile here", which callers must
    distinguish from "the wrong one is here": absent is a skip, wrong is a fail.
    """
    target = root or DECOMPILE_ROOT
    if target.is_file():
        m = _ASSEMBLY_VERSION.search(target.read_text(errors="replace"))
        return m.group(1) if m else None
    for path in sorted(target.glob("Properties/*.cs")):
        if m := _ASSEMBLY_VERSION.search(path.read_text(errors="replace")):
            return m.group(1)
    return None


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
# Type declarations.  A class and its constructor share a name, so an anchor
# that matches one matches the other -- which is why this exists; see
# ``CSharpSource.types``.
_TYPE_RE = re.compile(
    r"^\s*(?:public|internal|private|protected|partial|sealed|abstract|static|\s)*"
    r"\b(?:class|struct|enum|interface)\s+(\w+)", re.M)


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

    def types(self) -> set[str]:
        """Every type declared here — classes, structs, enums, interfaces.

        A citation anchored to one of these is NOT checkable as a method, even
        though a same-named method exists: ``public FormLCD()`` is indexed as a
        method spanning 35 lines, while prose saying "constructed only by
        `FormLCD`" means the 5,000-line CLASS.  The anchor is genuinely
        ambiguous and the text cannot tell which was meant, so the caller
        declines to assert rather than resolving it to the constructor and
        condemning every citation elsewhere in the class.
        """
        return set(_TYPE_RE.findall(self._text))

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
