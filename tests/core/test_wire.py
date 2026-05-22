"""Wire envelope + Renderer.encode_for_wire round-trip tests (10C.4).

Exercises:
- ``QtRenderer.encode_for_wire`` / ``decode_from_wire`` PNG round-trip.
- ``trcc.core.wire.wrap_surface`` / ``unwrap_surface`` envelope shape.
- ``IPCServer._sanitize_payload`` — Topic.FRAME path with + without renderer.
- ``EventBusProxy._desanitize_payload`` — symmetric reconstruction.

Together these prove a ``Topic.FRAME`` event published on the daemon
arrives at a ``TrccProxy`` callback as a usable native surface.
"""
from __future__ import annotations

import os
import unittest
from typing import Any
from unittest.mock import MagicMock

# Headless Qt for the renderer tests.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _ensure_qapp() -> None:
    """Bring up an offscreen QApplication once for the QtRenderer tests."""
    from PySide6.QtWidgets import QApplication
    if QApplication.instance() is None:
        QApplication([])


class TestQtRendererWireRoundTrip(unittest.TestCase):
    """QtRenderer encodes a QImage to PNG bytes and back without pixel loss."""

    def setUp(self) -> None:
        _ensure_qapp()
        from trcc.legacy.adapters.render.qt import QtRenderer
        self.renderer: Any = QtRenderer()

    def test_round_trip_solid_color(self) -> None:
        original = self.renderer.create_surface(64, 32, (40, 200, 90))
        wire = self.renderer.encode_for_wire(original)
        self.assertIsInstance(wire, bytes)
        # PNG signature (89 50 4E 47 ...).  Cheap content check that we
        # didn't emit some other format silently.
        self.assertEqual(wire[:4], b"\x89PNG")

        restored = self.renderer.decode_from_wire(wire)
        self.assertEqual(self.renderer.surface_size(restored), (64, 32))

    def test_round_trip_with_alpha_preserves_transparency(self) -> None:
        # ARGB color carries alpha — verify it survives the round trip.
        original = self.renderer.create_surface(8, 8, (255, 0, 0, 128))
        original = self.renderer.convert_to_rgba(original)
        wire = self.renderer.encode_for_wire(original)
        restored = self.renderer.decode_from_wire(wire)
        # Decoded image keeps its alpha — Format_ARGB32_Premultiplied.
        self.assertTrue(restored.hasAlphaChannel())

    def test_decode_invalid_returns_safe_fallback(self) -> None:
        # Caller-error: hand decode a non-PNG byte string.
        result = self.renderer.decode_from_wire(b"not a png")
        # Doesn't crash; returns a non-null 1x1 placeholder.
        self.assertFalse(result.isNull())


# =========================================================================
# wire.py — envelope shape + helpers (no Qt-specific behaviour here)
# =========================================================================


class TestWireEnvelope(unittest.TestCase):
    """``wrap_surface``/``unwrap_surface`` produce + consume the
    ``{"__surface__": "<base64>"}`` envelope through any Renderer."""

    def test_wrap_returns_envelope_dict(self) -> None:
        from trcc.legacy.core.wire import is_surface_envelope, wrap_surface
        renderer = MagicMock()
        renderer.encode_for_wire.return_value = b"\x89PNGfake"
        env = wrap_surface(renderer, object())
        self.assertTrue(is_surface_envelope(env))
        self.assertIn('__surface__', env)
        # Base64-encoded — round-trippable to bytes.
        import base64
        self.assertEqual(base64.b64decode(env['__surface__']), b"\x89PNGfake")

    def test_unwrap_calls_decoder(self) -> None:
        from trcc.legacy.core.wire import unwrap_surface, wrap_surface
        renderer = MagicMock()
        renderer.encode_for_wire.return_value = b"raw-bytes"
        renderer.decode_from_wire.return_value = "decoded-surface-sentinel"

        env = wrap_surface(renderer, object())
        result = unwrap_surface(renderer, env)

        self.assertEqual(result, "decoded-surface-sentinel")
        renderer.decode_from_wire.assert_called_once_with(b"raw-bytes")

    def test_is_surface_envelope_rejects_other_dicts(self) -> None:
        from trcc.legacy.core.wire import is_surface_envelope
        self.assertFalse(is_surface_envelope({}))
        self.assertFalse(is_surface_envelope({'foo': 'bar'}))
        self.assertFalse(is_surface_envelope(None))
        self.assertFalse(is_surface_envelope("string"))
        self.assertFalse(is_surface_envelope([1, 2, 3]))
        self.assertTrue(is_surface_envelope({'__surface__': 'b64='}))


# =========================================================================
# IPCServer._sanitize_payload — non-FRAME passthrough + FRAME wrapping
# =========================================================================


class TestIPCServerSanitize(unittest.TestCase):

    def _server(self, renderer: Any | None) -> Any:
        from trcc.legacy.ipc import IPCServer
        return IPCServer(trcc=MagicMock(), renderer=renderer)

    def test_non_frame_topic_passes_through(self) -> None:
        # METRICS/PROGRESS/etc. payloads are already JSON-safe — no transform.
        server = self._server(renderer=None)
        from trcc.legacy.core.events import Topic
        result = server._sanitize_payload(Topic.METRICS, ({'cpu': 42},))
        self.assertEqual(result, [{'cpu': 42}])

    def test_frame_with_no_surface_passes_through(self) -> None:
        from trcc.legacy.core.events import Topic
        server = self._server(renderer=None)
        result = server._sanitize_payload(Topic.FRAME, ('/dev/sg0', None))
        self.assertEqual(result, ['/dev/sg0', None])

    def test_frame_with_surface_no_renderer_drops_surface(self) -> None:
        """Misconfigured server: warns once and replaces surface with None."""
        from trcc.legacy.core.events import Topic
        server = self._server(renderer=None)
        result = server._sanitize_payload(Topic.FRAME, ('/dev/sg0', object()))
        self.assertEqual(result, ['/dev/sg0', None])

    def test_frame_with_surface_and_renderer_wraps_envelope(self) -> None:
        from trcc.legacy.core.events import Topic
        renderer = MagicMock()
        renderer.encode_for_wire.return_value = b"\x89PNGfake"
        server = self._server(renderer=renderer)
        result = server._sanitize_payload(Topic.FRAME, ('/dev/sg0', object()))
        self.assertEqual(result[0], '/dev/sg0')
        # Index 1 is now an envelope dict.
        self.assertIsInstance(result[1], dict)
        self.assertIn('__surface__', result[1])

    def test_frame_with_renderer_raising_drops_surface_safely(self) -> None:
        from trcc.legacy.core.events import Topic
        renderer = MagicMock()
        renderer.encode_for_wire.side_effect = RuntimeError("encoder broken")
        server = self._server(renderer=renderer)
        result = server._sanitize_payload(Topic.FRAME, ('/dev/sg0', object()))
        # Failure is non-fatal: drop surface, payload still goes through.
        self.assertEqual(result, ['/dev/sg0', None])


# =========================================================================
# EventBusProxy._desanitize_payload — symmetric reconstruction
# =========================================================================


class TestEventBusProxyDesanitize(unittest.TestCase):

    def _proxy(self, renderer: Any | None) -> Any:
        from trcc.legacy.core.trcc_proxy import EventBusProxy
        return EventBusProxy(socket_path=None, timeout=1.0, renderer=renderer)

    def test_non_envelope_items_pass_through(self) -> None:
        proxy = self._proxy(renderer=MagicMock())
        result = proxy._desanitize_payload(['/dev/sg0', None, 42, 'plain'])
        self.assertEqual(result, ('/dev/sg0', None, 42, 'plain'))

    def test_envelope_items_are_unwrapped(self) -> None:
        renderer = MagicMock()
        renderer.decode_from_wire.return_value = "surface-sentinel"
        proxy = self._proxy(renderer=renderer)

        from trcc.legacy.core.wire import wrap_surface
        env = wrap_surface(MagicMock(encode_for_wire=lambda _: b"raw"), object())
        result = proxy._desanitize_payload(['/dev/sg0', env])

        self.assertEqual(result[0], '/dev/sg0')
        self.assertEqual(result[1], "surface-sentinel")

    def test_no_renderer_passes_envelope_through(self) -> None:
        """Renderer-less proxies (test fixtures) get the envelope dict
        as-is — callers that don't subscribe to FRAME aren't affected."""
        proxy = self._proxy(renderer=None)
        env = {'__surface__': 'aGVsbG8='}
        result = proxy._desanitize_payload(['/dev/sg0', env])
        self.assertEqual(result, ('/dev/sg0', env))


# =========================================================================
# End-to-end: server sanitize → wire JSON → proxy desanitize
# =========================================================================


class TestEndToEndFrameTransport(unittest.TestCase):
    """Full simulated trip: native surface → JSON line → native surface."""

    def setUp(self) -> None:
        _ensure_qapp()
        from trcc.legacy.adapters.render.qt import QtRenderer
        self.renderer: Any = QtRenderer()

    def test_qimage_survives_server_to_client_round_trip(self) -> None:
        import json

        from trcc.legacy.core.events import Topic
        from trcc.legacy.core.trcc_proxy import EventBusProxy
        from trcc.legacy.ipc import IPCServer

        original = self.renderer.create_surface(48, 24, (10, 200, 250))

        server = IPCServer(trcc=MagicMock(), renderer=self.renderer)
        server_payload = server._sanitize_payload(Topic.FRAME, ('/dev/sg0', original))
        # Critical: this is what fails today without 10C.4 — JSON.dumps
        # would raise on a raw QImage; with the envelope it succeeds.
        wire_line = json.dumps({'topic': Topic.FRAME, 'payload': server_payload})

        # ── Client side ───────────────────────────────────────────────
        msg = json.loads(wire_line)
        proxy = EventBusProxy(socket_path=None, timeout=1.0,
                              renderer=self.renderer)
        received = proxy._desanitize_payload(msg['payload'])

        self.assertEqual(received[0], '/dev/sg0')
        self.assertEqual(self.renderer.surface_size(received[1]), (48, 24))
