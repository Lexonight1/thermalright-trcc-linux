"""Tests for VideoFrameCache — lazy per-frame surface adjustment."""
from __future__ import annotations

from unittest.mock import MagicMock

from conftest import make_test_surface, surface_size

from trcc.legacy.services.video_cache import VideoFrameCache


def _make_frames(count: int = 5, w: int = 32, h: int = 32) -> list:
    """Create test native renderer surfaces (small for speed)."""
    return [make_test_surface(w, h, (i * 50, 0, 0)) for i in range(count)]


def _make_mask(w: int = 32, h: int = 32):
    """Create test RGBA mask surface."""
    return make_test_surface(w, h, (255, 255, 255, 128))


def _build(frames, *, mask=None, brightness=100):
    """Helper: build a VideoFrameCache from frames.

    Cache is now brightness-only — rotation, encode_angle, protocol/fbl are
    read fresh by `DisplayService._encode_angle()` at tick time so a rotation
    change applies on the next frame without a rebuild.
    """
    cache = VideoFrameCache()
    cache.build(
        frames=frames, mask=mask, mask_position=(0, 0),
        brightness=brightness,
    )
    return cache


class TestBuild:
    """Test full cache build at video load."""

    def test_build_creates_active_cache(self):
        cache = _build(_make_frames(5, 32, 32))
        assert cache.active

    def test_build_empty_frames_inactive(self):
        cache = _build([])
        assert not cache.active

    def test_build_no_mask_shares_references(self):
        frames = _make_frames(3, 32, 32)
        cache = _build(frames, mask=None)
        # Without mask, L2 references L1 frames directly (zero copy)
        for i in range(3):
            assert cache._masked_frames[i] is frames[i]

    def test_build_with_mask_composites(self):
        frames = _make_frames(3, 32, 32)
        mask = _make_mask(32, 32)
        cache = _build(frames, mask=mask)
        assert cache.active
        assert len(cache._masked_frames) == 3
        # Masked frames differ from originals (mask composited)
        assert bytes(cache._masked_frames[0].constBits()) != bytes(frames[0].constBits())

    def test_brightness_stored(self):
        """Cache only stores brightness — encoding params live on the device.

        Encoding params (protocol, resolution, fbl, use_jpeg, encode_angle)
        are read fresh from `device.encoding_params` + `display._encode_angle()`
        at tick time so changes apply on the next frame without a rebuild.
        """
        cache = _build(_make_frames(2), brightness=75)
        assert cache._brightness == 75


class TestAccess:
    """Test per-tick surface access."""

    def test_get_surface_returns_surface(self):
        frames = _make_frames(3, 32, 32)
        cache = _build(frames)
        surface = cache.get_surface(0)
        assert surface is not None
        assert surface_size(surface) == (32, 32)

    def test_get_surface_out_of_range(self):
        cache = VideoFrameCache()
        assert cache.get_surface(0) is None
        assert cache.get_surface(-1) is None

    def test_cache_hit_same_frame(self):
        """Accessing same frame index twice reuses cached surface."""
        frames = _make_frames(3, 32, 32)
        cache = _build(frames)
        s1 = cache.get_surface(1)
        s2 = cache.get_surface(1)
        assert s1 is s2  # Same object — no re-adjustment

    def test_different_frames_different_surfaces(self):
        """Different frame indices produce different surfaces."""
        frames = _make_frames(3, 32, 32)
        cache = _build(frames)
        s0 = cache.get_surface(0)
        s1 = cache.get_surface(1)
        # Frames have different colors (i*50 vs (i+1)*50)
        assert bytes(s0.constBits()) != bytes(s1.constBits())


class TestTextOverlay:
    """Test text overlay update (once per refresh interval)."""

    def test_no_text_by_default(self):
        cache = _build(_make_frames(3))
        assert not cache.has_text
        assert cache.text_overlay is None

    def test_update_text_overlay_stores_surface(self):
        cache = _build(_make_frames(3))
        text = make_test_surface(32, 32, (0, 0, 0, 128))
        changed = cache.update_text_overlay(text, ('key', 1))
        assert changed is True
        assert cache.has_text
        assert cache.text_overlay is text

    def test_same_key_skips_update(self):
        """Same text cache key → update_text_overlay returns False (no change)."""
        cache = _build(_make_frames(3))
        text = make_test_surface(32, 32, (0, 0, 0, 128))
        cache.update_text_overlay(text, ('key', 1))
        changed = cache.update_text_overlay(text, ('key', 1))
        assert changed is False

    def test_different_key_updates(self):
        cache = _build(_make_frames(3))
        t1 = make_test_surface(32, 32, (0, 0, 0, 128))
        t2 = make_test_surface(32, 32, (255, 0, 0, 128))
        cache.update_text_overlay(t1, ('key', 1))
        changed = cache.update_text_overlay(t2, ('key', 2))
        assert changed is True
        assert cache.text_overlay is t2

    def test_clear_text_overlay(self):
        cache = _build(_make_frames(3))
        text = make_test_surface(32, 32, (0, 0, 0, 128))
        cache.update_text_overlay(text, ('key', 1))
        cache.clear_text_overlay()
        assert not cache.has_text
        assert cache.text_overlay is None

    def test_clear_text_clears_both_raw_and_dimmed(self):
        cache = _build(_make_frames(3), brightness=50)
        text = make_test_surface(32, 32, (255, 255, 255, 255))
        cache.update_text_overlay(text, ('key', 1))
        assert cache._text_overlay_raw is text
        assert cache.text_overlay is not None
        cache.clear_text_overlay()
        assert cache._text_overlay_raw is None
        assert cache.text_overlay is None

    def test_text_overlay_passthrough_at_brightness_100(self):
        """At brightness >= 100, dimmed surface IS the raw — zero copy."""
        cache = _build(_make_frames(3), brightness=100)
        text = make_test_surface(32, 32, (255, 255, 255, 255))
        cache.update_text_overlay(text, ('key', 1))
        assert cache.text_overlay is text  # identity, not equality

    def test_text_overlay_dimmed_to_match_brightness(self):
        """At brightness < 100, cache stores a dimmed copy distinct from raw."""
        cache = _build(_make_frames(3), brightness=50)
        text = make_test_surface(32, 32, (255, 255, 255, 255))
        cache.update_text_overlay(text, ('key', 1))
        assert cache._text_overlay_raw is text
        assert cache.text_overlay is not None
        assert cache.text_overlay is not text  # different surface
        # Pixel byte comparison — dimmed bytes should differ from full-bright.
        assert bytes(cache.text_overlay.constBits()) != bytes(text.constBits())

    def test_brightness_change_redims_existing_text(self):
        """Setting text at full brightness then dimming should redim the cached text."""
        cache = _build(_make_frames(3), brightness=100)
        text = make_test_surface(32, 32, (255, 255, 255, 255))
        cache.update_text_overlay(text, ('key', 1))
        assert cache.text_overlay is text  # passthrough at 100

        cache.rebuild_from_brightness(50)
        assert cache._text_overlay_raw is text  # raw preserved
        assert cache.text_overlay is not text   # dimmed surface now stored
        assert bytes(cache.text_overlay.constBits()) != bytes(text.constBits())


class TestRebuild:
    """Test partial cache rebuilds (brightness / rotation)."""

    def test_rebuild_from_brightness(self):
        frames = [make_test_surface(32, 32, (255, 255, 255)) for _ in range(3)]
        cache = _build(frames, brightness=100)
        s_full = cache.get_surface(0)

        cache.rebuild_from_brightness(50)
        assert cache._brightness == 50
        # L3 cleared — next access rebuilds with dimmer surface
        s_dim = cache.get_surface(0)
        assert bytes(s_dim.constBits()) != bytes(s_full.constBits())

    def test_rebuild_from_brightness_same_value(self):
        """Rebuild at same brightness — surfaces remain consistent.

        At brightness=100 the cache returns L2 frames directly (zero copy
        optimization), so identity is preserved across rebuilds.
        """
        frames = [make_test_surface(32, 32, (200, 200, 200)) for _ in range(3)]
        cache = _build(frames, brightness=100)
        s1 = cache.get_surface(0)
        cache.rebuild_from_brightness(100)
        s2 = cache.get_surface(0)
        assert bytes(s1.constBits()) == bytes(s2.constBits())

    def test_rebuild_from_rotation_is_noop(self):
        """rebuild_from_rotation is a no-op — rotation moved to encode boundary.

        After the Observer/SSoT refactor, rotation flows through
        `DisplayService._encode_angle()` and applies at `encode_for_device`
        time.  Cache stays in source coord space — no rotation in surfaces.
        """
        cache = _build(_make_frames(3))
        s0 = cache.get_surface(0)
        cache.rebuild_from_rotation(90)
        # Same surface returned — rotation does not invalidate the cache.
        assert cache.get_surface(0) is s0


class TestInactiveCache:
    """Test inactive cache behavior."""

    def test_inactive_cache_returns_none(self):
        cache = VideoFrameCache()
        assert not cache.active
        assert cache.get_surface(0) is None

    def test_update_text_overlay_on_inactive_cache_is_safe(self):
        cache = VideoFrameCache()
        # update_text_overlay on inactive cache is safe (stores but has no frames)
        cache.update_text_overlay(MagicMock(), ('key', 1))
        assert cache.has_text
