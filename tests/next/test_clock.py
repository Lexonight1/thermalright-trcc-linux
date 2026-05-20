"""Clock-element resolver — pure-data tests for services/_clock.py."""
from __future__ import annotations

from datetime import datetime

import pytest

from trcc.next.services._clock import (
    WEEKDAYS_BY_LANG,
    _translate_date_pattern,
    compute_clock,
    resolve_clock,
)

# Reference moment: Wednesday 2026-05-20 14:58:30 (weekday=2)
_NOW = datetime(2026, 5, 20, 14, 58, 30)


# ── resolve_clock ─────────────────────────────────────────────────────


def test_time_24h() -> None:
    assert resolve_clock("time", time_format="24h", now=_NOW) == "14:58"


def test_time_12h_strips_leading_zero_and_appends_pm() -> None:
    assert resolve_clock("time", time_format="12h", now=_NOW) == "2:58 PM"


def test_time_12h_midnight_renders_as_12() -> None:
    midnight = datetime(2026, 5, 20, 0, 5)
    assert resolve_clock("time", time_format="12h", now=midnight) == "12:05 AM"


def test_time_12h_noon_renders_as_12_pm() -> None:
    noon = datetime(2026, 5, 20, 12, 0)
    assert resolve_clock("time", time_format="12h", now=noon) == "12:00 PM"


def test_date_default_pattern() -> None:
    assert resolve_clock("date", date_format="yyyy/MM/dd", now=_NOW) == "2026/05/20"


def test_date_alternative_pattern() -> None:
    assert resolve_clock("date", date_format="dd/MM/yyyy", now=_NOW) == "20/05/2026"


def test_date_short_pattern() -> None:
    assert resolve_clock("date", date_format="MM/dd", now=_NOW) == "05/20"


def test_weekday_english_default() -> None:
    # Wednesday → index 2 → "WED"
    assert resolve_clock("weekday", language="en", now=_NOW) == "WED"


def test_weekday_german() -> None:
    assert resolve_clock("weekday", language="de", now=_NOW) == "MI"


def test_weekday_unknown_falls_back_to_english() -> None:
    assert resolve_clock("weekday", language="xx", now=_NOW) == "WED"


def test_weekday_subtag_fallback() -> None:
    # "de_AT" (Austria) not in table — should fall back to "de"
    assert resolve_clock("weekday", language="de_AT", now=_NOW) == "MI"


def test_weekday_zh_TW_exact_match() -> None:
    # zh_TW is in the table — must use that, not fallback to zh
    assert resolve_clock("weekday", language="zh_TW", now=_NOW) == "星期三"


def test_unknown_source_raises() -> None:
    with pytest.raises(ValueError, match="Unknown clock source"):
        resolve_clock("year", now=_NOW)  # type: ignore[arg-type]


# ── compute_clock (one-shot dict) ─────────────────────────────────────


def test_compute_clock_returns_all_three() -> None:
    result = compute_clock("24h", "yyyy/MM/dd", "en", now=_NOW)
    assert result == {
        "time": "14:58",
        "date": "2026/05/20",
        "weekday": "WED",
    }


def test_compute_clock_uses_same_moment_for_all_three() -> None:
    # Midnight rollover edge: 23:59:59.9 vs 00:00:00 — verifying
    # all three sources share the same datetime, so no drift.
    moment = datetime(2026, 5, 20, 23, 59, 59)
    result = compute_clock("24h", "dd/MM/yyyy", "fr", now=moment)
    assert result == {
        "time": "23:59",
        "date": "20/05/2026",
        "weekday": "MER",
    }


# ── pattern translator ────────────────────────────────────────────────


def test_pattern_translator_basic() -> None:
    assert _translate_date_pattern("yyyy/MM/dd") == "%Y/%m/%d"


def test_pattern_translator_order_safe() -> None:
    # ``yyyy`` must be replaced before ``yy`` so the long token wins.
    assert _translate_date_pattern("yyyy") == "%Y"


def test_pattern_translator_yy_short() -> None:
    assert _translate_date_pattern("dd/MM/yy") == "%d/%m/%y"


# ── weekday table integrity ───────────────────────────────────────────


def test_weekday_table_all_have_seven_entries() -> None:
    for lang, names in WEEKDAYS_BY_LANG.items():
        assert len(names) == 7, f"{lang!r} has {len(names)} entries, expected 7"


def test_weekday_table_has_english() -> None:
    assert "en" in WEEKDAYS_BY_LANG
