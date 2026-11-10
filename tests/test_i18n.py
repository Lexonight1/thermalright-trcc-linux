"""i18n — language code + translation table coverage + tr() fallback rules."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from trcc.core.i18n import (
    LANGUAGE_NAMES,
    TRANSLATIONS,
    language_name,
    supported_languages,
    tr,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dev" / "tools"))
import i18n_gaps


def test_every_language_translates_every_english_key() -> None:
    """Every table -- the main one and each module's -- is complete in every
    language TRCC offers.  29 keys sat untranslated in 37 languages until
    2026-10-21 because nothing measured it; ``dev/tools/i18n_gaps.py`` lists
    a gap and fills it.  MUTATION CHECK: drop one German entry and this
    names it."""
    main = i18n_gaps.main_table()
    short = {name: gaps for name, table in i18n_gaps.tables().items()
             if (gaps := i18n_gaps.gaps(table, None if name == "main" else main))}
    assert short == {}, (
        "untranslated -- run PYTHONPATH=src python3.12 dev/tools/i18n_gaps.py:\n"
        + "\n".join(f"  {name} {lang}: {keys}" for name, langs in short.items()
                    for lang, keys in langs.items()))


def test_the_gap_tool_refuses_what_it_must_not_write() -> None:
    """``--write`` takes only real languages, English keys, non-empty text,
    and never replaces a translation already there."""
    table = {"en": {"Hello": "Hello"}, "de": {"Hello": "Hallo"}}
    assert i18n_gaps.problems(table, {"fr": {"Hello": "Bonjour"}}) == []
    assert i18n_gaps.problems(table, {"de": {"Hello": "Hallo"}}) == []
    assert i18n_gaps.problems(table, {
        "xx": {"Hello": "?"},
        "fr": {"Bye": "Adieu", "Hello": " "},
        "de": {"Hello": "Servus"},
    }) == [
        "xx: TRCC has no such language",
        "fr: 'Bye' is not an English key",
        "fr: 'Hello' has no translation",
        "de: 'Hello' is already 'Hallo' -- not replaced",
    ]


def test_the_gap_tool_adds_lines_without_moving_any(tmp_path: Path) -> None:
    """The main table is edited in place: each new line lands at the end of
    its language's block, comments and existing lines untouched."""
    source = tmp_path / "i18n.py"
    source.write_text(
        "TRANSLATIONS = {\n"
        "    'en': {\n"
        "        # a comment that must survive\n"
        "        'A': 'A', 'B': 'B',\n"
        "    },\n"
        "    'de': {\n"
        "        'A': 'Ah',\n"
        "    },\n"
        "}\n", encoding="utf-8")
    assert i18n_gaps.write_main({"de": {"A": "Ah", "B": "Beh"}}, source) == 1
    assert source.read_text(encoding="utf-8").split("\n")[5:9] == [
        "    'de': {", "        'A': 'Ah',", "        'B': 'Beh',", "    },"]


def test_the_gap_tool_reads_an_entry_split_over_two_lines(tmp_path: Path) -> None:
    """A long entry wraps its translation onto the next line; the block is
    still read whole.  MUTATION CHECK: read ``_block_keys`` a line at a time."""
    source = tmp_path / "i18n.py"
    source.write_text(
        "TRANSLATIONS = {\n"
        "    'en': {\n"
        "        'Long words':\n"
        "            'Long words',\n"
        "    },\n"
        "}\n", encoding="utf-8")
    assert i18n_gaps.write_main({"en": {"Long words": "Long words", "B": "B"}}, source) == 1
    assert source.read_text(encoding="utf-8").split("\n")[2:6] == [
        "        'Long words':", "            'Long words',", "        'B': 'B',", "    },"]


def test_a_module_table_merges_into_its_json(tmp_path: Path) -> None:
    """A module's ``i18n.json`` gains the new entries and keeps the old."""
    (tmp_path / "viz").mkdir()
    path = tmp_path / "viz" / "i18n.json"
    path.write_text(json.dumps({"en": {"A": "A"}, "de": {}}), encoding="utf-8")
    assert i18n_gaps.write_module("viz", {"de": {"A": "Ah"}}, tmp_path) == 1
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "en": {"A": "A"}, "de": {"A": "Ah"}}


def test_our_modules_reference_trccs_words_instead_of_copying_them() -> None:
    """A word TRCC already translates is used by reference: our own modules
    carry no copy of it (RGB's "Rainbow" and the visualizer's "Colour" did,
    and 7 of the copies had drifted from TRCC's wording).  MUTATION CHECK:
    put a German "Background" back in RGB's ``i18n.json`` -- this names it."""
    main = i18n_gaps.main_table()
    copies = {name: found for name, table in i18n_gaps.tables().items()
              if name != "main" and (found := i18n_gaps.references(table, main)[0])}
    assert copies == {}, (
        "a module copies TRCC's words -- run i18n_gaps.py --drop-redundant:\n"
        + "\n".join(f"  {name}: {langs}" for name, langs in copies.items()))


def test_the_gap_tool_counts_trccs_words_and_drops_only_copies(tmp_path: Path) -> None:
    """For a module, a word TRCC has is covered; a copy of it is removed, an
    override kept, and a new word not translated yet is never touched."""
    main = {"en": {"Cancel": "Cancel", "Rainbow": "Rainbow"},
            "de": {"Cancel": "Abbrechen", "Rainbow": "Regenbogen"}}
    table = {"en": {"Cancel": "Cancel", "Rainbow": "Rainbow", "Gust": "Gust"},
             "de": {"Cancel": "Abbrechen", "Rainbow": "Regenbogen-Effekt"}}
    (tmp_path / "weather").mkdir()
    (tmp_path / "weather" / "i18n.json").write_text(json.dumps(table), encoding="utf-8")

    assert i18n_gaps.gaps(table, main)["de"] == ["Gust"]
    assert i18n_gaps.references(table, main) == ({"de": ["Cancel"]},
                                                 {"de": ["Rainbow"]})
    assert i18n_gaps.drop_redundant("weather", main, tmp_path) == 1
    assert json.loads((tmp_path / "weather" / "i18n.json").read_text(encoding="utf-8")) == {
        "en": {"Rainbow": "Rainbow", "Gust": "Gust"},
        "de": {"Rainbow": "Regenbogen-Effekt"}}


def test_every_language_in_table_has_a_name() -> None:
    """Every language code in TRANSLATIONS must show up in LANGUAGE_NAMES.

    UIs render language pickers off LANGUAGE_NAMES; missing entries
    would show as "[code only]" in the dropdown.
    """
    missing = set(TRANSLATIONS) - set(LANGUAGE_NAMES)
    assert not missing, f"language(s) in TRANSLATIONS but not LANGUAGE_NAMES: {missing}"


def test_every_named_language_has_a_translation_table() -> None:
    """And the converse — every code in LANGUAGE_NAMES should have
    a TRANSLATIONS sub-dict (even if currently sparse)."""
    missing = set(LANGUAGE_NAMES) - set(TRANSLATIONS)
    assert not missing, f"named language(s) without TRANSLATIONS entry: {missing}"


def test_english_table_carries_every_key_other_languages_translate() -> None:
    """Every key used in any non-English language must also appear in
    English — otherwise tr() can't fall back, and the key string ends
    up rendered raw."""
    en = TRANSLATIONS.get("en", {})
    for lang, table in TRANSLATIONS.items():
        if lang == "en":
            continue
        leaked = set(table) - set(en)
        assert not leaked, f"{lang!r} has keys not in English: {leaked}"


def test_tr_returns_translation_for_known_key_and_lang() -> None:
    # "Layer Mask" is present in zh; pick something we know is translated.
    en = TRANSLATIONS["en"]
    assert "Layer Mask" in en, "test fixture drift: 'Layer Mask' missing from en"
    assert tr("Layer Mask", "zh") == TRANSLATIONS["zh"]["Layer Mask"]


def test_tr_falls_back_to_english_when_lang_unknown() -> None:
    """Unknown language code → English string (the key)."""
    assert tr("Background", "made-up-lang-zz") == TRANSLATIONS["en"]["Background"]


def test_tr_falls_back_to_key_when_key_unknown() -> None:
    """Unregistered key → the key string itself.

    Acceptable because the key IS English — UIs render "Some New Button"
    even when no one's translated it yet.
    """
    assert tr("Definitely Unregistered Key 9zx", "fr") == "Definitely Unregistered Key 9zx"


def test_supported_languages_includes_english() -> None:
    langs = supported_languages()
    assert "en" in langs
    assert "zh" in langs
    assert len(langs) == len(LANGUAGE_NAMES)


def test_language_name_returns_native_spelling() -> None:
    assert language_name("zh") == "简体中文"
    assert language_name("en") == "English"


def test_language_name_falls_back_to_code() -> None:
    """Unknown code → return the code itself so UIs don't crash on
    stale config carried forward from older releases."""
    assert language_name("zz_made_up") == "zz_made_up"


# =========================================================================
# Command-level behavior
# =========================================================================


def _cli_app():
    """Late import so cli_app fixture's platform override is in place."""
    from trcc.ui.cli.main import app
    return app


def test_set_language_rejects_unknown_code(cli_runner, cli_app) -> None:
    """``SetLanguage`` via CLI surfaces a structured error for codes
    that aren't in the i18n table."""
    del cli_app
    result = cli_runner.invoke(
        _cli_app(),
        ["config", "language", "zz_unknown"],
    )
    assert result.exit_code != 0
    assert "unknown language code" in result.output.lower()


def test_set_language_accepts_known_code(cli_runner, cli_app) -> None:
    del cli_app
    result = cli_runner.invoke(_cli_app(), ["config", "language", "fr"])
    assert result.exit_code == 0
    assert "fr" in result.output
    # native-name appears in the success message
    assert "Français" in result.output


def test_list_languages_via_cli(cli_runner, cli_app) -> None:
    del cli_app
    result = cli_runner.invoke(_cli_app(), ["system", "list-languages"])
    assert result.exit_code == 0
    # Every language line should at least show "en" and "English".
    assert "en " in result.output
    assert "English" in result.output


# =========================================================================
# Sanity — no surprising keys from legacy that we silently dropped
# =========================================================================


_EXPECTED_LANG_COUNT = 38     # ISO 639-1 codes legacy ships


def test_full_38_language_count_preserved() -> None:
    """Every language in legacy should be in next/.  If this drops, the
    port lost data — investigate before merging."""
    assert len(LANGUAGE_NAMES) == _EXPECTED_LANG_COUNT


@pytest.mark.parametrize("lang_code", [
    "en", "zh", "zh_TW", "fr", "de", "ja", "ko", "es", "it", "pt",
    "ru", "ar", "hi", "th", "vi", "id", "cs", "sv", "da", "no",
    "fi", "hu", "ro", "uk", "el", "he", "ms", "bn", "ur", "fa",
    "tl", "ta", "pa", "sw", "my", "nl", "pl", "tr",
])
def test_every_legacy_language_present(lang_code: str) -> None:
    """Pin each legacy code so a future refactor can't accidentally
    drop one — they're an audited set."""
    assert lang_code in LANGUAGE_NAMES
    assert lang_code in TRANSLATIONS


# ── A module's words: its own first, then TRCC's -- for it alone ─────────


@pytest.fixture
def module_words(monkeypatch: pytest.MonkeyPatch) -> dict:
    """An empty module-word store, put back after."""
    from trcc.core import i18n

    store: dict = {}
    monkeypatch.setattr(i18n, "_MODULE_WORDS", store)
    return store


def test_a_module_uses_trccs_word_unless_it_sets_its_own(module_words: dict) -> None:
    """MUTATION CHECK: look TRCC's table up first in ``tr`` -- the override
    is ignored."""
    from trcc.core.i18n import add_translations

    add_translations("weather", {"en": {"Background": "Background"},
                                 "de": {"Background": "Wetterhintergrund"}})

    assert tr("Background", "de", "weather") == "Wetterhintergrund"
    assert tr("Layer Mask", "de", "weather") == TRANSLATIONS["de"]["Layer Mask"]


def test_a_modules_words_never_reach_trcc_or_another_module(module_words: dict) -> None:
    """An installed module's ``i18n.json`` loads even while it is off, so its
    words must not fill TRCC's text -- the "Unsigned" badge included -- or
    another module's.  MUTATION CHECK: let ``tr`` fall back to every
    module's words -- both fail."""
    from trcc.core.i18n import add_translations

    add_translations("weather", {"de": {"Background": "Geprüft", "Gust": "Böe"}})

    assert tr("Background", "de") == TRANSLATIONS["de"]["Background"]
    assert tr("Gust", "de", "rgb") == "Gust"


def test_an_update_replaces_a_modules_words(module_words: dict) -> None:
    """They used to be first-come: an update's corrected words waited for a
    restart.  MUTATION CHECK: merge instead of replace -- the old word stays."""
    from trcc.core.i18n import add_translations, drop_translations

    add_translations("weather", {"de": {"Gust": "Boe"}})
    add_translations("weather", {"de": {"Gust": "Böe"}})
    assert tr("Gust", "de", "weather") == "Böe"

    drop_translations("weather")
    assert tr("Gust", "de", "weather") == "Gust"


# ── The gui's tooltips ───────────────────────────────────────────────────


def test_the_gui_says_its_tooltips_in_the_apps_language(tmp_path: Path, qtbot: Any) -> None:
    """His ask (2026-11-05): the gui's tooltips were English in every
    language.  One made at start, the theme ✕ made when the list fills and a
    custom dashboard panel's -- both made AFTER the language was chosen --
    all in German, and back.  MUTATION CHECK: drop ``retranslate(self, ...)``
    from ``TRCCApp._show_language``; then the one in ``_apply_decorations``."""
    from PySide6.QtWidgets import QPushButton

    from tests.mock_platform import MockPlatform
    from trcc.adapters.render.qt import QtRenderer
    from trcc.app import App
    from trcc.core.commands import SetLanguage
    from trcc.core.results import ThemeListEntry
    from trcc.ui.gui.trcc_app import TRCCApp

    app = App(MockPlatform([], tmp_path), renderer=QtRenderer())
    try:
        window = TRCCApp(app=app)
        qtbot.addWidget(window)
        assert window.form1_close_btn.toolTip() == "Close"
        assert app.dispatch(SetLanguage(language="de")).ok
        qtbot.waitUntil(lambda: window.form1_close_btn.toolTip() == "Schließen", timeout=2000)

        browser = window.uc_theme_local
        browser.set_themes([ThemeListEntry(name="Mine", resolution=(320, 320),
                                           path=str(tmp_path / "Mine"), origin="user")])
        crosses = [b for b in browser.findChildren(QPushButton) if b.text() == "✕"]
        assert crosses and {b.toolTip() for b in crosses} == {"Theme löschen"}

        window.uc_system_info._on_add_clicked()
        deletes = [b for b in window.uc_system_info.findChildren(QPushButton)
                   if b.text() == "✕" and b.isVisibleTo(window.uc_system_info)]
        assert deletes and {b.toolTip() for b in deletes} == {"Panel löschen"}

        assert app.dispatch(SetLanguage(language="en")).ok
        qtbot.waitUntil(lambda: window.form1_close_btn.toolTip() == "Close", timeout=2000)
        assert {b.toolTip() for b in crosses} == {"Delete theme"}
    finally:
        app.close()


def test_every_tooltip_the_gui_sets_is_a_word_of_the_table() -> None:
    """A new English tooltip must be added to the table -- in every language,
    by the gate above -- or it shows English to everyone.  Read from the
    source: ``say_tip`` calls, ``tooltip=`` / ``tip=`` arguments, the tooltip
    tables and the tab list.  App data (a sensor's explanation, a device's
    name) and module tabs (their own table) are not literals, so not read."""
    import ast

    english = TRANSLATIONS["en"]
    found: dict[str, str] = {}
    ui = Path(__file__).resolve().parents[1] / "src" / "trcc" / "ui"
    # The gui's own files and the Settings panel every GUI shares.
    for source in sorted([*(ui / "gui").glob("*.py"), *(ui / "settings_panel").glob("*.py")]):
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
            texts: list[ast.expr] = []
            if isinstance(node, ast.Call):
                name = getattr(node.func, "id", getattr(node.func, "attr", ""))
                if name == "say_tip" and len(node.args) > 1:
                    texts.append(node.args[1])
                texts += [k.value for k in node.keywords if k.arg in ("tooltip", "tip")]
            elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict) and any(
                    isinstance(t, ast.Name) and "TOOLTIP" in t.id for t in node.targets):
                texts += [v for v in node.value.values if v is not None]
            for text in texts:
                for leaf in (text.body, text.orelse) if isinstance(text, ast.IfExp) else (text,):
                    if isinstance(leaf, ast.Constant) and isinstance(leaf.value, str):
                        found[leaf.value] = f"{source.name}:{getattr(node, 'lineno', 0)}"
    assert len(found) > 60            # the reader still finds them
    assert {text: where for text, where in found.items() if text not in english} == {}
