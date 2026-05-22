"""Shared fixtures for services/ tests.

Provides properly DI-wired service instances with mock adapter dependencies.
Tests request these fixtures instead of constructing services bare.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from trcc.legacy.services import DeviceService, DisplayService, MediaService, OverlayService
from trcc.legacy.services.image import ImageService


def make_device_service(**overrides) -> DeviceService:
    """Create a DeviceService with mock adapter deps (no RuntimeError)."""
    defaults = {
        'detect_fn': MagicMock(return_value=[]),
        'probe_led_fn': MagicMock(return_value=None),
        'get_protocol': MagicMock(return_value=MagicMock()),
        'get_protocol_info': MagicMock(return_value=None),
    }
    defaults.update(overrides)
    return DeviceService(**defaults)


@pytest.fixture()
def device_svc() -> DeviceService:
    """DeviceService with mock adapter deps."""
    return make_device_service()


@pytest.fixture()
def overlay_svc() -> OverlayService:
    """OverlayService at 320x320 with real renderer."""
    return OverlayService(320, 320, renderer=ImageService.renderer())


@pytest.fixture()
def media_svc() -> MediaService:
    """MediaService without decoder classes (fine unless load() is called)."""
    return MediaService()


@pytest.fixture()
def display_svc(device_svc, overlay_svc, media_svc) -> DisplayService:
    """DisplayService with mock device/media, real overlay."""
    return DisplayService(device_svc, overlay_svc, media_svc)
