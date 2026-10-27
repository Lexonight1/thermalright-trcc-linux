#!/usr/bin/env python3
"""The panel's short weekdays in every TRCC language, from CLDR -- the
Unicode locale data browsers and operating systems use, not from memory.

They live in TRCC's translation table under the keys ``MON`` ... ``SUN``,
so the panel draws ``tr("WED", lang)`` and a module (a weather forecast)
uses them by reference.  This prints them as the ``{lang: {key: text}}``
JSON ``i18n_gaps.py --write`` merges::

    python3.12 -m venv /tmp/cldr && /tmp/cldr/bin/pip install babel
    PYTHONPATH=src /tmp/cldr/bin/python dev/tools/cldr_weekdays.py > days.json
    PYTHONPATH=src python3.12 dev/tools/i18n_gaps.py --table main --write days.json

``babel`` (CLDR's Python reader) is needed only here -- never by TRCC.

The rule, which reproduces the 10 languages the panel had before:

* CLDR's ABBREVIATED day name, its trailing dot dropped, upper-cased
  ("Mo." -> "MO", "lun." -> "LUN");
* except Chinese, which keeps the C#'s 星期一 (``UCXiTongXianShiSub.cs``);
* and the languages in ``_SHORT``, whose abbreviated name is a whole word
  or a phrase ("יום ב׳", "Th 2"), which take CLDR's SHORT name instead.
  Persian's short name is a digit (۲ش), so it keeps its abbreviated one;
  Swahili and Urdu have nothing shorter than the word in CLDR.
"""
from __future__ import annotations

import json
import logging
import sys

log = logging.getLogger(__name__)

#: The keys, Monday first -- ``datetime.weekday()``'s order.
KEYS = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")
#: TRCC's language code -> CLDR's, where they differ.
_CLDR = {"zh_TW": "zh_Hant_TW", "tl": "fil", "no": "nb"}
#: Languages that take CLDR's short name (see the module docstring).
_SHORT = frozenset({"ar", "he", "my", "pl", "vi", "hi", "ta"})
#: The C#'s Chinese weekdays, Monday first.
_CHINESE = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")


def weekdays(lang: str) -> tuple[str, ...]:
    """*lang*'s seven short weekdays, Monday first, by the rule above."""
    from babel import Locale

    if lang in ("zh", "zh_TW"):
        log.debug("weekdays: %s -> the C#'s", lang)
        return _CHINESE
    width = "short" if lang in _SHORT else "abbreviated"
    names = Locale.parse(_CLDR.get(lang, lang)).days["format"][width]
    days = tuple(names[i].rstrip(".").upper() for i in range(7))
    log.debug("weekdays: %s (%s) -> %s", lang, width, days)
    return days


def main() -> int:
    from trcc.core.i18n import LANGUAGE_NAMES

    table = {lang: dict(zip(KEYS, weekdays(lang), strict=True)) for lang in LANGUAGE_NAMES
             if lang != "en"}
    json.dump(table, sys.stdout, ensure_ascii=False, indent=1)
    print()
    log.info("main: %d language(s)", len(table))
    return 0


if __name__ == "__main__":
    sys.exit(main())
