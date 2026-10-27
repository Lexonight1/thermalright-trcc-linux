"""Translation gaps -- which English strings each language still lacks, and
filling them in.

TRCC's words live in two kinds of table, both keyed by the English text, and
a language without a key falls back to English:

* the main one, ``TRANSLATIONS`` in ``src/trcc/core/i18n.py``;
* each module's ``i18n.json`` in its own folder (``src/trcc/modules/<key>/``),
  beside its manifest, so a module's words leave with it.

A module's text is its own word first, then TRCC's (``tr(..., module=KEY)``).
So a word TRCC already translates is covered for a module without a copy:
an entry identical to TRCC's is REDUNDANT (``--drop-redundant`` removes
them), and one that differs is an OVERRIDE -- the module's own wording, for
that module only.  Both are listed, so an override is visibly deliberate.

Usage::

    PYTHONPATH=src python3.12 dev/tools/i18n_gaps.py                # every gap
    PYTHONPATH=src python3.12 dev/tools/i18n_gaps.py --table main   # one table
    PYTHONPATH=src python3.12 dev/tools/i18n_gaps.py --write new.json --table main
    PYTHONPATH=src python3.12 dev/tools/i18n_gaps.py --module ~/weather   # an author's
    PYTHONPATH=src python3.12 dev/tools/i18n_gaps.py --module ~/weather --drop-redundant

``--write`` merges ``{"<lang>": {"<English>": "<translation>"}}`` into the
table, and refuses the whole file if any entry is wrong: a key English does
not have, a language TRCC does not have, an empty translation, or one that
would replace a translation already there.  The main table is edited in
place -- each new line goes at the end of its language's block -- so its
comments and layout stay exactly as they are.

Exit status: 0 when nothing is missing (or the write succeeded), 1 otherwise.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

log = logging.getLogger(__name__)

_REPO = Path(__file__).resolve().parents[2]
#: The main table's source file.
MAIN = _REPO / "src" / "trcc" / "core" / "i18n.py"
#: Where each module's folder is -- its ``i18n.json`` sits at its top.
MODULES = _REPO / "src" / "trcc" / "modules"
#: A module's table file.
MODULE_TABLE = "i18n.json"

Table = dict[str, dict[str, str]]


def languages() -> list[str]:
    """Every language TRCC offers, English included."""
    from trcc.core.i18n import LANGUAGE_NAMES

    log.debug("languages: %d", len(LANGUAGE_NAMES))
    return list(LANGUAGE_NAMES)


def tables(modules: Path = MODULES) -> dict[str, Table]:
    """Every table by name: ``"main"``, then each module's key."""
    from trcc.core.i18n import TRANSLATIONS

    found: dict[str, Table] = {"main": TRANSLATIONS}
    for path in sorted(modules.glob(f"*/{MODULE_TABLE}")):
        found[path.parent.name] = json.loads(path.read_text(encoding="utf-8"))
    log.debug("tables: %s", list(found))
    return found


def main_table() -> Table:
    """TRCC's own table -- what a module's words fall back to."""
    from trcc.core.i18n import TRANSLATIONS

    log.debug("main_table: %d language(s)", len(TRANSLATIONS))
    return TRANSLATIONS


def gaps(table: Table, reference: Table | None = None) -> dict[str, list[str]]:
    """``{language: [English keys it lacks]}`` -- only languages with a gap.
    A language with no block at all lacks every key.  A module's table passes
    TRCC's as *reference*: a word TRCC has in that language is covered."""
    english = list(table.get("en", {}))
    ref = reference or {}
    missing = {lang: [k for k in english if not table.get(lang, {}).get(k)
                      and not ref.get(lang, {}).get(k)]
               for lang in languages() if lang != "en"}
    found = {lang: keys for lang, keys in missing.items() if keys}
    log.debug("gaps: %d language(s) short", len(found))
    return found


def references(table: Table, reference: Table) -> tuple[dict[str, list[str]],
                                                       dict[str, list[str]]]:
    """A module's entries TRCC also has: ``(redundant, overrides)``, each
    ``{language: [keys]}`` -- the same text as TRCC's, or a different one."""
    redundant: dict[str, list[str]] = {}
    overrides: dict[str, list[str]] = {}
    for lang, entries in table.items():
        if lang == "en":
            continue
        for key, text in entries.items():
            theirs = reference.get(lang, {}).get(key)
            if theirs:
                (redundant if theirs == text else overrides).setdefault(lang, []).append(key)
    log.debug("references: %d redundant, %d override(s)",
              sum(map(len, redundant.values())), sum(map(len, overrides.values())))
    return redundant, overrides


def drop_redundant(key: str, reference: Table, modules: Path = MODULES) -> int:
    """Remove from module *key*'s ``i18n.json`` every entry identical to
    *reference*'s -- TRCC's word is used by reference; how many went.  An
    English entry goes too once no language of the module has a key it just
    dropped -- never a new word not translated yet."""
    path = modules / key / MODULE_TABLE
    table: Table = json.loads(path.read_text(encoding="utf-8"))
    redundant, _ = references(table, reference)
    for lang, keys in redundant.items():
        for k in keys:
            del table[lang][k]
    english = table.get("en", {})
    dropped_keys = {k for keys in redundant.values() for k in keys}
    for k in [k for k in dropped_keys & set(english)
              if not any(k in table[lang] for lang in table if lang != "en")]:
        del english[k]
    path.write_text(json.dumps(table, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    dropped = sum(map(len, redundant.values()))
    log.info("drop_redundant: %d entr(ies) from %s", dropped, path)
    return dropped


def problems(table: Table, new: Table) -> list[str]:
    """Why *new* cannot be merged into *table* -- empty when it can."""
    english, known = table.get("en", {}), set(languages())
    found: list[str] = []
    for lang, entries in new.items():
        if lang not in known:
            found.append(f"{lang}: TRCC has no such language")
            continue
        for key, text in entries.items():
            if key not in english:
                found.append(f"{lang}: {key!r} is not an English key")
            elif not isinstance(text, str) or not text.strip():
                found.append(f"{lang}: {key!r} has no translation")
            elif table.get(lang, {}).get(key) not in (None, "", text):
                found.append(f"{lang}: {key!r} is already "
                             f"{table[lang][key]!r} -- not replaced")
    log.debug("problems: %d", len(found))
    return found


def write_main(new: Table, path: Path = MAIN) -> int:
    """Add *new*'s entries to the main table's source; how many went in.
    Each goes at the end of its language's block, nothing else moves."""
    lines = path.read_text(encoding="utf-8").split("\n")
    added = 0
    for lang, entries in new.items():
        start = lines.index(f"    {lang!r}: {{")
        end = lines.index("    },", start)
        have = _block_keys(lines[start:end])
        fresh = [f"        {key!r}: {text!r}," for key, text in entries.items()
                 if key not in have]
        lines[end:end] = fresh
        added += len(fresh)
    path.write_text("\n".join(lines), encoding="utf-8")
    log.info("write_main: %d entr(ies) into %s", added, path)
    return added


def _block_keys(block: list[str]) -> set[str]:
    """The keys a language's block already holds, read from its lines."""
    import ast

    keys: set[str] = set()
    for line in block[1:]:
        text = line.strip().rstrip(",")
        if text and not text.startswith("#"):
            parsed = ast.literal_eval("{" + text + "}")
            keys.update(parsed)
    return keys


def write_module(key: str, new: Table, modules: Path = MODULES) -> int:
    """Merge *new* into module *key*'s ``i18n.json``; how many went in."""
    path = modules / key / MODULE_TABLE
    table: Table = json.loads(path.read_text(encoding="utf-8"))
    added = 0
    for lang, entries in new.items():
        block = table.setdefault(lang, {})
        for k, text in entries.items():
            added += k not in block
            block.setdefault(k, text)
    path.write_text(json.dumps(table, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    log.info("write_module: %d entr(ies) into %s", added, path)
    return added


def _report(name: str, table: Table) -> int:
    """Print one table's gaps -- for a module, what it references of TRCC's
    too; how many are missing."""
    reference = None if name == "main" else main_table()
    short = gaps(table, reference)
    total = sum(len(keys) for keys in short.values())
    print(f"{name}: {len(table.get('en', {}))} English key(s), "
          f"{total} missing across {len(short)} language(s)")
    by_key: dict[str, list[str]] = {}
    for lang, keys in short.items():
        for k in keys:
            by_key.setdefault(k, []).append(lang)
    for k, langs in by_key.items():
        print(f"  {k!r}: {len(langs)} -- {' '.join(langs)}")
    if reference is not None:
        redundant, overrides = references(table, reference)
        for label, found in (("redundant (same as TRCC's -- --drop-redundant)", redundant),
                             ("override (its own wording, this module only)", overrides)):
            for lang, keys in sorted(found.items()):
                print(f"  {lang}: {label}: {', '.join(keys)}")
    return total


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--table", help='"main" or a module key (default: every table)')
    ap.add_argument("--write", type=Path, metavar="FILE",
                    help="merge this JSON of translations into --table")
    ap.add_argument("--module", type=Path, metavar="FOLDER",
                    help="a module's folder anywhere -- an author's own")
    ap.add_argument("--drop-redundant", action="store_true",
                    help="remove the module's entries identical to TRCC's")
    args = ap.parse_args(argv)
    modules = MODULES
    if args.module:
        folder = args.module.expanduser().resolve()
        if not (folder / MODULE_TABLE).is_file():
            print(f"{folder}: no {MODULE_TABLE}")
            return 1
        modules, args.table = folder.parent, folder.name
        every = {"main": main_table(), folder.name: json.loads(
            (folder / MODULE_TABLE).read_text(encoding="utf-8"))}
    else:
        every = tables()
    if args.drop_redundant:
        if args.table in (None, "main") or args.table not in every:
            print("--drop-redundant needs --module or a module --table")
            return 1
        dropped = drop_redundant(args.table, main_table(), modules)
        print(f"{dropped} redundant entr(ies) removed from {args.table}")
        return 0
    if args.write:
        if args.table not in every:
            print(f"--write needs --table, one of: {', '.join(every)}")
            return 1
        new: Table = json.loads(args.write.read_text(encoding="utf-8"))
        if wrong := problems(every[args.table], new):
            print("nothing written:\n  " + "\n  ".join(wrong))
            return 1
        added = (write_main(new) if args.table == "main"
                 else write_module(args.table, new, modules))
        print(f"{added} translation(s) added to {args.table}")
        return 0
    chosen = {args.table: every[args.table]} if args.table else every
    return 1 if sum(_report(n, t) for n, t in chosen.items()) else 0


if __name__ == "__main__":
    sys.exit(main())
