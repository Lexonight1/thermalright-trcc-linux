#!/usr/bin/env python3
"""Generate `doc/REFERENCE_API.md` from the live FastAPI app.

The fourth generated document, for the same reason as the other three, and
with the sharpest evidence of any of them.  Measured against the running app,
the hand-written page claimed **59 routes of which 55 did not exist**, and
documented **4 of the 133** the app actually serves.  It also told readers to
connect on port 9876; the server has defaulted to 8080 for a long time.  It was
last touched before the cutover and nothing gated it, so nobody found out.

Everything here is read off the app itself: the path, the methods, the tag it
is grouped under, the response model, the path parameters, and the endpoint's
own docstring.  A route with no docstring is listed bare rather than skipped —
the page must show the whole surface, and an undocumented endpoint should be
visible as one.

Deterministic: grouped by tag, sorted by path inside each group, no timestamp.

    PYTHONPATH=src python3 dev/gen_api_reference.py           # write it
    PYTHONPATH=src python3 dev/gen_api_reference.py --check    # exit 1 if stale
"""
from __future__ import annotations

import inspect
import os
import re
import sys
from pathlib import Path

# The API composes a QtRenderer, which wants a platform plugin.  Same knob the
# daemon sets for headless rendering — set before Qt is imported.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "src"))

_DOC = _REPO / "doc" / "REFERENCE_API.md"

#: Tag -> heading.  A tag absent here still appears, under its own name: the
#: page must never omit part of the surface because nobody added a title.
_GROUPS = {
    "devices": "Devices",
    "display": "Display, themes and frames",
    "theme": "Theme library",
    "led": "LED",
    "system": "System and diagnostics",
    "config": "Preferences",
    "trcc": "Daemon control",
    "meta": "Meta",
}

_PATH_PARAM = re.compile(r"\{(\w+)\}")


def _summary(endpoint: object) -> str:
    """The endpoint docstring's first paragraph, collapsed to one line."""
    doc = (inspect.getdoc(endpoint) or "").strip()
    if not doc:
        return ""
    paragraph = doc.split("\n\n", 1)[0]
    return " ".join(p.strip() for p in paragraph.splitlines() if p.strip())


def generate() -> str:
    from fastapi.routing import APIRoute, APIWebSocketRoute

    from trcc.ui.api.main import api_routes, build_app

    rows: list[tuple[str, str, str, str, str]] = []   # tag, path, methods, resp, summary
    for route in api_routes(build_app()):
        if isinstance(route, APIRoute):
            methods = " ".join(sorted(m for m in route.methods
                                      if m not in ("HEAD", "OPTIONS")))
            response = getattr(route.response_model, "__name__", "") or ""
            tag = str(route.tags[0]) if route.tags else "(untagged)"
            rows.append((tag, route.path, methods, response, _summary(route.endpoint)))
        elif isinstance(route, APIWebSocketRoute):
            tag = "display" if "display" in route.path else "(untagged)"
            rows.append((tag, route.path, "WS", "", _summary(route.endpoint)))

    tags = sorted({t for t, *_ in rows})
    ordered = [t for t in _GROUPS if t in tags] + sorted(t for t in tags if t not in _GROUPS)

    lines = [
        "# API reference",
        "",
        "**Generated — do not edit.** "
        "`PYTHONPATH=src python3 dev/gen_api_reference.py`",
        "",
        "A REST interface to the same command bus every other UI uses. Each "
        "endpoint builds a Command, dispatches it, and returns the Result as "
        "JSON — so anything here is also reachable from the CLI, the GUI, or "
        "your own client. The Commands themselves are documented in "
        "[`REFERENCE_COMMANDS.md`](REFERENCE_COMMANDS.md).",
        "",
        f"**{len(rows)} endpoints.**",
        "",
        "## Running it",
        "",
        "```bash",
        "trcc api                              # http://127.0.0.1:8080",
        "trcc api --port 9000                  # another port",
        "trcc api --token random:32            # require X-API-Token",
        "trcc api --host 0.0.0.0 --token ...   # a public bind REQUIRES a token",
        "```",
        "",
        "Without `--token` on loopback the API is unauthenticated (dev mode). "
        "Binding any other interface without one is refused rather than "
        "allowed — an open device-control API on a LAN is not a default worth "
        "having. `--pair` prints a one-time 6-character code that a remote "
        "device exchanges for the token via `POST /pair`.",
        "",
        "Devices are addressed by **key** — the `vid:pid` string, e.g. "
        "`0402:3922` — the same identifier the CLI and the wire use. Every "
        "response carries `ok` and `message`.",
        "",
        "Interactive docs are served at `/docs` while the API is running.",
        "",
    ]

    for tag in ordered:
        group = sorted((r for r in rows if r[0] == tag), key=lambda r: (r[1], r[2]))
        lines += [f"## {_GROUPS.get(tag, tag.title())}", ""]
        lines += ["| Endpoint | Returns | Description |", "|---|---|---|"]
        for _tag, path, methods, response, summary in group:
            resp = f"`{response}`" if response else "—"
            desc = summary or "—"
            lines.append(f"| `{methods} {path}` | {resp} | {desc} |")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str]) -> int:
    content = generate()
    if "--check" in argv:
        current = _DOC.read_text() if _DOC.exists() else ""
        if current != content:
            print("stale doc/REFERENCE_API.md "
                  "(run: PYTHONPATH=src python3 dev/gen_api_reference.py)")
            return 1
        print("doc/REFERENCE_API.md current")
        return 0
    _DOC.parent.mkdir(parents=True, exist_ok=True)
    _DOC.write_text(content)
    print(f"wrote {_DOC} ({len(content.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
