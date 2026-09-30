"""Clock element resolver — time / weekday / date for overlay rendering.

Overlay elements with ``type: "clock"`` and ``source: "time" | "weekday" |
"date"`` are drawn here.  A time or date element draws in its OWN pattern —
the C# keeps the format per element (``myModeSub``, UCXiTongXianShiSub.cs:
248-285) and has no global one; the weekday follows ``AppSettings.language``.

Pure stdlib — no Qt, no I/O.  ``DisplayService`` calls ``compute_clock``
once per frame; ``OverlayService`` draws each element with ``clock_text``.
"""
from __future__ import annotations

import functools
import logging
from datetime import datetime

from ..core.logs import per_frame
from ..core.models import DATE_FORMATS, TIME_FORMATS, format_clock

log = logging.getLogger(__name__)
frame_log = per_frame(__name__)

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


# Legacy yyyy/MM/dd pattern → strftime translation.  Order matters: longer
# tokens first so ``yyyy`` doesn't get rewritten by the ``yy`` rule.
_PATTERN_RULES: tuple[tuple[str, str], ...] = (
    ("yyyy", "%Y"),
    ("yy",   "%y"),
    ("MM",   "%m"),
    ("dd",   "%d"),
)


def icu_date_pattern(pattern: str) -> str:
    """A strftime date pattern in the ``yyyy/MM/dd`` tokens UIs show and take.

    The reverse of :func:`_translate_date_pattern`, from the same table, so
    what a UI is shown it can send back unchanged.
    """
    result = pattern
    for token, strf in _PATTERN_RULES:
        result = result.replace(strf, token)
    frame_log.debug("icu_date_pattern: %r -> %r", pattern, result)
    return result


def _translate_date_pattern(pattern: str) -> str:
    """Convert a ``yyyy/MM/dd``-style pattern to a strftime spec."""
    result = pattern
    for token, repl in _PATTERN_RULES:
        result = result.replace(token, repl)
    frame_log.debug("_translate_date_pattern: %r -> %r", pattern, result)
    return result


#: What an element with no pattern of its own draws — an older layout, or a
#: 0xDD time element read before its ``mode_sub`` was.
_DEFAULT_PATTERN: dict[str, str] = {"time": TIME_FORMATS[0], "date": DATE_FORMATS[0]}


def clock_text(source: str, pattern: str, clock: dict[str, str]) -> str:
    """What one clock element draws, at the frame's moment.

    *clock* is ``compute_clock``'s dict.  ``""`` for a source this does not
    know, which the caller reports.
    """
    if source == "weekday":
        return clock.get("weekday", "")
    if source not in _DEFAULT_PATTERN or "now" not in clock:
        frame_log.debug("clock_text: %r unresolved (keys %s)", source, list(clock))
        return ""
    return format_clock(pattern if "%" in pattern else _DEFAULT_PATTERN[source],
                        datetime.fromisoformat(clock["now"]))


def compute_clock(
    language: str = "en",
    *,
    now: datetime | None = None,
) -> dict[str, str]:
    """The frame's moment, to the minute, and the weekday in *language*.

    DisplayService calls this once per frame, passes the dict to
    OverlayService, and includes it in the overlay cache key — so the frame
    rebuilds when the minute or the day rolls over, and not more often.
    """
    moment = (now or datetime.now()).replace(second=0, microsecond=0)
    frame_log.debug("compute_clock: %s lang=%s", moment, language)
    return {
        "now": moment.isoformat(),
        "weekday": _weekday_names(language)[moment.weekday()],
    }
