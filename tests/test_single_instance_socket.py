"""``SingleInstance``'s accept loop — the socket a second launch talks to.

A second ``trcc gui`` does not start a second app: it finds the running one's
socket, sends ``{"raise": true}`` and exits, and the running window comes to
the front.  ``_accept_loop`` is what receives that.

It measured depth 5 with NO test naming it (16 of the 39 functions at depth
>= 4 were in that state on 2026-09-10).  The depth is not the problem —
accept, read, decode, dispatch is honestly that deep.  What was missing is
that every one of those layers swallows its own errors on purpose, and
nothing checked that swallowing an error leaves the loop ALIVE.  A loop that
dies on the first malformed byte looks identical to a healthy one until the
day a user's second launch stops raising the window.
"""
from __future__ import annotations

import contextlib
import gc
import json
import os
import socket
import threading
import time
import warnings
from collections.abc import Iterator
from pathlib import Path

import pytest

from trcc.ipc import (
    SingleInstance,
    _instance_socket_path,
    _peer_alive,
    _send_raise,
)


@pytest.fixture(autouse=True)
def _isolated_runtime_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Bind under a per-test directory, never the developer's real one."""
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))


def _needs_af_unix() -> None:
    if not hasattr(socket, "AF_UNIX"):
        pytest.skip("AF_UNIX unavailable (legacy Windows takes the msvcrt path)")


def _send(name: str, payload: bytes) -> None:
    """Talk to the instance socket exactly as a second launch would."""
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(2.0)
    try:
        client.connect(str(_instance_socket_path(name)))
        client.sendall(payload)
    finally:
        client.close()


def _instance(name: str) -> SingleInstance:
    inst = SingleInstance(name)
    assert inst is not None, "no peer should hold this per-test socket"
    return inst


def test_a_second_launch_raises_the_running_window() -> None:
    """The whole point: `{"raise": true}` reaches `on_raise`."""
    _needs_af_unix()
    raised = threading.Event()
    inst = _instance("gate-raise")
    inst.on_raise = raised.set
    try:
        _send("gate-raise", b'{"raise": true}\n')
        assert raised.wait(3.0), "on_raise never fired for a valid message"
    finally:
        inst.close()


def test_malformed_traffic_does_not_deafen_the_loop() -> None:
    """A bad peer must not cost every LATER launch its window-raise.

    Each layer swallows and continues on purpose — this asserts the
    `continue`, not the `except`, by sending a valid message afterwards.
    """
    _needs_af_unix()
    calls: list[str] = []
    heard = threading.Event()

    def _on_raise() -> None:
        calls.append("raise")
        heard.set()

    inst = _instance("gate-malformed")
    inst.on_raise = _on_raise
    try:
        _send("gate-malformed", b"not json at all\n")
        _send("gate-malformed", b"\xff\xfe binary garbage\n")
        _send("gate-malformed", b"")                       # connect, say nothing
        _send("gate-malformed", json.dumps({"other": 1}).encode() + b"\n")

        _send("gate-malformed", b'{"raise": true}\n')
        assert heard.wait(3.0), (
            "the loop stopped listening after malformed traffic — a second "
            "launch would silently fail to raise the window")
        assert calls, "on_raise never fired"
    finally:
        inst.close()


def test_a_callback_that_raises_does_not_kill_the_loop() -> None:
    """`on_raise` runs UI code on the accept thread; if it throws, the next
    launch must still be heard."""
    _needs_af_unix()
    calls: list[int] = []
    second = threading.Event()

    def _on_raise() -> None:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("slot blew up")
        second.set()

    inst = _instance("gate-callback")
    inst.on_raise = _on_raise
    try:
        _send("gate-callback", b'{"raise": true}\n')
        _send("gate-callback", b'{"raise": true}\n')
        assert second.wait(3.0), (
            "one exception from on_raise deafened the loop permanently")
    finally:
        inst.close()


def test_the_socket_is_per_ui_flavour() -> None:
    """`trcc gui` and `trcc qtgui` are separate instances, so separate sockets."""
    assert _instance_socket_path("gui") != _instance_socket_path("qtgui")
    assert _instance_socket_path("gui").parent.name == SingleInstance._DIR_NAME
    assert os.environ["XDG_RUNTIME_DIR"] in str(_instance_socket_path("gui"))


def test_without_xdg_runtime_dir_no_socket_goes_to_tmp(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """``/tmp`` is shared by every account — another user could bind
    ``/tmp/trcc.sock`` first and every TRCC UI would dispatch to THEIR process
    — and systemd-tmpfiles ages it out under a running App.  macOS never sets
    ``XDG_RUNTIME_DIR``, so this is every Mac session."""
    from trcc import ipc

    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))

    assert ipc.socket_path() == tmp_path / ".cache" / "trcc.sock"
    assert _instance_socket_path("gui") == tmp_path / ".cache" / "trcc" / "gui.sock"
    assert not str(ipc.socket_path()).startswith("/tmp/trcc")


def test_with_xdg_runtime_dir_the_paths_are_unchanged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Byte-identical to every release so far, so a UI still finds an App
    that an older install started."""
    from trcc import ipc

    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))

    assert ipc.socket_path() == tmp_path / "trcc.sock"
    assert _instance_socket_path("gui") == tmp_path / "trcc" / "gui.sock"


def test_only_a_raise_request_raises() -> None:
    """A well-formed message that does NOT ask to raise must not raise.

    The negative needs care.  Both a wrongly-accepted message and the valid
    one that follows call the SAME callback, so "did it fire?" cannot tell
    them apart, and an assertion taken the moment the valid one arrives passes
    either way — measured: the mutation that accepts any dict slipped straight
    through two earlier versions of this check.

    So the loop is allowed to go quiet first (it is serial, one connection at
    a time), and the count is taken after a bounded settle.
    """
    _needs_af_unix()
    calls: list[str] = []
    inst = _instance("gate-selective")
    inst.on_raise = lambda: calls.append("raise")
    try:
        for payload in (b'{"other": 1}\n', b'{"raise": false}\n', b'[]\n',
                        b'"a string"\n'):
            _send("gate-selective", payload)
        time.sleep(0.2)
        assert calls == [], (
            f"a message that never asked to raise triggered one: {calls}")
    finally:
        inst.close()


# ---------------------------------------------------------------------------
# Descriptor hygiene on the failure paths
#
# Both helpers below are reached on EVERY launch, and the branch that matters
# is the one that fails: a killed GUI leaves its socket file behind, so the
# next launch connects to an inode with nothing accepting on it.  A socket
# built inside a ``try`` and abandoned there leaks a descriptor exactly there,
# and nothing noticed because the caller swallows the ``OSError``.
# ---------------------------------------------------------------------------


def _leave_a_stale_socket_file(path: Path) -> None:
    """Bind and close, leaving the inode — what a killed GUI leaves behind.

    Closing an ``AF_UNIX`` listener does not unlink its path, so this is the
    real article rather than a plain file that would fail differently.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(str(path))
    stale.close()


@contextlib.contextmanager
def _leaks_no_descriptor() -> Iterator[None]:
    """Fail if the block abandons an unclosed socket.

    CPython's deallocator reports one as ``ResourceWarning``, which the suite
    already turns into an error — but only whenever the collector gets round
    to it, which lands the failure on an unrelated test.  Trapping it here
    names the culprit instead.  Pre-existing garbage is collected BEFORE the
    trap opens so nothing else can be mistaken for this block's leak, and no
    collection is forced inside it: an abandoned socket dies on refcount
    alone.
    """
    gc.collect()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        yield
    leaked = [str(w.message) for w in caught
              if issubclass(w.category, ResourceWarning)]
    assert not leaked, f"an unclosed socket was abandoned: {leaked}"


def test_peer_alive_leaks_no_descriptor_on_a_stale_socket(tmp_path: Path) -> None:
    """The branch taken by every launch that follows a crash."""
    _needs_af_unix()
    stale = tmp_path / "stale.sock"
    _leave_a_stale_socket_file(stale)

    with _leaks_no_descriptor():
        assert _peer_alive(stale, 0.5) is False


def test_send_raise_leaks_no_descriptor_when_the_peer_vanished(
    tmp_path: Path,
) -> None:
    """`_peer_alive` can say yes and the peer die before this connects.

    ``SingleInstance`` catches that ``OSError`` and carries on, so a leak on
    this path produces no symptom at all until descriptors run out.
    """
    _needs_af_unix()
    stale = tmp_path / "stale.sock"
    _leave_a_stale_socket_file(stale)

    with _leaks_no_descriptor(), pytest.raises(OSError):
        _send_raise(stale, b'{"raise": true}\n', 0.5)
