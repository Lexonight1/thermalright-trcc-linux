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
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from core.csharp import CSharpSource, Method

_ASSEMBLY_RE = re.compile(r'AssemblyVersion\("([\d.]+)"\)')

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
        return {p.name: p for p in self.path.rglob("*.cs")}


def discover(*roots: Path) -> list[Tree]:
    """Every decompile under `roots`, identified by its own AssemblyVersion.

    Found rather than hardcoded: a hardcoded second path is how the first tree
    came to be mislabelled, and a new release should join the comparison by
    being extracted, not by editing this file.
    """
    seen: dict[Path, Tree] = {}
    for root in roots:
        for info in sorted(root.glob("*/Properties/AssemblyInfo.cs")) + \
                sorted(root.glob("*/*/Properties/AssemblyInfo.cs")):
            tree = info.parent.parent
            if tree in seen:
                continue
            if m := _ASSEMBLY_RE.search(info.read_text(errors="replace")):
                seen[tree] = Tree(tree, m.group(1))
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


