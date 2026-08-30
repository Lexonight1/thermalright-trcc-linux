"""Clock element resolver — time / weekday / date for overlay rendering.

Overlay elements emitted by ``_dc_reader`` with ``type: "clock"`` and
``source: "time" | "weekday" | "date"`` are resolved here against the
per-device ``DeviceSettings`` (time_format / date_format) and the global
``AppSettings.language``.

Pure stdlib — no Qt, no I/O.  ``DisplayService`` calls ``compute_clock``
once per frame; ``OverlayService`` looks up by source name.
"""
from __future__ import annotations

import functools
import logging
from datetime import datetime
from typing import Literal

from ..core.logs import per_frame

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)

ClockSource = Literal["time", "weekday", "date"]


# Weekday names per ISO 639-1 language code.  Index by ``datetime.weekday()``
# (Monday=0 … Sunday=6).  Add a language = paste a 7-element list.  Unknown
# codes fall back to the language's base subtag, then to English.
WEEKDAYS_BY_LANG: dict[str, list[str]] = {
    "en":    ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"],
    "de":    ["MO",  "DI",  "MI",  "DO",  "FR",  "SA",  "SO"],
    "fr":    ["LUN", "MAR", "MER", "JEU", "VEN", "SAM", "DIM"],
    "es":    ["LUN", "MAR", "MIÉ", "JUE", "VIE", "SÁB", "DOM"],
    "pt":    ["SEG", "TER", "QUA", "QUI", "SEX", "SÁB", "DOM"],
    "ru":    ["ПН",  "ВТ",  "СР",  "ЧТ",  "ПТ",  "СБ",  "ВС"],
    "ja":    ["月",   "火",   "水",   "木",   "金",   "土",   "日"],
    "ko":    ["월",   "화",   "수",   "목",   "금",   "토",   "일"],
    "zh":    ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"],
    "zh_TW": ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"],
}


@functools.cache
def _weekday_names(language: str) -> list[str]:
    """Resolve language → weekday name list, with fallback chain.

    Cached because the render path asks once per frame while the answer only
    changes when the user changes language.  Without it the unknown-language
    WARNING below fires at frame rate and buries the rest of the report; once
    per language it is the line that explains English weekdays on a non-English
    install.  The single caller indexes the result and never mutates it, and the
    uncached version already handed out the shared ``WEEKDAYS_BY_LANG`` list, so
    nothing about aliasing changes.
    """
    base = language.split("_", 1)[0]
    for rung, names in (("exact", WEEKDAYS_BY_LANG.get(language)),
                        (f"base subtag {base!r}", WEEKDAYS_BY_LANG.get(base))):
        if names is not None:
            log.info("_weekday_names: %r matched on %s", language, rung)
            return names
    log.warning("_weekday_names: %r is not a known language and its base "
                "subtag %r isn't either — weekdays fall back to English; "
                "known: %s", language, base, sorted(WEEKDAYS_BY_LANG))
    return WEEKDAYS_BY_LANG["en"]


def _format_time(now: datetime, time_format: Literal["12h", "24h"]) -> str:
    """Format a time of day.  12h drops the leading-zero hour (2:58 PM)."""
    if time_format == "12h":
        hour12 = now.hour % 12 or 12
        suffix = "AM" if now.hour < 12 else "PM"
        out = f"{hour12}:{now.minute:02d} {suffix}"
    else:
        out = f"{now.hour:02d}:{now.minute:02d}"
    frame_log.debug("_format_time: %s -> %r", time_format, out)
    return out


# Legacy yyyy/MM/dd pattern → strftime translation.  Order matters: longer
# tokens first so ``yyyy`` doesn't get rewritten by the ``yy`` rule.
_PATTERN_RULES: tuple[tuple[str, str], ...] = (
    ("yyyy", "%Y"),
    ("yy",   "%y"),
    ("MM",   "%m"),
    ("dd",   "%d"),
)


def _translate_date_pattern(pattern: str) -> str:
    """Convert a ``yyyy/MM/dd``-style pattern to a strftime spec."""
    result = pattern
    for token, repl in _PATTERN_RULES:
        result = result.replace(token, repl)
    frame_log.debug("_translate_date_pattern: %r -> %r", pattern, result)
    return result


# The default date format (DeviceSettings.date_format default).  A date element
# whose pattern renders the same as this is treated as "uncustomised" → the
# user's global pref wins; a different pattern is a deliberate theme choice.
_DEFAULT_DATE_FORMAT = "yyyy/MM/dd"


def is_default_date_pattern(fmt: str) -> bool:
    """True if *fmt* (a strftime spec like ``%Y/%m/%d`` OR a ``yyyy/MM/dd``-style
    pattern) renders identically to the DEFAULT date format.

    The theme reconciliation rule: a date element carrying a NON-default pattern
    (e.g. ``%m/%d``) is a deliberate design choice the renderer honours; a
    default-equivalent pattern means the theme didn't customise the date, so the
    user's global ``date_format`` preference wins — universally, every UI."""
    return _translate_date_pattern(fmt) == _translate_date_pattern(_DEFAULT_DATE_FORMAT)


def resolve_clock(
    source: ClockSource,
    *,
    time_format: Literal["12h", "24h"] = "24h",
    date_format: str = "yyyy/MM/dd",
    language: str = "en",
    now: datetime | None = None,
) -> str:
    """Resolve a single clock source to its display string."""
    frame_log.debug("resolve_clock: source=%s time_format=%s date_format=%s lang=%s",
              source, time_format, date_format, language)
    moment = now or datetime.now()
    if source == "time":
        return _format_time(moment, time_format)
    if source == "date":
        return moment.strftime(_translate_date_pattern(date_format))
    if source == "weekday":
        return _weekday_names(language)[moment.weekday()]
    raise ValueError(f"Unknown clock source: {source!r}")


def compute_clock(
    time_format: Literal["12h", "24h"] = "24h",
    date_format: str = "yyyy/MM/dd",
    language: str = "en",
    *,
    now: datetime | None = None,
) -> dict[str, str]:
    """Resolve all three clock sources at once.

    DisplayService calls this once per frame, passes the dict to
    OverlayService, and includes it in the overlay cache key so frames
    rebuild when the minute / day rolls over.
    """
    frame_log.debug("compute_clock: time_format=%s date_format=%s lang=%s",
              time_format, date_format, language)
    moment = now or datetime.now()
    return {
        "time":    resolve_clock("time",    time_format=time_format, now=moment),
        "date":    resolve_clock("date",    date_format=date_format, now=moment),
        "weekday": resolve_clock("weekday", language=language,       now=moment),
    }
