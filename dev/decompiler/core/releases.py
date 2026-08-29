"""Two decompiled releases, compared — the layer that knows what changed.

`audit_provenance` reports which release a doc describes; `audit_rebase` moves
its citations onto the current one.  Both need the same two answers — what
releases are on disk, and did this method change between them — so both get them
from here.  Written separately they would have been two definitions of "changed",
and the first divergence would be silent: one tool re-anchoring a citation the
other still calls stale.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from collections import Counter
from dataclasses import dataclass, field, replace
from pathlib import Path

from core.csharp import ORACLE_PRODUCT, CSharpSource, Method

_ASSEMBLY_RE = re.compile(r'AssemblyVersion\("([\d.]+)"\)')
_PRODUCT_RE = re.compile(r'AssemblyProduct\("([^"]*)"\)')

# WinForms designer/boilerplate: generated accessors, colour pickers, combo
# boxes, scrollbars, the About page.  Not ported to Linux, so excluded from the
# behaviour-bearing denominator and from any re-audit worklist -- marked here
# once, never silently skipped in two places with two different lists.
BOILERPLATE = frozenset({
    "Resources.cs", "Program.cs", "FormStart.cs", "UCButton.cs",
    "UCScrollA.cs", "UCScrollB.cs", "UCScrollC.cs",
    "UCComboBoxA.cs", "UCComboBoxB.cs", "UCComboBoxC.cs",
    "UCColorA.cs", "UCColorB.cs", "UCColorC.cs", "FormGetColor.cs", "UCAbout.cs",
})


@dataclass(frozen=True)
class Tree:
    """One decompiled release on disk, keyed by the version it declares."""

    path: Path
    version: str
    product: str = ORACLE_PRODUCT
    companions: tuple["Tree", ...] = ()
    """Component binaries shipped in the same release — see :meth:`files`."""

    @property
    def release(self) -> str:
        """The three-component release — what a human writes: 2.0.3.0 -> 2.0.3."""
        return ".".join(self.version.split(".")[:3])

    @property
    def label(self) -> str:
        return f"TRCC {self.release}"

    @property
    def short(self) -> str:
        return self.path.name

    @property
    def home_path(self) -> str:
        """The path as a person would write it — `~`-relative when under home."""
        try:
            return f"~/{self.path.relative_to(Path.home())}"
        except ValueError:
            return str(self.path)

    def files(self) -> dict[str, Path]:
        """Every ``.cs`` in this decompile AND its companion binaries.

        A citation names a bare filename, and the application is several
        binaries: ``DCReadWriteAsync.cs`` — the whole bulk/LY/ALi wire — lives in
        the USBLCDNEW component, not in ``TRCC.exe``.  Resolving only against the
        release did not make such a citation FAIL, which would at least be
        visible; it made it UNANCHORED, so the verifier skipped it and the doc
        read green while being wholly unchecked.

        The release wins a name collision, which today means ``AssemblyInfo.cs``
        (the only name both declare).  Nothing cites it, and preferring the
        release keeps the version identity reading from the program we port.
        """
        out = {p.name: p for c in self.companions for p in c.path.rglob("*.cs")}
        return out | {p.name: p for p in self.path.rglob("*.cs")}


def discover(*roots: Path) -> list[Tree]:
    """Every decompiled RELEASE of the ported program under `roots`.

    Found rather than hardcoded: a hardcoded second path is how the first tree
    came to be mislabelled, and a new release should join the comparison by
    being extracted, not by editing this file.

    A decompile of a *component* is not a release.  The installer ships several
    binaries, and each declares its own ``AssemblyVersion``: decompiling the
    wire component ``USBLCDNEW.dll`` (which says ``2.3.0.0``) previously entered
    this list as "TRCC 2.3.0" -- a release that has never existed -- because the
    only test applied was "has a Properties/AssemblyInfo.cs".  Every caller here
    compares RELEASES, so a component silently became a comparison arm.  Both
    facts come from the binary's own metadata; the product is what says which
    program it is.  Components are returned by :func:`binaries`.
    """
    found = _trees(roots, lambda product: product == ORACLE_PRODUCT)
    # Components attach to the release they shipped in.  With one release on
    # disk that is unambiguous; if two ever coexist, attribute each component by
    # the installer it was carved from (its provenance marker) rather than by
    # proximity, which is all this can see.
    companions = tuple(_trees(roots, lambda product: product != ORACLE_PRODUCT))
    return [replace(t, companions=companions) for t in found]


def binaries(*roots: Path) -> list[Tree]:
    """Every decompiled COMPONENT under `roots` -- the non-``TRCC`` binaries."""
    return _trees(roots, lambda product: product != ORACLE_PRODUCT)


def _trees(roots: tuple[Path, ...], keep: Callable[[str], bool]) -> list[Tree]:
    """Decompiles under `roots` whose declared product satisfies `keep`."""
    seen: dict[Path, Tree] = {}
    for root in roots:
        for info in sorted(root.glob("*/Properties/AssemblyInfo.cs")) + \
                sorted(root.glob("*/*/Properties/AssemblyInfo.cs")):
            tree = info.parent.parent
            if tree in seen:
                continue
            text = info.read_text(errors="replace")
            m = _ASSEMBLY_RE.search(text)
            product = pm.group(1) if (pm := _PRODUCT_RE.search(text)) else ""
            if m and keep(product):
                seen[tree] = Tree(tree, m.group(1), product)
    return sorted(seen.values(), key=lambda t: t.version)



def _norm(body: str) -> str:
    """A method body with whitespace flattened — reindentation is not a change."""
    return re.sub(r"\s+", " ", body).strip()


@dataclass
class FilePair:
    """One C# file as it exists in both releases, methods matched up.

    Matched by (name, ordinal) rather than by name alone: `SetMyNumeral` is
    overloaded, and pairing every overload with the first definition would call
    unchanged code "changed" and send a reader to re-audit nothing.
    """

    source: dict[tuple[str, int], Method] = field(default_factory=dict)
    target: dict[tuple[str, int], Method] = field(default_factory=dict)

    @staticmethod
    def _index(src: CSharpSource | None) -> dict[tuple[str, int], Method]:
        if src is None:
            return {}
        seen: Counter[str] = Counter()
        out = {}
        for m in src.methods():
            out[(m.name, seen[m.name])] = m
            seen[m.name] += 1
        return out

    @classmethod
    def read(cls, old: Path | None, new: Path | None) -> FilePair:
        return cls(cls._index(CSharpSource.read(old) if old else None),
                   cls._index(CSharpSource.read(new) if new else None))

    def at(self, line: int, name: str | None = None) -> tuple[Method, Method] | None:
        """The (source, target) pair whose SOURCE definition spans `line`.

        Spans from the SIGNATURE, not the opening brace.  This decompile is
        Allman-braced, so a doc citing a method cites the signature line — one
        line above `body_line`.  Testing the body only refused every such
        citation, which is most of them: 295 moves where 1,000+ were safe.

        `name` disambiguates when several definitions span the line.  They can:
        brace matching is naive, so a method whose body contains a brace inside a
        string literal swallows its neighbours.  Returning the first match then
        handed back a method the citation was not about, and the caller — seeing
        the wrong name — refused a move that was perfectly safe.
        """
        spanning = [(key, o) for key, o in self.source.items()
                    if o.line <= line <= o.body_line + o.body.count("\n")]
        if name:
            spanning = [kv for kv in spanning if kv[1].name == name]
        for key, o in spanning:
            n = self.target.get(key)
            return (o, n) if n and _norm(o.body) == _norm(n.body) else None
        return None

    def status(self) -> dict[str, list[str]]:
        """Documented surface split into what a re-audit must actually read."""
        out = {"identical": [], "changed": [], "gone": [], "new": []}
        for key, o in self.source.items():
            n = self.target.get(key)
            if n is None:
                out["gone"].append(o.name)
            elif _norm(o.body) == _norm(n.body):
                out["identical"].append(o.name)
            else:
                out["changed"].append(o.name)
        out["new"] = [m.name for key, m in self.target.items()
                      if key not in self.source]
        return out


