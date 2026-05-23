"""i18n — language code + translation table coverage + tr() fallback rules."""
from __future__ import annotations

import pytest

from trcc.core.i18n import (
    LANGUAGE_NAMES,
    TRANSLATIONS,
    language_name,
    supported_languages,
    tr,
)


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
