"""Clock-element resolver — pure-data tests for services/_clock.py."""
from __future__ import annotations

from datetime import datetime

import pytest

from trcc.core.i18n import LANGUAGE_NAMES, WEEKDAY_KEYS, weekdays
from trcc.core.models import DATE_FORMATS, TIME_FORMATS, format_clock
from trcc.services._clock import (
    _translate_date_pattern,
    clock_text,
    compute_clock,
)

# Reference moment: Wednesday 2026-05-20 14:58:30 (weekday=2)
_NOW = datetime(2026, 5, 20, 14, 58, 30)


def _at(moment: datetime, language: str = "en") -> dict[str, str]:
    return compute_clock(language, now=moment)


# ── format_clock: the C# patterns (UCXiTongXianShiSub.cs:248-285) ──────


def test_time_24h() -> None:
    assert format_clock(TIME_FORMATS[0], _NOW) == "14:58"


def test_time_12h_drops_the_leading_zero() -> None:
    """The C#'s ``hh:mm tt`` pads the hour ("02:58 PM"); TRCC does not, by
    his choice -- it looked unprofessional.  A two-digit hour keeps both.
    MUTATION CHECK: drop the ``%I`` replacement in ``format_clock``."""
    assert format_clock(TIME_FORMATS[1], _NOW) == "2:58 PM"
    assert format_clock(TIME_FORMATS[1], datetime(2026, 5, 20, 10, 5)) == "10:05 AM"
    assert format_clock(TIME_FORMATS[1], datetime(2026, 5, 20, 9, 7)) == "9:07 AM"


def test_time_12h_midnight_renders_as_12() -> None:
    assert format_clock(TIME_FORMATS[1], datetime(2026, 5, 20, 0, 5)) == "12:05 AM"


def test_time_12h_noon_renders_as_12_pm() -> None:
    assert format_clock(TIME_FORMATS[1], datetime(2026, 5, 20, 12, 0)) == "12:00 PM"


def test_the_meridiem_ignores_the_process_locale(monkeypatch) -> None:
    """The C# uses InvariantCulture; ``strftime``'s ``%p`` follows the locale
    and is empty in some.  A 12h clock must still say AM/PM."""
    import locale
    try:
        locale.setlocale(locale.LC_TIME, "de_DE.UTF-8")
    except locale.Error:
        pytest.skip("de_DE.UTF-8 not installed")
    try:
        assert format_clock(TIME_FORMATS[1], _NOW) == "2:58 PM"
    finally:
        locale.setlocale(locale.LC_TIME, "C")


@pytest.mark.parametrize(("index", "text"), [
    (0, "2026/05/20"), (1, "2026/05/20"), (2, "20/05/2026"),
    (3, "05/20"), (4, "20/05"),
])
def test_every_csharp_date_format(index: int, text: str) -> None:
    assert format_clock(DATE_FORMATS[index], _NOW) == text


# ── clock_text: each element in its own pattern ───────────────────────


def test_a_time_element_draws_in_its_own_pattern() -> None:
    clock = _at(_NOW)
    assert clock_text("time", TIME_FORMATS[1], clock) == "2:58 PM"
    assert clock_text("time", TIME_FORMATS[0], clock) == "14:58"


def test_a_date_element_draws_in_its_own_pattern() -> None:
    assert clock_text("date", "%d.%m.%Y", _at(_NOW)) == "20.05.2026"


@pytest.mark.parametrize(("source", "text"), [("time", "14:58"),
                                              ("date", "2026/05/20")])
def test_an_element_with_no_pattern_draws_the_default(source: str, text: str) -> None:
    """An older layout, or a 0xDD time element: no ``%`` pattern of its own."""
    assert clock_text(source, "{value}", _at(_NOW)) == text


def test_weekday_follows_the_language() -> None:
    assert clock_text("weekday", "", _at(_NOW, "de")) == "MI"


def test_weekday_unknown_falls_back_to_english() -> None:
    assert clock_text("weekday", "", _at(_NOW, "xx")) == "WED"


def test_weekday_subtag_fallback() -> None:
    # "de_AT" (Austria) not in table — should fall back to "de"
    assert clock_text("weekday", "", _at(_NOW, "de_AT")) == "MI"


def test_weekday_zh_TW_exact_match() -> None:
    # zh_TW is in the table — must use that, not fallback to zh
    assert clock_text("weekday", "", _at(_NOW, "zh_TW")) == "星期三"


def test_an_unknown_source_draws_nothing() -> None:
    assert clock_text("year", "%Y", _at(_NOW)) == ""


# ── compute_clock: the frame's minute ─────────────────────────────────


def test_compute_clock_is_the_minute_and_the_weekday() -> None:
    """To the minute: the dict is part of the overlay cache key, so seconds
    would rebuild the frame every tick."""
    assert _at(_NOW) == {"now": "2026-05-20T14:58:00", "weekday": "WED"}


def test_one_moment_serves_every_element() -> None:
    # Midnight rollover edge: every element reads the same moment, no drift.
    clock = _at(datetime(2026, 5, 20, 23, 59, 59), "fr")
    assert (clock_text("time", "%H:%M", clock), clock_text("date", "%d/%m", clock),
            clock["weekday"]) == ("23:59", "20/05", "MER")


# ── pattern translator ────────────────────────────────────────────────


def test_pattern_translator_basic() -> None:
    assert _translate_date_pattern("yyyy/MM/dd") == "%Y/%m/%d"


def test_pattern_translator_order_safe() -> None:
    # ``yyyy`` must be replaced before ``yy`` so the long token wins.
    assert _translate_date_pattern("yyyy") == "%Y"


def test_pattern_translator_yy_short() -> None:
    assert _translate_date_pattern("dd/MM/yy") == "%d/%m/%y"


# ── weekday table integrity ───────────────────────────────────────────


@pytest.mark.parametrize("lang", list(LANGUAGE_NAMES))
def test_every_language_has_seven_different_weekdays(lang: str) -> None:
    """From CLDR (``dev/tools/cldr_weekdays.py``) for all 38 -- the panel
    showed English in 28 of them.  No language's abbreviation equals the
    English one, so a day that does is a missing translation falling back.
    MUTATION CHECK: delete German's "WED" in ``TRANSLATIONS``."""
    days = weekdays(lang)
    assert len(days) == 7 and len(set(days)) == 7 and all(days)
    if lang != "en":
        assert [k for k, d in zip(WEEKDAY_KEYS, days, strict=True) if d == k] == []


@pytest.mark.parametrize(("lang", "days"), [
    ("en", "MON TUE WED THU FRI SAT SUN"), ("de", "MO DI MI DO FR SA SO"),
    ("fr", "LUN MAR MER JEU VEN SAM DIM"), ("es", "LUN MAR MIÉ JUE VIE SÁB DOM"),
    ("pt", "SEG TER QUA QUI SEX SÁB DOM"), ("ru", "ПН ВТ СР ЧТ ПТ СБ ВС"),
    ("ja", "月 火 水 木 金 土 日"), ("ko", "월 화 수 목 금 토 일"),
    ("zh", "星期一 星期二 星期三 星期四 星期五 星期六 星期日"),
    ("zh_TW", "星期一 星期二 星期三 星期四 星期五 星期六 星期日"),
])
def test_the_ten_languages_the_panel_had_are_unchanged(lang: str, days: str) -> None:
    assert weekdays(lang) == tuple(days.split())
