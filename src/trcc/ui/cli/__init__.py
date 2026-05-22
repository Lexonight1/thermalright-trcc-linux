"""CLI adapter — typer-based.

Proof of the Command API: every CLI verb is a one-liner that builds a
Command and dispatches.  Result rendering is the CLI's only job — the
business logic lives in Commands.
"""

from .main import app as app  # re-export for entry point
