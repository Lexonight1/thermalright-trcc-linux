"""Language listing and selection CLI commands."""
from __future__ import annotations

from trcc.legacy.ui.cli import _cli_handler


@_cli_handler
def get_languages():
    """List all available languages with ISO 639-1 codes."""
    from trcc.legacy.core.i18n import LANGUAGE_NAMES

    print(f"Available languages ({len(LANGUAGE_NAMES)}):")
    for code, name in sorted(LANGUAGE_NAMES.items()):
        print(f"  {code:5s}  {name}")
    return 0


@_cli_handler
def get_language():
    """Show current language."""
    from trcc.legacy._boot import trcc as _trcc
    from trcc.legacy.core.i18n import LANGUAGE_NAMES

    code = _trcc().settings.lang
    name = LANGUAGE_NAMES.get(code, code)
    print(f"{code} ({name})")
    return 0


@_cli_handler
def set_language(code: str):
    """Set the application language by ISO 639-1 code."""
    from trcc.legacy._boot import trcc as _trcc
    from trcc.legacy.core.i18n import LANGUAGE_NAMES

    result = _trcc().control_center.set_language(code)
    if not result.success:
        print(f"Unknown language code '{code}'.")
        print("Use 'trcc lang-list' to see valid codes.")
        return 1
    print(f"Language set to {code} ({LANGUAGE_NAMES.get(code, code)})")
    return 0
