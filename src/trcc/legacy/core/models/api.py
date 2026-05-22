"""API server DTOs."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ServerInfo:
    """Connection details for a running TRCC API server."""
    host: str
    port: int
    token: str
    tls: bool

    def to_json(self) -> str:
        """Compact JSON payload for QR codes / remote apps."""
        import json
        return json.dumps({
            "host": self.host,
            "port": self.port,
            "token": self.token,
            "tls": self.tls,
        }, separators=(",", ":"))


__all__ = ['ServerInfo']
