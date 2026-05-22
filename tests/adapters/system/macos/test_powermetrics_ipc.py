"""Tests for powermetrics helper socket client (no real daemon).

Opt-in live helper test (macOS, helper installed):

  TRCC_TEST_POWERMETRICS_HELPER=1 pytest ... -k powermetrics_helper_live
"""
from __future__ import annotations

import os
import socket
import struct
import sys
import threading
import time
from unittest.mock import patch

import pytest

from trcc.legacy.adapters.system.macos.powermetrics_ipc import (
    fetch_powermetrics_bytes,
    fetch_powermetrics_text,
    powermetrics_socket_path,
    samplers_allowed,
)


def test_samplers_allowed() -> None:
    assert samplers_allowed('gpu_power')
    assert samplers_allowed('gpu_power,cpu_power')
    assert not samplers_allowed('')
    assert not samplers_allowed('gpu;power')
    assert not samplers_allowed('x' * 300)


def test_powermetrics_socket_path_empty_disables() -> None:
    with patch.dict(os.environ, {'TRCC_POWERMETRICS_SOCKET': ''}):
        assert powermetrics_socket_path() is None


def test_fetch_rejects_bad_samplers() -> None:
    with patch.dict(os.environ, {'TRCC_POWERMETRICS_SOCKET': '/tmp/x'}):
        assert fetch_powermetrics_text('gpu power') is None


def _frame_ok(body: bytes, status: int = 0) -> bytes:
    return b'TRC1' + struct.pack('!II', status, len(body)) + body


def test_fetch_roundtrip() -> None:
    # macOS sun_path is ~104 bytes; avoid long pytest tmp_path under deep trees.
    sock_path = f'/tmp/trcc-pm-rt-{os.getpid()}.sock'
    if os.path.exists(sock_path):
        os.unlink(sock_path)
    ready = threading.Event()
    body = b'GPU Power: 2 W\n'

    def serve() -> None:
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(sock_path)
        srv.listen(2)
        ready.set()
        for _ in range(2):
            conn, _ = srv.accept()
            line = b''
            while b'\n' not in line and len(line) < 128:
                line += conn.recv(128 - len(line))
            conn.sendall(_frame_ok(body))
            conn.close()
        srv.close()

    th = threading.Thread(target=serve, daemon=True)
    th.start()
    assert ready.wait(timeout=2.0)

    try:
        with patch.dict(os.environ, {'TRCC_POWERMETRICS_SOCKET': sock_path}):
            out = fetch_powermetrics_text('gpu_power', timeout=2.0)
            raw = fetch_powermetrics_bytes('gpu_power', timeout=2.0)
        assert out == body.decode()
        assert raw == body
    finally:
        th.join(timeout=3.0)
        if os.path.exists(sock_path):
            os.unlink(sock_path)


def test_fetch_helper_error_status() -> None:
    sock_path = None
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock_path = f'/tmp/trcc-pm-err-{os.getpid()}.sock'
        if os.path.exists(sock_path):
            os.unlink(sock_path)
        srv.bind(sock_path)
        srv.listen(1)

        def serve() -> None:
            c, _ = srv.accept()
            _ = c.recv(128)
            c.sendall(_frame_ok(b'powermetrics failed', status=4))
            c.close()

        th = threading.Thread(target=serve, daemon=True)
        th.start()
        time.sleep(0.05)
        with patch.dict(os.environ, {'TRCC_POWERMETRICS_SOCKET': sock_path}):
            assert fetch_powermetrics_text('gpu_power', timeout=2.0) is None
        th.join(timeout=2.0)
    finally:
        srv.close()
        if sock_path and os.path.exists(sock_path):
            os.unlink(sock_path)


@pytest.mark.skipif(sys.platform != 'darwin', reason='macOS only')
@pytest.mark.skipif(
    not os.environ.get('TRCC_TEST_POWERMETRICS_HELPER', '').strip(),
    reason='set TRCC_TEST_POWERMETRICS_HELPER=1 to hit the real LaunchDaemon socket',
)
def test_powermetrics_helper_live_integration() -> None:
    """Requires installed helper, running daemon, and a normal login UID (>= 500)."""
    from trcc.legacy.adapters.system.macos.powermetrics_extra import full_powermetrics_sampler_csv
    from trcc.legacy.adapters.system.macos.powermetrics_plist import parse_powermetrics_plist

    path = powermetrics_socket_path()
    assert path, 'TRCC_POWERMETRICS_SOCKET must not be empty for this test'
    samp = full_powermetrics_sampler_csv()
    raw = fetch_powermetrics_bytes(samp, timeout=15.0)
    assert raw and len(raw) > 200, 'helper returned no payload'

    chunk = raw.split(b'\x00', 1)[0].lstrip()
    if chunk.startswith((b'Machine model:', b'*** ')):
        pytest.skip(
            'helper returned text-mode powermetrics; reinstall plist helper or OS may not emit plist here'
        )

    assert chunk.startswith((b'<?xml', b'<plist')), 'expected plist XML from powermetrics -f plist'
    parsed = parse_powermetrics_plist(raw)
    assert parsed is not None and len(parsed) >= 1, f'expected parsed metrics, got {parsed!r}'
