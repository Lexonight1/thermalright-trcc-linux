#!/usr/bin/env python3
"""Mock CLI — real TRCC CLI commands against MockPlatform.

Lets the dev reproduce a user's exact CLI behavior (`trcc detect`,
`trcc led-mode temp_linked`, `trcc render`, etc.) without hardware.

DI flow (clean, no monkey-patches):
    1. Bootstrap loads device specs, builds MockPlatform.
    2. We pre-seed `cli._boot.trcc(platform)` — first call wins, caches
       the mock-backed Trcc.
    3. Real typer CLI dispatches; every command that calls `_boot.trcc()`
       gets the cached, mock-backed Trcc.

Usage:
    PYTHONPATH=src python3 dev/mock_cli.py detect
    PYTHONPATH=src python3 dev/mock_cli.py led-mode static --color red
    PYTHONPATH=src python3 dev/mock_cli.py --report user.txt detect
    PYTHONPATH=src python3 dev/mock_cli.py --help

`--report <file>` is parsed here; everything else is forwarded to typer.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Bootstrap path/preflight/MockPlatform install.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _mock_bootstrap import bootstrap


def _extract_report_arg() -> str | None:
    """Pull --report <file> out of sys.argv before typer sees it."""
    if '--report' not in sys.argv:
        return None
    idx = sys.argv.index('--report')
    if idx + 1 >= len(sys.argv):
        print("Error: --report requires a file path", file=sys.stderr)
        sys.exit(1)
    report = sys.argv[idx + 1]
    del sys.argv[idx:idx + 2]
    return report


def main() -> None:
    report_path = _extract_report_arg()

    # CLI commands drop into the offscreen Qt platform automatically;
    # don't override here — let trcc.ui.cli._make_cli_renderer do it.
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

    platform = bootstrap(report_path)

    # Pre-seed the CLI boot cache with our MockPlatform. Any command
    # that subsequently calls `_boot.trcc()` gets the cached, mock-backed
    # Trcc — no monkey-patching, just clean DI.
    from trcc._boot import trcc as boot_trcc
    boot_trcc(platform)

    from trcc.ui.cli import app as cli_app
    cli_app()


if __name__ == '__main__':
    main()
