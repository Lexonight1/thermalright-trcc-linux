#!/usr/bin/env python3
"""Splice re-audited method entries into the docs — verifying each one first.

The re-audit is done by agents reading the decompile, and agents get line
numbers wrong. One of them placed `RGB_ADD_Device` at `Form1.cs:708`, which is
`Form1_Activated`, in a different file from the method it named. Written
straight into a doc that citation would have looked exactly like the 1,237
correct ones around it.

So nothing is written on trust. Every incoming entry must clear four checks
against the decompile and the source policy, and one that fails is REPORTED,
never silently dropped and never written:

  1. the method exists in that file at all         (agents invent names)
  2. it is defined within WINDOW lines of the cited line   (agents invent lines)
  3. the entry carries no pasted C# statement      (AUDIT_INDEX "Source policy")
  4. the bullet parses as a citation our tools can check

Applying is then deterministic: replace the existing bullet for that method,
or append under the file's section. A method documented twice in two formats is
how a doc starts disagreeing with itself.

    python3.12 dev/decompiler/audit_apply.py results.json            # dry run
    python3.12 dev/decompiler/audit_apply.py results.json --write
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from core.citations import _SUBJECT, files_mentioned
from core.csharp import DECOMPILE_ROOT, CSharpSource
from core.releases import discover

DEC = Path(__file__).resolve().parent
WINDOW = 8

# A pasted C# statement, as opposed to a described one. Same shape the repo is
# gated on; an agent that ignores the source policy gets caught here rather than
# at review time.
#
# Each alternative consumes to end of line. An earlier cut anchored `\s*$`
# directly after the access modifier, so `private void Timer_event(object
# sender, EventArgs e);` matched nothing and a pasted signature sailed through
# the check written to stop it.
_PASTED = re.compile(
    r"^\s*(?:\d+:\s*)?(?:"
    r"(?:private|public|protected|internal|static)\s+[\w<>\[\],]+\s+\w+.*|"
    r"(?:class|struct|enum|interface)\s+\w+\s*[:{].*|"
    r"(?:if|else\s+if|for|foreach|while|switch)\s*\(.*|"
    r"\w+(?:\.\w+)+\s*\([^)]*\)\s*;|"
    r"\w+\s*=\s*(?:new\s+)?[\w<>\[\]().]+.*;"
    r")\s*$")
_FENCE = re.compile(r"```[a-z]*\n(.*?)```", re.S)


@dataclass(frozen=True)
class Entry:
    """One method's re-audited bullet, and whether it may be written."""

    file: str
    method: str
    line: int
    markdown: str

    def reject(self, src: CSharpSource | None) -> str | None:
        """Why this entry must not be written, or None if it may be."""
        if src is None:
            return f"{self.file} is not in the decompile"
        defs = [m for m in src.methods() if m.name == self.method]
        if not defs:
            return f"no method named {self.method} in {self.file}"
        if not any(abs(m.line - self.line) <= WINDOW for m in defs):
            near = ", ".join(str(m.line) for m in defs[:3])
            return (f"{self.method} cited at :{self.line} but defined at {near}")
        body = _FENCE.sub("", self.markdown)
        pasted = [raw for raw in self.markdown.splitlines() if _PASTED.match(raw)]
        # Code spans by SPLITTING on backticks, not by matching pairs. A pair
        # regex with a minimum length skips a short span (`Timer_event`, 11
        # chars) and then pairs its closing backtick with the next opening one,
        # so the C# span that follows is read as delimiter rather than content —
        # a pasted signature walked straight through this check.
        pasted += [span for raw in body.splitlines()
                   for span in raw.split("`")[1::2]
                   if _PASTED.match(span.strip())]
        if pasted:
            return f"carries pasted C#: {pasted[0][:60]!r}"
        if not _SUBJECT.match(self.markdown.strip()):
            return "not a parseable `- `Method` (File.cs:LINE) — …` bullet"
        return None


# Not places an audit entry may be written: the index is hand-curated prose, and
# CONTROL_FLOW.md is regenerated from the decompile — anything spliced into it
# disappears the next time extract_control_flow.py runs, silently. UCShortcut is
# mentioned ONLY there, so without this the whole new-file batch was routed into
# a generated file and would have looked applied.
_NOT_A_TARGET = frozenset({"AUDIT_INDEX.md", "CONTROL_FLOW.md"})


def owning_doc(filename: str) -> Path | None:
    """The hand-written doc that documents this C# file, if one does."""
    owners = [d for d in sorted(DEC.glob("*.md"))
              if d.name not in _NOT_A_TARGET and filename in files_mentioned(d)]
    behaviour = [d for d in owners if d.name.startswith("BEHAVIOR_")]
    return (behaviour or owners or [None])[0]


def splice(doc: Path, entry: Entry) -> tuple[str, str]:
    """`doc` text with this method's bullet replaced, or appended. (text, how)"""
    lines = doc.read_text().splitlines(keepends=True)
    want = re.compile(
        rf"^\s*(?:[-*+]|\d+\.)\s+\**`{re.escape(entry.method)}\s*(?:\([^`]*\))?`")
    bullet = entry.markdown.rstrip() + "\n"
    for i, raw in enumerate(lines):
        if want.match(raw):
            end = i + 1
            while end < len(lines) and lines[end].startswith(("  ", "\t")) \
                    and lines[end].strip():
                end += 1                      # the bullet's continuation lines
            return "".join([*lines[:i], bullet, *lines[end:]]), "replaced"
    return "".join(lines) + ("" if lines and lines[-1].endswith("\n") else "\n") \
        + bullet, "appended"


def main() -> int:
    ap = argparse.ArgumentParser(description="Splice verified re-audit entries into the docs.")
    ap.add_argument("results", type=Path, help="workflow output JSON")
    ap.add_argument("--write", action="store_true", help="apply; otherwise dry run")
    args = ap.parse_args()

    trees = discover(DECOMPILE_ROOT.parent)
    current = next((t for t in trees if t.path == DECOMPILE_ROOT), trees[-1])
    files = current.files()
    sources: dict[str, CSharpSource | None] = {}

    # Accept the workflow's own output file, which wraps the return value in
    # `result`, as well as a bare {"batches": …} or a plain list. Reaching into
    # one shape only means hand-editing every file before it can be applied,
    # which is where transcription mistakes come from.
    payload = json.loads(args.results.read_text())
    while isinstance(payload, dict) and "batches" not in payload and "result" in payload:
        payload = payload["result"]
    batches = payload.get("batches", []) if isinstance(payload, dict) else payload
    # Agents answer with whatever they were handed — sometimes the basename we
    # asked for, sometimes the absolute path from the prompt. Both name the same
    # file, and every index here is keyed on the basename.
    entries = [Entry(Path(b["file"]).name, e["method"], int(e["line"]), e["markdown"])
               for b in batches for e in b.get("entries", [])]
    print(f"{len(entries)} entries from {len(batches)} batch(es)\n")

    applied, rejected, counts = [], [], Counter()
    for e in entries:
        if e.file not in sources:
            path = files.get(e.file)
            sources[e.file] = CSharpSource.read(path) if path else None
        if (why := e.reject(sources[e.file])) is not None:
            rejected.append((e, why))
            counts["rejected"] += 1
            continue
        doc = owning_doc(e.file)
        if doc is None:
            rejected.append((e, f"no doc documents {e.file} — create one first"))
            counts["no-doc"] += 1
            continue
        text, how = splice(doc, e)
        if args.write:
            doc.write_text(text)
        applied.append((e, doc.name, how))
        counts[how] += 1

    for e, doc, how in applied:
        print(f"  {how:<8} {doc:<30} {e.file}:{e.line} `{e.method}`")
    if rejected:
        print(f"\n{len(rejected)} REJECTED — not written:")
        for e, why in rejected:
            print(f"  {e.file}:{e.line} `{e.method}` — {why}")
    print(f"\n{dict(counts)}")
    if not args.write:
        print("Dry run — nothing written. Re-run with --write.")
    else:
        print("Now run: audit_release.py --rebase   (refresh the state blocks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
