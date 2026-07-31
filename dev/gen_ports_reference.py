#!/usr/bin/env python3
"""Generate `doc/REFERENCE_PORTS.md` from the live ABCs.

Same source of truth, and the same reason, as ``dev/gen_cli_reference.py``:
the hand-written table drifted. CLAUDE.md's "Other ABCs" table listed **four**
ports when the tree has **28**, and two of those four pointed at files that no
longer contain them — ``UsbTransport`` in ``adapters/device/hid.py`` (the class
does not exist; it is ``BulkTransport`` / ``ScsiTransport`` in ``core/ports.py``)
and ``SegmentDisplay`` in ``adapters/device/led_segment.py`` (it moved to
``services/``).

That is the difference between a codebase that *is* extensible and one that
*can be extended by the person who wrote it*.  Adding a cooler is two table
rows and a new wire is three methods — but nothing anywhere said so, so only
the author knew.  A contract nobody can find is not a contract.

So this derives the page from the classes themselves: every ABC, what a new
implementation must write, what it inherits for free, how it registers, and who
already implements it.  Ordered **cheapest to extend first**, so the page also
reports where the codebase is welcoming and where it is not.

Runtime introspection rather than AST parsing: inheritance is what we are
documenting, and ``__abstractmethods__`` / the MRO are the truth about it.

Deterministic — everything sorted, no timestamp — so the committed file changes
only when the ports actually change.

    PYTHONPATH=src python3 dev/gen_ports_reference.py           # write the page
    PYTHONPATH=src python3 dev/gen_ports_reference.py --check   # exit 1 if stale
"""
from __future__ import annotations

import importlib
import inspect
import pkgutil
import sys
import warnings
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "src"))

_DOC = _REPO / "doc" / "REFERENCE_PORTS.md"

# Importing these RUNS the CLI (they are entry points, by design).
_SKIP_MODULES = {"trcc.__main__", "trcc._entry"}

# How a subclass declares its key.  Only stated where it is true — inventing a
# registration idiom for a port that has none would be exactly the drift this
# page exists to stop.
_REGISTRATION = {
    "Device": 'class MyLcd(BaseDevice[BulkTransport], wire=Wire.MINE):',
    "Platform": 'class MyPlatform(BaseOS, key="myos"):',
}

# Ports whose base class supplies most of the work — point the reader at it, so
# they extend the base rather than the raw port and re-implement the shared half.
_PREFERRED_BASE = {
    "Device": "BaseDevice / BaseBulkDevice (adapters/device/_base.py)",
    "Platform": "BaseOS (adapters/system/_base.py)",
    "Paths": "BasePaths (adapters/system/_base.py) — implements all four",
}


def _import_everything() -> None:
    """Import every trcc module so subclass registries are complete."""
    warnings.filterwarnings("ignore")
    import trcc

    for mod in pkgutil.walk_packages(trcc.__path__, "trcc."):
        if mod.name in _SKIP_MODULES:
            continue
        try:
            importlib.import_module(mod.name)
        except Exception:
            continue


def _all_classes() -> list[type]:
    import gc

    return [
        obj for obj in gc.get_objects()
        if inspect.isclass(obj)
        and getattr(obj, "__module__", "").startswith("trcc.")
    ]


def _signature(cls: type, name: str) -> str:
    """`name(args) -> Return`, or just the name if it cannot be resolved."""
    member = getattr(cls, name, None)
    if isinstance(member, property):
        member = member.fget
    if not callable(member):
        return name
    try:
        sig = inspect.signature(member)
    except (ValueError, TypeError):
        return name
    params = [str(p) for n, p in sig.parameters.items() if n != "self"]
    ret = sig.return_annotation
    if ret is inspect.Signature.empty:
        tail = ""
    else:
        tail = f" -> {ret if isinstance(ret, str) else getattr(ret, '__name__', str(ret))}"
    return f"{name}({', '.join(params)}){tail}"


def _inherited(cls: type) -> list[str]:
    """Concrete members the ABC already provides — what you do NOT write."""
    out: list[str] = []
    for name, member in vars(cls).items():
        if name.startswith("_") or name in cls.__abstractmethods__:
            continue
        if callable(member) or isinstance(member, property):
            out.append(name)
    return sorted(out)


def _rel(cls: type) -> str:
    return cls.__module__.replace("trcc.", "").replace(".", "/") + ".py"


def generate() -> str:
    _import_everything()
    everything = _all_classes()
    abcs = sorted(
        {c for c in everything if inspect.isabstract(c)},
        key=lambda c: (len(c.__abstractmethods__), c.__name__),
    )

    lines = [
        "# Port reference",
        "",
        "**Generated — do not edit.** "
        "`PYTHONPATH=src python3 dev/gen_ports_reference.py`",
        "",
        "Every abstract contract in the tree: what a new implementation must "
        "write, what it inherits for free, and who already implements it.",
        "",
        "Ordered **cheapest to extend first** — the ports at the top are where "
        "this codebase welcomes a contributor, the ones at the bottom are where "
        "it does not yet.",
        "",
        f"{len(abcs)} ports.",
        "",
        "| port | implement | inherit | implementations |",
        "|---|---|---|---|",
    ]

    for cls in abcs:
        impls = sorted(
            {c.__name__ for c in everything
             if not inspect.isabstract(c) and issubclass(c, cls) and c is not cls},
        )
        lines.append(
            f"| [`{cls.__name__}`](#{cls.__name__.lower()}) "
            f"| {len(cls.__abstractmethods__)} "
            f"| {len(_inherited(cls))} "
            f"| {len(impls)} |",
        )

    lines += ["", "---", ""]

    for cls in abcs:
        impls = sorted(
            {c.__name__ for c in everything
             if not inspect.isabstract(c) and issubclass(c, cls) and c is not cls},
        )
        lines += [f"## {cls.__name__}", "", f"`{_rel(cls)}`", ""]

        doc = inspect.getdoc(cls)
        if doc:
            lines += [doc.split("\n\n")[0].replace("\n", " "), ""]

        if base := _PREFERRED_BASE.get(cls.__name__):
            lines += [f"**Extend `{base}`**, not this port directly — "
                      "it already implements the shared half.", ""]

        if reg := _REGISTRATION.get(cls.__name__):
            lines += ["**Register by naming your key in the class line:**", "",
                      "```python", reg, "```", ""]

        if cls.__abstractmethods__:
            lines += [f"**You implement ({len(cls.__abstractmethods__)}):**", "",
                      "```python"]
            lines += [f"{_signature(cls, m)}" for m in sorted(cls.__abstractmethods__)]
            lines += ["```", ""]
        else:
            lines += ["**You implement:** nothing — every member has a default.", ""]

        if inherited := _inherited(cls):
            lines += [f"**You inherit ({len(inherited)}):** "
                      + " · ".join(f"`{m}`" for m in inherited), ""]

        lines += [f"**Implementations ({len(impls)}):** "
                  + (" · ".join(f"`{i}`" for i in impls) if impls else "_none yet_"),
                  ""]

    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    content = generate()
    if "--check" in argv:
        current = _DOC.read_text() if _DOC.exists() else ""
        if current != content:
            print("stale doc/REFERENCE_PORTS.md "
                  "(run: PYTHONPATH=src python3 dev/gen_ports_reference.py)")
            return 1
        print("doc/REFERENCE_PORTS.md current")
        return 0
    _DOC.parent.mkdir(parents=True, exist_ok=True)
    _DOC.write_text(content)
    print(f"wrote {_DOC} ({len(content.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
