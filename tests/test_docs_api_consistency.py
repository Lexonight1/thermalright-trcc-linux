"""Docs must describe the API that exists.

Measured against the running app, the hand-written ``doc/REFERENCE_API.md``
claimed **59 routes of which 55 did not exist**, and documented **4 of the
133** the app actually serves.  It also sent readers to port 9876 when the
server has defaulted to 8080 for a long time.  It was ungated, so nothing ever
said so.

Same contract as the man pages and the CLI, port and command references: the
committed copy must match what the generator produces right now.
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "dev"))

import gen_api_reference  # noqa: E402  # pyright: ignore[reportMissingImports]

_DOC = _ROOT / "doc" / "REFERENCE_API.md"


def test_committed_api_reference_is_current() -> None:
    """The committed page matches what the generator produces right now."""
    assert _DOC.exists(), (
        "doc/REFERENCE_API.md is missing — run: "
        "PYTHONPATH=src python3 dev/gen_api_reference.py"
    )
    assert _DOC.read_text() == gen_api_reference.generate(), (
        "doc/REFERENCE_API.md is stale — run: "
        "PYTHONPATH=src python3 dev/gen_api_reference.py"
    )


def test_every_served_route_appears_on_the_page() -> None:
    """No endpoint may be missing.

    This is the assertion the old page failed 129 times over.  FastAPI's own
    ``/openapi.json``, ``/redoc``, ``/docs/oauth2-redirect`` and the static
    mount are framework furniture, not TRCC's surface, and are excluded by
    name rather than by a pattern that could quietly swallow a real route.
    """
    from trcc.ui.api.main import api_routes, build_app

    framework = {"/openapi.json", "/redoc", "/docs", "/docs/oauth2-redirect",
                 "/static/web"}
    page = _DOC.read_text()
    missing = sorted(
        path for route in api_routes(build_app())
        if (path := getattr(route, "path", None))
        and path not in framework and path not in page
    )
    assert not missing, f"served but absent from the reference: {missing}"
