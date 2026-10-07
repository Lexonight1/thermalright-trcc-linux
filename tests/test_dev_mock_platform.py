"""The dev mock fleet must survive a change to the transport-opener port.

``dev/_mock_bootstrap._build_dev_platform`` overrides ONLY the USB seam --
``scan_devices`` plus the scripted ``_open_scsi`` / ``_open_bulk`` -- and
inherits ``BaseOS.open_transport`` and its Wire->opener table from the real host
platform.  That inheritance is the whole value of the dev mock (it exercises the
production dispatch rather than a copy of it) and it is also the exposure:
``open_transport`` calls ``opener(vid, pid, serial, unit)``, so an opener whose
signature drifts from the port is a ``TypeError`` on **every** connect.

It drifted.  #287 step 4 (``10c383c1``) added ``unit`` to the openers, taught
``tests/mock_platform.py`` about it, and missed these two.  Every dev harness --
``mock_gui``, ``mock_cli``, ``mock_api``, ``mock`` -- died on ``ConnectDevice``
with ``_open_scsi() takes from 3 to 4 positional arguments but 5 were given``,
while the suite stayed green at 27,161, because **no test had ever built a
``DevMockPlatform``**.  The tests import helper FUNCTIONS from that module
(``variant_resolution``, ``NO_PANEL``, ``_specs_from_report``) and nothing else,
so the class the harnesses actually run on was unmeasured.

That matters more than a broken script: ``dev/mock_gui.py`` is the tool
``CLAUDE.md`` makes mandatory after every refactor ("run the real app and
``dev/mock_gui.py``"), and the multi-device mock is how #136 / #137 / widescreen
panels are verified without hardware.  A silently dead harness is a verification
step that reports success by not running.

These DRIVE the seam rather than inspect it -- ``open_transport`` is called the
way the app calls it, so any future parameter the port gains is caught by the
same TypeError, with no list here to keep in sync.

MUTATION CHECK -- drop ``unit`` from either scripted opener in
``dev/_mock_bootstrap.py`` and the matching test must fail with that TypeError.
Both were confirmed to fail before this file was committed.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from trcc.core.models import Wire
from trcc.core.ports import Transport

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dev"))
from _mock_bootstrap import _build_dev_platform

#: One SCSI panel and one bulk panel -- the two scripted openers.  Kept here
#: rather than read from ``dev/devices.json``, which is local and gitignored:
#: a gate that depends on an untracked file passes by skipping on CI.
_SCSI = {"type": "lcd", "name": "scsi panel", "vid": "0402", "pid": "3922",
         "pm": 32, "fbl": 100, "resolution": "320x320"}
_BULK = {"type": "lcd", "name": "bulk panel", "vid": "87ad", "pid": "70db",
         "pm": 11, "sub": 5, "resolution": "854x480"}


@pytest.mark.parametrize(
    ("spec", "wire"),
    [(_SCSI, Wire.SCSI), (_BULK, Wire.BULK)],
    ids=("scsi", "bulk"),
)
def test_the_dev_mock_opens_a_transport_the_way_the_app_does(
    spec: dict, wire: Wire,
) -> None:
    """``App.attach`` reaches the scripted opener through this exact call."""
    platform = _build_dev_platform([spec])
    vid, pid = int(spec["vid"], 16), int(spec["pid"], 16)

    transport = platform.open_transport(wire, vid, pid, None, "")

    assert isinstance(transport, Transport), (
        f"the dev mock's {wire.value} opener did not hand back a Transport — "
        f"every dev harness connects through this call"
    )


def test_the_dev_mock_opens_a_transport_with_the_ports_defaults() -> None:
    """The same call with the optional arguments omitted.

    ``App.attach`` passes all four today, but the port declares ``serial`` and
    ``unit`` optional, and a scripted opener that only works when they are
    supplied would be a mock that is stricter than the thing it stands in for.
    """
    platform = _build_dev_platform([_SCSI])

    transport = platform.open_transport(Wire.SCSI, 0x0402, 0x3922)

    assert isinstance(transport, Transport)


@pytest.mark.parametrize("specs", [None, [{"vid": "0402", "pid": "3922",
                                            "fbl": 100}]],
                         ids=["--hardware", "fleet"])
def test_the_dev_platforms_are_stand_ins(
    monkeypatch: pytest.MonkeyPatch, specs: list[dict] | None,
) -> None:
    """Both dev platforms subclass the host's class without a key, so they are
    stand-ins whatever the shell says -- the mock harnesses used to be local
    only through ``tests.conftest`` setting TRCC_DAEMON=0, which ``--hardware``
    never imports."""
    import os

    from trcc._boot import _ENV_FLAG, _local_reason
    from trcc.adapters.system import host_platform_class

    monkeypatch.setenv(_ENV_FLAG, "1")
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    platform = _build_dev_platform(specs)

    assert _local_reason(platform) == (
        f"{type(platform).__name__} is a stand-in, not this host's "
        f"{host_platform_class().__name__}")


# ── A dev or test run is offline unless asked ────────────────────────────────

_SPEC = {"vid": "87ad", "pid": "70db", "pm": 11, "sub": 5, "resolution": "854x480"}


@pytest.mark.parametrize("specs", [None, [_SPEC]], ids=["--hardware", "fleet"])
@pytest.mark.parametrize("online", [False, True])
def test_a_dev_platform_is_offline_unless_asked(
    specs: list[dict] | None, online: bool,
) -> None:
    """Every mock launch used to go online unasked: the gui's update check at
    startup and hourly, the data archives on each auto-connect, the dev
    console's prefetch of every resolution."""
    from trcc.adapters.repo.http import OfflineHttpFetcher, UrllibHttpFetcher

    fetcher = _build_dev_platform(specs, online=online).http_fetcher()

    assert type(fetcher) is (UrllibHttpFetcher if online else OfflineHttpFetcher)


@pytest.mark.parametrize("specs", [None, [_SPEC]], ids=["--hardware", "fleet"])
def test_a_dev_platform_refuses_the_real_setup(
    specs: list[dict] | None, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``dev/mock_cli.py system setup`` ran the host's real setup: sudo, /etc
    writes, module loads and a pip install into the dev interpreter.  Every
    one of those is stubbed to a recorder here, so even a regression is safe."""
    from types import SimpleNamespace

    from trcc.adapters.system import _elevate, linux

    touched: list[str] = []

    def recorder(name: str):
        return lambda *a, **kw: touched.append(name) or 0

    # Every name LinuxOS.setup reaches that changes the system.  setattr
    # RAISES on a name that moved, so a rename fails here instead of
    # leaving a real side effect unstubbed.
    for name in ("install_udev_rules", "install_matching_gpu_extras",
                 "install_selinux_policy"):
        monkeypatch.setattr(linux, name, recorder(name))
    monkeypatch.setattr(linux, "XdgDesktopEntry",
                        lambda: SimpleNamespace(install=recorder("desktop entry"),
                                                path="(stub)"))
    monkeypatch.setattr(_elevate, "reexec_as_root", recorder("reexec_as_root"))

    code = _build_dev_platform(specs).setup(dry_run=False)

    assert touched == [], f"the dev platform's setup reached {touched}"
    assert code != 0, "the dev platform's setup reported success"


def test_the_host_platform_stays_online() -> None:
    from trcc.adapters.repo.http import UrllibHttpFetcher
    from trcc.adapters.system import current_platform

    assert type(current_platform().http_fetcher()) is UrllibHttpFetcher


def test_an_app_on_a_mock_platform_asks_the_network_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The App built its own fetcher (``app.py``), so no platform could keep it
    offline.  Measured on a mock before the fix: 12 lookups for
    api.github.com, raw.githubusercontent.com and the czhorde mirrors.  DNS is
    recorded as well as connect -- the suite's own guard only stops connect,
    and a lookup with no network has no time limit."""
    import socket

    from tests.mock_platform import MockPlatform
    from trcc.app import App
    from trcc.core.commands import CheckForUpdate, DownloadCloudTheme

    asked: list[object] = []

    def refuse_lookup(host: object, *_a: object, **_k: object) -> object:
        asked.append(host)
        raise socket.gaierror("refused by the test")

    def refuse_connect(_sock: object, address: object) -> None:
        asked.append(address)
        raise ConnectionRefusedError("refused by the test")

    monkeypatch.setattr(socket, "getaddrinfo", refuse_lookup)
    monkeypatch.setattr(socket.socket, "connect", refuse_connect)
    app = App(MockPlatform([_SPEC], tmp_path))

    update = app.dispatch(CheckForUpdate())
    cloud = app.dispatch(DownloadCloudTheme(theme_id="a001", resolution=(854, 480)))

    assert (update.ok, cloud.ok) == (False, False)
    assert asked == []


def test_the_suite_refuses_a_dns_lookup() -> None:
    """The session guard (tests/conftest.py) stopped connect, not the lookup
    before it, so a test reaching urllib still sent a real DNS query."""
    import socket

    with pytest.raises(socket.gaierror) as refused:
        socket.getaddrinfo("example.invalid", 443)

    assert str(refused.value) == (
        "the trcc test suite is hermetic — blocked a DNS lookup of "
        "'example.invalid'.  Stub the port (HttpFetcher, DataInstallService, "
        "GithubReleases) instead of reaching the network.")



@pytest.mark.parametrize("specs", [None, [_SPEC]], ids=["--hardware", "fleet"])
def test_a_dev_platforms_login_entry_is_not_the_users(
    specs: list[dict] | None,
) -> None:
    """The host's adapter wrote the user's REAL ~/.config/autostart entry,
    and the gui enables or refreshes it on launch -- so a dev run with a
    fresh dev/.trcc switched the user's autostart back on.

    MUTATION CHECK: drop the mixin from a dev class and its entry is the
    user's.
    """
    from _mock_bootstrap import DEV_TRCC

    platform = _build_dev_platform(specs)

    assert Path(platform.autostart().entry_location()).is_relative_to(DEV_TRCC)
