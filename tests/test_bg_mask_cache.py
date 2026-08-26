"""BgMaskCache — the budget is in bytes, and it is actually enforced.

The cache this replaces (``VideoFrameCache``, deleted in da4be2e9) was
unbounded, so the tests that matter here are the ones about the bound: that it
holds, that it is measured in bytes rather than entries, and that exceeding it
degrades to composing every tick rather than growing.
"""
from __future__ import annotations

from trcc.services.bg_cache import BgMaskCache

KB = 1024


def _key(n: int) -> tuple[object, ...]:
    return ("theme", (320, 320), n)


def test_a_stored_surface_comes_back() -> None:
    cache = BgMaskCache(64 * KB)
    cache.put(_key(0), "surface-0", KB)

    assert cache.get(_key(0)) == "surface-0"
    assert cache.count == 1
    assert cache.nbytes == KB


def test_an_unknown_key_misses() -> None:
    cache = BgMaskCache(64 * KB)
    cache.put(_key(0), "surface-0", KB)

    assert cache.get(_key(1)) is None


def test_the_whole_cycle_is_held_when_it_fits() -> None:
    """The 320x320 case: a short video fits its budget entirely.

    This is the configuration the CPU regression was measured on — 144 frames
    at 320x320 is 59 MB, well inside the 128 MB budget, so every frame of the
    loop is a hit after the first pass.
    """
    cache = BgMaskCache(64 * KB)
    for i in range(32):
        cache.put(_key(i), f"surface-{i}", KB)

    assert cache.count == 32
    assert all(cache.get(_key(i)) == f"surface-{i}" for i in range(32))


def test_the_budget_is_bytes_not_entries() -> None:
    """Two entries can exceed a budget that holds thirty-two smaller ones.

    The distinction is the entire reason this cache exists in this shape.  A
    frame-count limit is generous exactly where the bug was reported (#264's
    1600x720 panel, 475 MB for the same frame count that costs 59 MB at
    320x320) and stingy where it was not.

    MUTATION CHECK: cap on ``len(self._entries)`` instead of ``self._nbytes``
    and this fails — both large entries are retained.
    """
    cache = BgMaskCache(64 * KB)
    cache.put(_key(0), "big-0", 40 * KB)
    cache.put(_key(1), "big-1", 40 * KB)

    assert cache.count == 1
    assert cache.nbytes == 40 * KB
    assert cache.get(_key(0)) is None
    assert cache.get(_key(1)) == "big-1"


def test_eviction_takes_the_least_recently_used() -> None:
    """A frame still being asked for survives; the one that isn't goes.

    MUTATION CHECK: drop the ``move_to_end`` in ``get`` and this fails —
    entry 0 is evicted despite being the one in use.
    """
    cache = BgMaskCache(3 * KB)
    for i in range(3):
        cache.put(_key(i), f"surface-{i}", KB)

    cache.get(_key(0))                     # 0 becomes most-recent, 1 is oldest
    cache.put(_key(3), "surface-3", KB)

    assert cache.get(_key(0)) == "surface-0"
    assert cache.get(_key(1)) is None


def test_membership_does_not_promote() -> None:
    """``in`` is for the hit/miss log, so it must not reorder the LRU.

    Observing a cache should not change what it evicts, or the transition log
    would be steering the thing it reports on.

    MUTATION CHECK: implement ``__contains__`` via ``get`` and this fails —
    asking about entry 0 saves it from eviction.
    """
    cache = BgMaskCache(2 * KB)
    cache.put(_key(0), "surface-0", KB)
    cache.put(_key(1), "surface-1", KB)

    assert _key(0) in cache
    cache.put(_key(2), "surface-2", KB)

    assert cache.get(_key(0)) is None


def test_re_putting_a_key_does_not_double_count_its_bytes() -> None:
    cache = BgMaskCache(64 * KB)
    cache.put(_key(0), "surface-0", 10 * KB)
    cache.put(_key(0), "surface-0-again", 10 * KB)

    assert cache.count == 1
    assert cache.nbytes == 10 * KB
    assert cache.get(_key(0)) == "surface-0-again"


def test_a_surface_bigger_than_the_budget_is_not_stored() -> None:
    """One frame must not evict everything else to hold itself.

    A panel whose single composed frame exceeds the whole budget keeps
    composing every tick — the caller already holds the surface it just
    built, so nothing is lost by declining to keep it.
    """
    cache = BgMaskCache(4 * KB)
    cache.put(_key(0), "surface-0", KB)
    cache.put(_key(1), "enormous", 8 * KB)

    assert cache.get(_key(1)) is None
    assert cache.get(_key(0)) == "surface-0"
    assert cache.nbytes == KB


def test_a_working_set_that_cannot_fit_is_declined_whole() -> None:
    """Part-caching a cycle larger than the budget buys nothing, so don't.

    LRU against a cyclic access pattern longer than the cache scores a flat
    zero hit rate — every entry is evicted exactly one lap before it is
    wanted.  Measured on #264's panel: 123 MB retained, 0.0 ms/tick saved.
    The caller says how big the cycle is; a cycle that cannot fit is
    declined entirely rather than paid for.

    MUTATION CHECK: ignore ``working_set_bytes`` and this fails — the cache
    fills to its cap holding frames it will never be asked for in time.
    """
    cache = BgMaskCache(4 * KB)
    for i in range(16):
        cache.put(_key(i), f"surface-{i}", KB, working_set_bytes=16 * KB)

    assert cache.count == 0
    assert cache.nbytes == 0


def test_a_working_set_that_fits_is_cached() -> None:
    """The other side of the rule — a cycle inside the budget is served.

    This is the 320x320 case: 144 frames at 59 MB against a 128 MB budget.
    """
    cache = BgMaskCache(4 * KB)
    for i in range(4):
        cache.put(_key(i), f"surface-{i}", KB, working_set_bytes=4 * KB)

    assert cache.count == 4
    assert all(cache.get(_key(i)) == f"surface-{i}" for i in range(4))


def test_a_static_theme_is_cached_regardless() -> None:
    """A single surface is its own working set — the common non-video path."""
    cache = BgMaskCache(4 * KB)
    cache.put(_key(0), "static", KB, working_set_bytes=KB)

    assert cache.get(_key(0)) == "static"


def test_clear_empties_the_budget() -> None:
    cache = BgMaskCache(64 * KB)
    for i in range(4):
        cache.put(_key(i), f"surface-{i}", KB)
    cache.clear()

    assert cache.count == 0
    assert cache.nbytes == 0
    assert cache.get(_key(0)) is None


def test_the_budget_holds_under_a_long_animation() -> None:
    """#264's shape: far more frames than fit, retained bytes stay flat.

    897 frames at 1600x720 is 3,964 MB if nothing evicts — the number that
    made da4be2e9 delete the previous cache rather than bound it.  What
    matters is not which frames survive but that the total never walks past
    the cap.
    """
    cache = BgMaskCache(30 * KB)
    for i in range(897):
        cache.put(_key(i), f"surface-{i}", KB)
        assert cache.nbytes <= 30 * KB

    assert cache.nbytes == 30 * KB
    assert cache.count == 30
