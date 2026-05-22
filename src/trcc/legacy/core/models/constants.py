"""Domain constants — temperature conversion, display formats, locale maps."""
from __future__ import annotations

# Logging level for per-frame / per-tick lines.  Sits below DEBUG so the
# default file handler stays quiet; ``-vv`` opens the gate. The named
# binding ``logging.addLevelName(TRACE_LEVEL, 'TRACE')`` is set up by the
# logging adapter at boot time.
TRACE_LEVEL = 5


# =============================================================================
# Temperature conversion — single source of truth
# =============================================================================


def celsius_to_fahrenheit(celsius: float) -> float:
    """Convert Celsius to Fahrenheit. C#: value * 9 / 5 + 32."""
    return celsius * 9 / 5 + 32


def parse_hex_color(hex_color: str) -> tuple[int, int, int] | None:
    """Parse '#RRGGBB' or 'RRGGBB' → (r, g, b), or None on invalid input."""
    hex_color = hex_color.lstrip('#')
    if len(hex_color) != 6:
        return None
    try:
        return (int(hex_color[0:2], 16),
                int(hex_color[2:4], 16),
                int(hex_color[4:6], 16))
    except ValueError:
        return None


# =============================================================================
# Display constants
# =============================================================================

# LCD brightness button steps (percent values cycled by the GUI button)
BRIGHTNESS_STEPS: tuple[int, ...] = (25, 50, 100)
DEFAULT_BRIGHTNESS_LEVEL = 100

# JPEG encoding — max payload bytes (HID Type 2 transfer buffer is 691,200 bytes,
# leaving ~672 KB for payload; 650 KB gives safe margin at full quality 95)
JPEG_MAX_BYTES = 650_000


# Time formats matching Windows TRCC (UCXiTongXianShiSub.cs)
TIME_FORMATS: dict[int, str] = {
    0: "%H:%M",       # 24-hour (14:58)
    1: "%I:%M",       # 12-hour with leading zero (02:58) — stripped in _format_metric
    2: "%H:%M",       # 24-hour (same as mode 0 in Windows)
}

# Date formats matching Windows TRCC
DATE_FORMATS: dict[int, str] = {
    0: "%Y/%m/%d",    # 2026/01/30
    1: "%Y/%m/%d",    # 2026/01/30 (same as mode 0 in Windows)
    2: "%d/%m/%Y",    # 30/01/2026
    3: "%m/%d",       # 01/30
    4: "%d/%m",       # 30/01
}

# Weekday abbreviations per language — datetime.weekday() returns
# Monday=0, Sunday=6.  Keyed by ISO 639-1 code (matches `settings.lang`).
# Add a new language by pasting its 7-element list under its ISO code;
# format_metric() falls back to English when a language isn't here.
# Issue #141 — German users were getting English "Tue" instead of "DI".
WEEKDAYS_BY_LANG: dict[str, list[str]] = {
    'en':    ["MON",   "TUE",   "WED",   "THU",   "FRI",   "SAT",   "SUN"],
    'de':    ["MO",    "DI",    "MI",    "DO",    "FR",    "SA",    "SO"],
    'fr':    ["LUN",   "MAR",   "MER",   "JEU",   "VEN",   "SAM",   "DIM"],
    'es':    ["LUN",   "MAR",   "MIÉ",   "JUE",   "VIE",   "SÁB",   "DOM"],
    'pt':    ["SEG",   "TER",   "QUA",   "QUI",   "SEX",   "SÁB",   "DOM"],
    'ru':    ["ПН",    "ВТ",    "СР",    "ЧТ",    "ПТ",    "СБ",    "ВС"],
    'ja':    ["月",    "火",    "水",    "木",    "金",    "土",    "日"],
    'ko':    ["월",    "화",    "수",    "목",    "금",    "토",    "일"],
    'zh':    ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"],
    'zh_TW': ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"],
}

# Backwards-compat aliases — modules elsewhere still import these names.
# Both reference the same list objects as the per-lang dict.
WEEKDAYS = WEEKDAYS_BY_LANG['en']
WEEKDAYS_CN = WEEKDAYS_BY_LANG['zh']

# Legacy C# suffix → ISO 639-1 code migration map
# Used by conf.py to migrate old config.json "lang" values
LEGACY_TO_ISO: dict[str, str] = {
    '': 'zh',
    'tc': 'zh_TW',
    'd': 'de',
    'e': 'ru',
    'f': 'fr',
    'p': 'pt',
    'r': 'ja',
    'x': 'es',
    'h': 'ko',
    # These were already ISO — included for completeness
    'en': 'en',
}

# ISO 639-1 code → legacy C# asset suffix (for asset filename lookup)
# Only needed for the 10 original languages whose assets use C# suffixes
ISO_TO_LEGACY: dict[str, str] = {v: k for k, v in LEGACY_TO_ISO.items()}

# System locale prefix → ISO 639-1 language code
LOCALE_TO_LANG: dict[str, str] = {
    'zh_CN': 'zh',
    'zh_TW': 'zh_TW',
    'en': 'en',
    'de': 'de',
    'es': 'es',
    'fr': 'fr',
    'pt': 'pt',
    'ru': 'ru',
    'ja': 'ja',
    'ko': 'ko',
}


__all__ = [
    'BRIGHTNESS_STEPS',
    'DATE_FORMATS',
    'DEFAULT_BRIGHTNESS_LEVEL',
    'ISO_TO_LEGACY',
    'JPEG_MAX_BYTES',
    'LEGACY_TO_ISO',
    'LOCALE_TO_LANG',
    'TIME_FORMATS',
    'TRACE_LEVEL',
    'WEEKDAYS',
    'WEEKDAYS_BY_LANG',
    'WEEKDAYS_CN',
    'celsius_to_fahrenheit',
    'parse_hex_color',
]
