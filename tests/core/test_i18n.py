"""Tests for core/i18n.py — TRANSLATIONS table and tr() helper."""
from __future__ import annotations

import pytest

from trcc.legacy.core.i18n import LANGUAGE_NAMES, TITLE_BAR_POS, TITLE_BAR_TEXT, TRANSLATIONS, tr

# Original 10 languages (now ISO 639-1 codes, migrated from C# suffixes in v8.3.10)
ORIGINAL_LANGS = {'zh', 'zh_TW', 'en', 'de', 'ru', 'fr', 'pt', 'ja', 'es', 'ko'}

# Extended ISO 639-1 codes
EXTENDED_LANGS = {
    'it', 'nl', 'pl', 'tr', 'ar', 'hi', 'th', 'vi', 'id', 'cs',
    'sv', 'da', 'no', 'fi', 'hu', 'ro', 'uk', 'el', 'he', 'ms',
}

# New languages (v8.3.9)
NEW_LANGS = {'bn', 'ur', 'fa', 'tl', 'ta', 'pa', 'sw', 'my'}

ALL_LANGS = ORIGINAL_LANGS | EXTENDED_LANGS | NEW_LANGS

# All English keys that every language must have (except sparse)
EN_KEYS = set(TRANSLATIONS['en'].keys())

# Keys that don't need to be in every language (brand name, same across all)
SPARSE_KEYS = {'THERMALRIGHT Control Center'}


class TestTranslationsStructure:
    """Verify structural integrity of TRANSLATIONS dict."""

    def test_has_english(self) -> None:
        assert 'en' in TRANSLATIONS

    def test_all_langs_present(self) -> None:
        missing = ALL_LANGS - set(TRANSLATIONS.keys())
        assert not missing, f'Missing languages: {missing}'

    def test_no_unexpected_lang_keys(self) -> None:
        unexpected = set(TRANSLATIONS.keys()) - ALL_LANGS
        assert not unexpected, f'Unexpected language codes: {unexpected}'

    @pytest.mark.parametrize('lang', sorted(ALL_LANGS))
    def test_all_english_keys_present(self, lang: str) -> None:
        """Every language must have all English keys (except sparse)."""
        required = EN_KEYS - SPARSE_KEYS
        lang_keys = set(TRANSLATIONS[lang].keys())
        missing = required - lang_keys
        assert not missing, f'{lang!r} missing keys: {missing}'

    @pytest.mark.parametrize('lang', sorted(ALL_LANGS))
    def test_no_empty_values(self, lang: str) -> None:
        for key, text in TRANSLATIONS[lang].items():
            assert text.strip(), f'TRANSLATIONS[{lang!r}][{key!r}] is empty'

    @pytest.mark.parametrize('lang', sorted(ALL_LANGS))
    def test_no_leading_trailing_whitespace(self, lang: str) -> None:
        for key, text in TRANSLATIONS[lang].items():
            assert text == text.strip(), (
                f'TRANSLATIONS[{lang!r}][{key!r}] has whitespace'
            )

    @pytest.mark.parametrize('lang', sorted(ALL_LANGS))
    def test_values_are_strings(self, lang: str) -> None:
        for key, text in TRANSLATIONS[lang].items():
            assert isinstance(text, str), (
                f'TRANSLATIONS[{lang!r}][{key!r}] is {type(text)}'
            )

    @pytest.mark.parametrize('lang', sorted(ALL_LANGS))
    def test_no_unexpected_keys(self, lang: str) -> None:
        """No language should have keys that English doesn't have."""
        unexpected = set(TRANSLATIONS[lang].keys()) - EN_KEYS
        assert not unexpected, f'{lang!r} has unexpected keys: {unexpected}'


class TestLanguageNames:
    """LANGUAGE_NAMES — used for dropdown selector."""

    def test_covers_all_langs(self) -> None:
        assert set(LANGUAGE_NAMES.keys()) == ALL_LANGS

    def test_english_is_english(self) -> None:
        assert LANGUAGE_NAMES['en'] == 'English'

    def test_chinese_simplified(self) -> None:
        assert LANGUAGE_NAMES['zh'] == '简体中文'

    def test_all_names_unique(self) -> None:
        values = list(LANGUAGE_NAMES.values())
        assert len(values) == len(set(values)), 'Duplicate language names found'

    def test_new_langs_present(self) -> None:
        for lang in NEW_LANGS:
            assert lang in LANGUAGE_NAMES, f'{lang} missing from LANGUAGE_NAMES'


class TestTrFunction:
    """tr() — translation lookup with fallback chain."""

    def test_exact_match(self) -> None:
        assert tr('Layer Mask', 'en') == 'Layer Mask'

    def test_chinese_default_key(self) -> None:
        assert tr('Layer Mask', 'zh') == '布局蒙板'

    def test_extended_lang(self) -> None:
        assert tr('Layer Mask', 'it') == 'Maschera livello'

    def test_new_lang_bengali(self) -> None:
        assert tr('Layer Mask', 'bn') == 'লেয়ার মাস্ক'

    def test_new_lang_swahili(self) -> None:
        assert tr('Layer Mask', 'sw') == 'Mask ya Tabaka'

    def test_fallback_to_english(self) -> None:
        assert tr('Layer Mask', 'zz_nonexistent') == 'Layer Mask'

    def test_fallback_returns_key_for_unknown(self) -> None:
        assert tr('nonexistent_key_xyz', 'en') == 'nonexistent_key_xyz'

    def test_all_original_langs_resolve(self) -> None:
        for lang in ORIGINAL_LANGS:
            result = tr('Layer Mask', lang)
            assert result, f'tr("Layer Mask", {lang!r}) returned empty'

    def test_all_extended_langs_resolve(self) -> None:
        for lang in EXTENDED_LANGS:
            result = tr('Layer Mask', lang)
            assert result, f'tr("Layer Mask", {lang!r}) returned empty'

    def test_all_new_langs_resolve(self) -> None:
        for lang in NEW_LANGS:
            result = tr('Layer Mask', lang)
            assert result, f'tr("Layer Mask", {lang!r}) returned empty'

    def test_returns_str(self) -> None:
        assert isinstance(tr('Layer Mask', 'en'), str)


class TestCoordinateTuples:
    """Verify _POS coordinate tuples have correct structure."""

    def test_all_pos_tuples_are_5_ints(self) -> None:
        from trcc.legacy.core import i18n
        for name in dir(i18n):
            if name.endswith('_POS'):
                val = getattr(i18n, name)
                assert isinstance(val, tuple), f'{name} is not a tuple'
                assert len(val) == 5, f'{name} has {len(val)} elements, expected 5'
                assert all(isinstance(v, int) for v in val), f'{name} has non-int'

    def test_pos_values_non_negative(self) -> None:
        from trcc.legacy.core import i18n
        for name in dir(i18n):
            if name.endswith('_POS'):
                val = getattr(i18n, name)
                assert all(v >= 0 for v in val), f'{name} has negative values'


class TestTitleBar:
    """Gold title bar — universal, not translated."""

    def test_text_is_trcc_linux(self) -> None:
        assert TITLE_BAR_TEXT == 'TRCC-Linux'

    def test_pos_tuple(self) -> None:
        assert isinstance(TITLE_BAR_POS, tuple)
        assert len(TITLE_BAR_POS) == 5
        assert TITLE_BAR_POS[4] == 28  # font size
