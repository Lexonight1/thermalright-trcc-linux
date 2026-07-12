#!/usr/bin/env python3
"""Mock API — real TRCC FastAPI server against MockPlatform.

Lets the dev curl any API endpoint to reproduce a user's HTTP-side bug
without hardware. The real `trcc.ui.api` module is loaded; only the
detect path is rebound to MockPlatform's mock devices.

Usage:
    PYTHONPATH=src python3 dev/mock_api.py
    PYTHONPATH=src python3 dev/mock_api.py --port 9876 --token devtoken
    PYTHONPATH=src python3 dev/mock_api.py --report user.txt
    curl -H "X-API-Token: devtoken" http://127.0.0.1:9876/devices

Auth: defaults to no token (open endpoints) for ease of debugging. Pass
`--token X` to require it. Bind is always 127.0.0.1 — never expose mock
devices to the LAN.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Bootstrap path/preflight/MockPlatform install.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _mock_bootstrap import bootstrap


def _parse_args() -> tuple[int, str | None, str | None]:
    port = 9876
    token: str | None = None
    report_path: str | None = None
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ('--port', '-p'):
            i += 1
            if i >= len(args):
                print("Error: --port requires a value", file=sys.stderr)
                sys.exit(1)
            port = int(args[i])
        elif arg in ('--token', '-t'):
            i += 1
            if i >= len(args):
                print("Error: --token requires a value", file=sys.stderr)
                sys.exit(1)
            token = args[i]
        elif arg == '--report':
            i += 1
            if i >= len(args):
                print("Error: --report requires a file path", file=sys.stderr)
                sys.exit(1)
            report_path = args[i]
        elif arg in ('-h', '--help'):
            print(__doc__)
            sys.exit(0)
        else:
            print(f"Error: unknown argument: {arg}", file=sys.stderr)
            sys.exit(1)
        i += 1
    return port, token, report_path


def main() -> None:
    port, token, report_path = _parse_args()

    # API renders frames headlessly — same offscreen Qt as the CLI uses.
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

    platform = bootstrap(report_path)

    print(f"\nServing on http://127.0.0.1:{port}")
    print(f"Auth: {'token=' + token if token else 'OPEN (no auth)'}")
    print("Try:")
    print(f"  curl http://127.0.0.1:{port}/devices")
    print(f"  curl http://127.0.0.1:{port}/system/health")
    print("Ctrl+C to quit.\n")

    # Uniform launch seam (METHOD_UI.md): inject the MockPlatform; the API
    # composes its App from it (headless QtRenderer) and serves.  ``run``
    # is the same code path the shipping ``trcc api`` uses.
    from trcc.ui.api.main import configure_auth, run
    configure_auth(token)
    run(platform, host='127.0.0.1', port=port)


if __name__ == '__main__':
    main()
