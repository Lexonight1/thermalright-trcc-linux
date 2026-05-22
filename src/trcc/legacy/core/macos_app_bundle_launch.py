"""PyInstaller ``TRCC.app`` (macOS) — default Typer subcommand when launched from Finder.

Finder double-click often passes no CLI arguments (sometimes only ``-psn_*``). Typer
then prints help and exits, which is invisible in a ``--windowed`` bundle.
"""

from __future__ import annotations

from pathlib import Path

# Written after the first auto-launched ``setup-gui`` session so subsequent
# opens run ``gui`` (see ``src/trcc/__main__.py``).
ONBOARDING_MARKER = Path.home() / '.trcc' / '.macos_app_onboarding_done'


def argv_tail_without_launch_services_noise(argv: list[str]) -> list[str]:
    """Argv after executable, excluding macOS ``-psn_*`` Process Serial Number args."""
    return [a for a in argv[1:] if not a.startswith('-psn_')]


def is_frozen_macos_trcc_pyinstaller_bundle(
    platform: str,
    frozen: bool,
    executable: str,
) -> bool:
    if platform != 'darwin' or not frozen:
        return False
    p = Path(executable).resolve()
    parts = p.parts
    if len(parts) < 4:
        return False
    try:
        macos_idx = parts.index('MacOS')
    except ValueError:
        return False
    if macos_idx == 0:
        return False
    if parts[-2] != 'MacOS' or parts[macos_idx - 1] != 'Contents':
        return False
    return p.name.lower() == 'trcc'


def should_inject_typer_subcommand(argv_tail: list[str]) -> bool:
    """True only for a bare launch (no subcommand, no help/version flags)."""
    if not argv_tail:
        return True
    first = argv_tail[0]
    if first in ('-h', '--help', '--version'):
        return False
    if not first.startswith('-'):
        return False
    return False


def subcommand_for_bundle_double_click(
    argv: list[str],
    *,
    platform: str,
    frozen: bool,
    executable: str,
    marker: Path = ONBOARDING_MARKER,
) -> str | None:
    """Return ``setup-gui``, ``gui``, or ``None`` to leave ``argv`` unchanged."""
    if not is_frozen_macos_trcc_pyinstaller_bundle(platform, frozen, executable):
        return None
    if not should_inject_typer_subcommand(argv_tail_without_launch_services_noise(argv)):
        return None
    if marker.exists():
        return 'gui'
    return 'setup-gui'
