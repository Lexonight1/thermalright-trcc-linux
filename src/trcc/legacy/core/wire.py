"""Wire envelope for non-JSON-safe types over IPC.

Today the only payload that can't survive ``json.dumps`` is a native
rendering surface (Qt's ``QImage``).  ``Topic.FRAME`` events ship one
of those, so the IPC forwarder has to wrap the surface before sending
and the proxy has to unwrap it after reading.

The envelope shape::

    {"__surface__": "<base64-encoded PNG>"}

is intentionally narrow — exactly one well-known key, one inner value,
detection is a single ``isinstance(x, dict) and "__surface__" in x``.
Mirrors the pre-existing ``{"__bytes__": "..."}`` sanitizer the
manifold uses for raw ``bytes`` arguments.

Surface encoding/decoding goes through the ``Renderer`` port so this
module stays free of Qt imports — pure transport-layer concern.
"""
from __future__ import annotations

import base64
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .ports import Renderer


_ENVELOPE_KEY = "__surface__"


def is_surface_envelope(value: Any) -> bool:
    """True iff *value* is the wire envelope produced by :func:`wrap_surface`."""
    return isinstance(value, dict) and _ENVELOPE_KEY in value


def wrap_surface(renderer: Renderer, surface: Any) -> dict[str, str]:
    """Encode a native surface into a JSON-safe envelope dict.

    Caller must own the lifetime of *surface* — the payload is read
    immediately and the surface can be garbage collected after this
    call returns.
    """
    raw = renderer.encode_for_wire(surface)
    return {_ENVELOPE_KEY: base64.b64encode(raw).decode("ascii")}


def unwrap_surface(renderer: Renderer, envelope: dict[str, Any]) -> Any:
    """Decode an envelope dict back into a native surface.

    Raises ``KeyError`` if *envelope* doesn't carry the expected key.
    Caller is expected to gate on :func:`is_surface_envelope` first.
    """
    raw = base64.b64decode(envelope[_ENVELOPE_KEY])
    return renderer.decode_from_wire(raw)
