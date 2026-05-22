"""Tests for trcc serve QR code output and LAN IP detection.

Hexagonal layers tested:
- Core: ServerInfo DTO (to_json payload)
- Infrastructure: get_lan_ip (network adapter)
- CLI: _print_serve_qr (presentation — thin adapter over DTO + infra)
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from trcc.legacy.core.models import ServerInfo

# =========================================================================
# Fixtures
# =========================================================================

@pytest.fixture
def server_info() -> ServerInfo:
    """Default ServerInfo for tests."""
    return ServerInfo(host="192.168.1.42", port=9876, token="secret", tls=True)


@pytest.fixture
def server_info_no_auth() -> ServerInfo:
    """ServerInfo without token or TLS."""
    return ServerInfo(host="10.0.0.5", port=8080, token="", tls=False)


@pytest.fixture
def mock_qrcode():
    """Mock qrcode module — captures add_data calls."""
    mock_qr = MagicMock()
    mock_mod = MagicMock()
    mock_mod.QRCode.return_value = mock_qr
    mock_mod.ERROR_CORRECT_L = 1
    with patch.dict("sys.modules", {"qrcode": mock_mod}):
        yield mock_qr


@pytest.fixture
def mock_lan_ip():
    """Mock get_lan_ip to return a known address."""
    with patch(
        "trcc.legacy.adapters.infra.network.get_lan_ip", return_value="192.168.1.100",
    ) as m:
        yield m


# =========================================================================
# Core — ServerInfo DTO
# =========================================================================

class TestServerInfo:
    """ServerInfo dataclass and JSON serialization."""

    def test_to_json_all_fields(self, server_info: ServerInfo):
        payload = json.loads(server_info.to_json())
        assert payload == {
            "host": "192.168.1.42",
            "port": 9876,
            "token": "secret",
            "tls": True,
        }

    def test_to_json_empty_token(self, server_info_no_auth: ServerInfo):
        payload = json.loads(server_info_no_auth.to_json())
        assert payload["token"] == ""
        assert payload["tls"] is False

    def test_to_json_compact(self, server_info: ServerInfo):
        """No whitespace in JSON — keeps QR code small."""
        raw = server_info.to_json()
        assert " " not in raw

    def test_frozen(self):
        info = ServerInfo(host="x", port=1, token="", tls=False)
        with pytest.raises(AttributeError):
            info.host = "y"  # type: ignore[misc]


# =========================================================================
# Infrastructure — get_lan_ip
# =========================================================================

class TestGetLanIp:
    """Network adapter: LAN IP auto-detection."""

    def test_returns_ip_from_socket(self):
        mock_sock = MagicMock()
        mock_sock.getsockname.return_value = ("192.168.1.42", 12345)
        mock_sock.__enter__ = MagicMock(return_value=mock_sock)
        mock_sock.__exit__ = MagicMock(return_value=False)
        with patch("trcc.legacy.adapters.infra.network.socket.socket", return_value=mock_sock):
            from trcc.legacy.adapters.infra.network import get_lan_ip
            assert get_lan_ip() == "192.168.1.42"

    def test_fallback_on_oserror(self):
        with patch("trcc.legacy.adapters.infra.network.socket.socket", side_effect=OSError):
            from trcc.legacy.adapters.infra.network import get_lan_ip
            assert get_lan_ip() == "127.0.0.1"


# =========================================================================
# CLI — _print_serve_qr (presentation adapter)
# =========================================================================

class TestPrintServeQr:
    """CLI QR code rendering — thin presentation over ServerInfo + get_lan_ip."""

    def test_qr_payload_matches_server_info(self, mock_qrcode: MagicMock):
        from trcc.legacy.ui.cli import _print_serve_qr
        _print_serve_qr("10.0.0.5", 9876, "secret", True)

        added_data = mock_qrcode.add_data.call_args[0][0]
        payload = json.loads(added_data)
        assert payload["host"] == "10.0.0.5"
        assert payload["port"] == 9876
        assert payload["token"] == "secret"
        assert payload["tls"] is True

    def test_wildcard_host_resolves_to_lan_ip(
        self, mock_qrcode: MagicMock, mock_lan_ip: MagicMock,
    ):
        from trcc.legacy.ui.cli import _print_serve_qr
        _print_serve_qr("0.0.0.0", 9876, None, False)

        added_data = mock_qrcode.add_data.call_args[0][0]
        payload = json.loads(added_data)
        assert payload["host"] == "192.168.1.100"
        mock_lan_ip.assert_called_once()

    def test_ipv6_wildcard_resolves_to_lan_ip(
        self, mock_qrcode: MagicMock, mock_lan_ip: MagicMock,
    ):
        from trcc.legacy.ui.cli import _print_serve_qr
        _print_serve_qr("::", 9876, None, False)

        added_data = mock_qrcode.add_data.call_args[0][0]
        payload = json.loads(added_data)
        assert payload["host"] == "192.168.1.100"

    def test_no_token_sends_empty_string(self, mock_qrcode: MagicMock):
        from trcc.legacy.ui.cli import _print_serve_qr
        _print_serve_qr("10.0.0.1", 9876, None, False)

        added_data = mock_qrcode.add_data.call_args[0][0]
        payload = json.loads(added_data)
        assert payload["token"] == ""

    def test_explicit_host_skips_lan_detection(
        self, mock_qrcode: MagicMock, mock_lan_ip: MagicMock,
    ):
        from trcc.legacy.ui.cli import _print_serve_qr
        _print_serve_qr("10.0.0.5", 9876, None, False)

        mock_lan_ip.assert_not_called()
        added_data = mock_qrcode.add_data.call_args[0][0]
        assert json.loads(added_data)["host"] == "10.0.0.5"

    def test_silently_skips_when_qrcode_not_installed(self):
        """No crash when qrcode package is missing."""
        with patch.dict("sys.modules", {"qrcode": None}):
            from trcc.legacy.ui.cli import _print_serve_qr
            _print_serve_qr("10.0.0.1", 9876, "tok", False)

    def test_calls_print_ascii(self, mock_qrcode: MagicMock):
        from trcc.legacy.ui.cli import _print_serve_qr
        _print_serve_qr("10.0.0.1", 9876, None, False)

        mock_qrcode.make.assert_called_once_with(fit=True)
        mock_qrcode.print_ascii.assert_called_once_with(invert=True)
