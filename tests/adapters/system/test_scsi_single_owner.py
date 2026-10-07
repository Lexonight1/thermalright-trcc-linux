"""LinuxScsiTransport takes a single-owner advisory claim on the sg node.

SCSI generic has NO kernel-level exclusion — measured on real hardware, two
processes can each ``os.open`` /dev/sgN and both write frames, which interleave
with no error and nothing logged.  ``open()`` therefore takes an advisory
``flock`` and refuses when another TRCC process already holds it.

``flock`` is a property of the file description, not of the driver, so a
regular file is a faithful stand-in for the device node here: two ``open()``
calls contend exactly as two processes on /dev/sgN do.  That is what makes this
testable with no hardware and no root.
"""
from __future__ import annotations

import errno
import fcntl
import os
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="LinuxScsiTransport is Linux-only",
)


@pytest.fixture
def node(tmp_path: Path) -> str:
    """A stand-in device node — ``flock`` behaves identically on it."""
    path = tmp_path / "sg0"
    path.write_bytes(b"")
    return str(path)


def _transport(path: str):
    from trcc.adapters.system.linux import LinuxScsiTransport
    return LinuxScsiTransport(path)


def test_first_owner_opens(node: str) -> None:
    first = _transport(node)
    assert first.open() is True
    assert first.is_open is True
    first.close()


def test_second_owner_is_refused_while_the_first_holds_it(node: str) -> None:
    """The bug this exists to prevent: two owners interleaving frames."""
    first = _transport(node)
    assert first.open() is True

    second = _transport(node)
    assert second.open() is False, (
        "a second TRCC process opened a device the first already owns — "
        "on SCSI both would write frames and interleave silently"
    )
    assert second.is_open is False

    first.close()


def test_the_claim_is_released_on_close(node: str) -> None:
    first = _transport(node)
    assert first.open() is True
    first.close()

    second = _transport(node)
    assert second.open() is True, "closing the first owner must free the device"
    second.close()


def test_reopening_the_same_transport_is_idempotent(node: str) -> None:
    """``open()`` short-circuits on an already-open fd, so it must not
    re-enter the claim and refuse itself."""
    transport = _transport(node)
    assert transport.open() is True
    assert transport.open() is True
    transport.close()


def test_a_foreign_holder_of_the_flock_blocks_us(node: str) -> None:
    """Whoever holds the advisory lock owns the device — including a peer
    that is not going through this class."""
    fd = os.open(node, os.O_RDWR)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        assert _transport(node).open() is False
    finally:
        os.close(fd)


def test_refusing_leaks_no_file_descriptor(node: str) -> None:
    """A refused open must close the fd it just took, or every retry leaks
    one and a reconnect loop exhausts the process."""
    first = _transport(node)
    assert first.open() is True

    before = len(os.listdir("/proc/self/fd"))
    for _ in range(20):
        assert _transport(node).open() is False
    after = len(os.listdir("/proc/self/fd"))

    assert after <= before, f"leaked {after - before} fds across 20 refused opens"
    first.close()


def test_block_nodes_are_not_claimed(tmp_path: Path, monkeypatch) -> None:
    """The root-only /dev/sd* fallback keeps today's behaviour: its locking
    interacts with the kernel's mount claiming, which is unverified."""
    from trcc.adapters.system.linux import LinuxScsiTransport

    path = tmp_path / "sda"
    path.write_bytes(b"")
    # The class decides by path prefix; point it at /dev/sd* semantics.
    first = LinuxScsiTransport(str(path))
    monkeypatch.setattr(first, "_is_block", True)
    assert first.open() is True

    second = LinuxScsiTransport(str(path))
    monkeypatch.setattr(second, "_is_block", True)
    assert second.open() is True, "block nodes must not take the advisory claim"

    first.close()
    second.close()


def test_a_lock_error_that_is_not_busy_degrades_instead_of_failing(
    node: str, monkeypatch,
) -> None:
    """ENOLCK on an exotic filesystem must not make an otherwise-usable
    device un-openable."""
    def _enolck(fd: int, op: int) -> None:
        raise OSError(errno.ENOLCK, "no locks available")

    monkeypatch.setattr(fcntl, "flock", _enolck)
    transport = _transport(node)
    assert transport.open() is True
    transport.close()


def test_a_denied_open_says_so_instead_of_returning_false(
    node: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """EACCES is the user's setup, not a panel that is not answering yet.

    A plain False read as "try again", so a panel nobody may open was retried
    for as long as TRCC ran, and the connect lost the remedy.  Raised the way
    the bulk and HID transports raise it.  (Injected rather than ``chmod 000``:
    CI runs as root, which ignores the mode.)
    """
    from trcc.core.errors import PermissionError_

    real_open = os.open

    def denied(path, *args, **kwargs):  # type: ignore[no-untyped-def]
        if path == node:
            raise PermissionError(errno.EACCES, "Permission denied", path)
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(os, "open", denied)

    with pytest.raises(PermissionError_, match="trcc system setup"):
        _transport(node).open()
