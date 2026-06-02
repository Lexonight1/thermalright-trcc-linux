"""REST API adapter — FastAPI.

Same pattern as CLI/GUI: every endpoint builds a Command and calls
App.dispatch.  Pydantic models describe request/response shape; the
Command Result is serialised directly.
"""

from .main import build_app

__all__ = ["build_app"]
