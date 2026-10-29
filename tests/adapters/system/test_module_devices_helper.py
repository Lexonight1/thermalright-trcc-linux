"""``trcc-module-devices``: the root helper that lets this seat reach an
installed module's USB devices -- and its password-free ``-off`` twin.

Every test drives the helper's own functions against a temp root or a fake
sysfs; none runs it as root, and none touches the real ``/etc`` or ``/dev``.
"""
from __future__ import annotations

import subprocess
from importlib.machinery import SourceFileLoader
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from trcc.adapters.system import _module_devices, _udev, linux
from trcc.core.models import MODULE_DEVICE, MODULE_KEY

_ASSETS = Path(linux.__file__).resolve().parents[2] / "assets"
_INSTALL_SH = _ASSETS.parents[2] / "install.sh"


def _load(name: str) -> ModuleType:
    """Import a helper script from ``assets/`` without running it."""
    loader = SourceFileLoader(f"_helper_{name.replace('-', '_')}", str(_ASSETS / name))
    module = ModuleType(loader.name)
    loader.exec_module(module)
    return module


@pytest.fixture
def helper() -> ModuleType:
    return _load("trcc-module-devices")


# ── what it may be asked ──────────────────────────────────────────────────


def test_it_checks_keys_and_ids_as_trcc_does(helper: ModuleType) -> None:
    """It imports nothing from trcc, so the patterns, the paths and the group
    are spelled twice -- and must agree."""
    off = _load("trcc-module-devices-off")
    assert (helper.KEY.pattern, off.KEY.pattern, helper.DEVICE.pattern) == (
        MODULE_KEY.pattern, MODULE_KEY.pattern, MODULE_DEVICE.pattern)
    assert helper.GROUP == _udev.DEVICE_GROUP
    assert (helper.RULES_DIR, helper.RULE_GLOB, off.HELPER) == (
        _module_devices.RULES_DIR, _module_devices.RULE_GLOB, _module_devices.HELPER)
    assert (_module_devices.HELPER_ASSET.name, _module_devices.HELPER_OFF_ASSET.name) == (
        Path(_module_devices.HELPER).name, Path(_module_devices.HELPER_OFF).name)


@pytest.mark.parametrize(("argv", "parsed"), [
    (["enable", "weather", "0b05:19af"], ("enable", ["weather", "0b05:19af"])),
    (["enable", "weather", "0b05:19af", "0b05:1aa6"],
     ("enable", ["weather", "0b05:19af", "0b05:1aa6"])),
    (["disable", "weather"], ("disable", ["weather"])),
    (["disable-all"], ("disable-all", [])),
    ([], None),
    (["enable", "weather"], None),                         # no device
    (["enable", "Weather", "0b05:19af"], None),            # not a key
    (["enable", "../x", "0b05:19af"], None),
    (["enable", "weather", "0B05:19AF"], None),            # not as lsusb prints
    (["enable", "weather", '0b05:19af", RUN+="/bin/sh'], None),   # udev text
    (["enable", "weather", "0b05:19af", "0b05:19af"], None),      # twice
    (["enable", "weather", *[f"0b05:{n:04x}" for n in range(17)]], None),
    (["disable"], None),
    (["disable", "weather", "extra"], None),
    (["disable-all", "weather"], None),
    (["grant", "weather"], None),
])
def test_only_its_three_command_lines_are_accepted(
        helper: ModuleType, argv: list[str], parsed: Any) -> None:
    assert helper.parse(argv) == parsed


def test_a_refused_command_line_touches_nothing(
        helper: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    """Refused before it even asks whether it is root."""
    touched: list[str] = []
    monkeypatch.setattr(helper.os, "geteuid", lambda: touched.append("geteuid") or 0)
    monkeypatch.setattr(helper, "write", lambda *a: touched.append("write"))
    monkeypatch.setattr(helper.sys, "argv", ["trcc-module-devices", "enable", "weather"])
    assert (helper.main(), touched) == (2, [])


# ── what it writes ────────────────────────────────────────────────────────


def test_its_usb_lines_are_the_packaged_rgb_rules(helper: ModuleType) -> None:
    """The USB node line is ``70-trcc-rgb.rules``'s, built from ids alone."""
    packaged = [line for line in _udev.build_rgb_udev_rules().splitlines()
                if line.startswith('SUBSYSTEM=="usb"')]
    ids = list(dict.fromkeys(helper.devices_in("\n".join(packaged))))
    built = [line for line in helper.rule_text("rgb", ids).splitlines()
             if line.startswith('SUBSYSTEM=="usb"')]
    assert ids and built == packaged


def test_a_hidraw_node_is_matched_by_its_own_hid_device_never_an_ancestor(
        helper: ModuleType) -> None:
    """ATTRS searches every ancestor: a hub's ids would match the keyboard
    plugged into it (measured with udevadm test on a real desk, 2026-10-29).
    MUTATION CHECK: put ``ATTRS{idVendor}`` back on the hidraw line."""
    text = helper.rule_text("hub", ["05e3:0608"])
    assert "ATTRS" not in text
    assert ('SUBSYSTEM=="hidraw", KERNELS=="0003:05E3:0608.*", MODE="0660", '
            'GROUP="trcc", TAG+="uaccess"') in text.splitlines()


def test_it_writes_and_removes_only_its_own_file(
        helper: ModuleType, tmp_path: Path) -> None:
    helper.write("weather", ["0b05:19af", "0b05:1aa6"], tmp_path)
    rule = tmp_path / "etc/udev/rules.d/70-trcc-module-weather.rules"
    assert rule.read_text(encoding="utf-8").splitlines()[0] == helper.header("weather")
    assert oct(rule.stat().st_mode & 0o777) == "0o644"
    assert helper.keys_written(tmp_path) == ["weather"]
    assert helper.remove("weather", tmp_path) == ["0b05:19af", "0b05:1aa6"]
    assert not rule.exists()
    assert helper.remove("weather", tmp_path) is None


@pytest.mark.parametrize("first_line", [
    "# my own rule",
    "# TRCC module ticker -- written by trcc-module-devices; removing the module "
    "removes it.",
], ids=["an-admins", "another-modules-header"])
def test_a_file_without_its_header_for_that_key_is_never_removed(
        helper: ModuleType, tmp_path: Path, first_line: str) -> None:
    rule = tmp_path / "etc/udev/rules.d/70-trcc-module-weather.rules"
    rule.parent.mkdir(parents=True)
    rule.write_text(f"{first_line}\nSUBSYSTEM==\"usb\"\n", encoding="utf-8")
    assert helper.remove("weather", tmp_path) is None
    assert rule.exists()


def test_disable_touches_nothing_unless_trcc_granted(
        helper: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    """The password-free ``-off`` twin can be run by any program: on a
    machine where nothing was granted, it changes nothing at all."""
    applied: list[Any] = []
    monkeypatch.setattr(helper.os, "geteuid", lambda: 0)
    monkeypatch.setattr(helper, "remove", lambda key: None)
    monkeypatch.setattr(helper, "apply", lambda *a: applied.append(a) or True)
    monkeypatch.setattr(helper.sys, "argv", ["trcc-module-devices", "disable", "weather"])
    assert (helper.main(), applied) == (0, [])


def test_disable_all_takes_back_every_modules_devices(
        helper: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    helper.write("weather", ["0b05:19af"], tmp_path)
    helper.write("ticker", ["1b1c:0c32"], tmp_path)
    applied: list[Any] = []
    monkeypatch.setattr(helper.os, "geteuid", lambda: 0)
    keys_written, remove = helper.keys_written, helper.remove
    monkeypatch.setattr(helper, "keys_written", lambda: keys_written(tmp_path))
    monkeypatch.setattr(helper, "remove", lambda key: remove(key, tmp_path))
    monkeypatch.setattr(helper, "apply", lambda *a: applied.append(a) or True)
    monkeypatch.setattr(helper.sys, "argv", ["trcc-module-devices", "disable-all"])
    assert (helper.main(), applied) == (0, [(False, ["1b1c:0c32", "0b05:19af"])])
    assert keys_written(tmp_path) == []


# ── taking the access back now ────────────────────────────────────────────


def _usb(sysfs: Path, name: str, ids: str, bus: int, dev: int) -> Path:
    device = sysfs / "devices" / name
    device.mkdir(parents=True)
    vid, pid = ids.split(":")
    for attr, value in (("idVendor", vid), ("idProduct", pid),
                        ("busnum", str(bus)), ("devnum", str(dev))):
        (device / attr).write_text(f"{value}\n", encoding="utf-8")
    return device


def _hidraw(sysfs: Path, name: str, under: Path, ids: str) -> None:
    """A hidraw node of the HID device *ids*, plugged in at *under*."""
    vid, pid = ids.upper().split(":")
    interface = under / f"{under.name}:1.0" / f"0003:{vid}:{pid}.{name[-1]:0>4}"
    interface.mkdir(parents=True)
    node = sysfs / "class" / name
    node.mkdir(parents=True)
    (node / "device").symlink_to(interface)


def test_it_finds_the_nodes_of_the_devices_it_named(
        helper: ModuleType, tmp_path: Path) -> None:
    usb = tmp_path / "bus"
    usb.mkdir()
    aura = _usb(tmp_path, "1-4", "0b05:19af", 1, 3)
    other = _usb(tmp_path, "1-5", "046d:c52b", 1, 9)      # a keyboard receiver
    for device in (aura, other):
        (usb / device.name).symlink_to(device)
    _hidraw(tmp_path, "hidraw2", aura, "0b05:19af")
    _hidraw(tmp_path, "hidraw5", other, "046d:c52b")
    assert helper.device_nodes(["0b05:19af"], usb, tmp_path / "class") == [
        "/dev/bus/usb/001/003", "/dev/hidraw2"]


def test_a_hub_s_nodes_are_never_the_keyboard_plugged_into_it(
        helper: ModuleType, tmp_path: Path) -> None:
    """The real desk: a Genesys hub, a Dell keyboard below it.  Taking the hub
    back must not strip the keyboard's ACL entries -- they are not the hub's."""
    usb = tmp_path / "bus"
    usb.mkdir()
    hub = _usb(tmp_path, "1-11", "05e3:0608", 1, 3)
    keyboard = _usb(hub, "1-11.3", "413c:2113", 1, 5)
    for device in (hub, keyboard):
        (usb / device.name).symlink_to(device)
    _hidraw(tmp_path, "hidraw1", keyboard, "413c:2113")
    assert helper.device_nodes(["05e3:0608"], usb, tmp_path / "class") == [
        "/dev/bus/usb/001/003"]


def test_disable_strips_only_named_user_entries_then_retriggers(
        helper: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    ran: list[tuple[str, ...]] = []
    acls = {"/dev/hidraw2": "user::rw-\nuser:1000:rw-\ngroup::rw-\ngroup:1001:rw-\n",
            "/dev/bus/usb/001/003": "user::rw-\ngroup::rw-\n"}
    monkeypatch.setattr(helper, "_tool", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(helper, "device_nodes", lambda devices: list(acls))
    monkeypatch.setattr(helper, "_read", lambda *argv: acls[argv[-1]])
    monkeypatch.setattr(helper, "_run", lambda *argv: ran.append(argv) or True)
    assert helper.apply(False, ["0b05:19af"]) is True
    assert ran == [
        ("/usr/bin/udevadm", "control", "--reload"),
        ("/usr/bin/setfacl", "-x", "u:1000", "/dev/hidraw2"),
        ("/usr/bin/udevadm", "trigger", "--subsystem-match=hidraw", "--action=change"),
        ("/usr/bin/udevadm", "trigger", "--subsystem-match=usb",
         "--attr-match=idVendor=0b05", "--attr-match=idProduct=19af", "--action=change"),
        ("/usr/bin/udevadm", "settle", "--timeout=10")]


def test_enable_reloads_and_triggers_without_touching_acls(
        helper: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    ran: list[tuple[str, ...]] = []
    monkeypatch.setattr(helper, "_tool", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(helper, "_run", lambda *argv: ran.append(argv) or True)
    assert helper.apply(True, ["0b05:19af"]) is True
    assert [argv[0] for argv in ran] == ["/usr/bin/udevadm"] * 4


# ── the password-free twin ────────────────────────────────────────────────


@pytest.mark.parametrize(("argv", "code"), [
    ([], 2), (["--enable"], 2), (["disable-all"], 2), (["Weather"], 2),
    (["weather", "extra"], 2),
])
def test_the_off_twin_takes_one_key_and_nothing_else(
        monkeypatch: pytest.MonkeyPatch, argv: list[str], code: int) -> None:
    off = _load("trcc-module-devices-off")
    monkeypatch.setattr(off.sys, "argv", ["trcc-module-devices-off", *argv])
    monkeypatch.setattr(off.os, "execv", lambda *a: pytest.fail("ran the helper"))
    assert off.main() == code


def test_the_off_twin_only_ever_asks_for_disable(monkeypatch: pytest.MonkeyPatch) -> None:
    off = _load("trcc-module-devices-off")
    ran: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(off.os, "geteuid", lambda: 0)
    monkeypatch.setattr(off.os.path, "isfile", lambda path: True)
    monkeypatch.setattr(off.os, "execv", lambda path, argv: ran.append((path, argv)))
    monkeypatch.setattr(off.sys, "argv", ["trcc-module-devices-off", "weather"])
    off.main()
    assert ran == [("/usr/bin/trcc-module-devices",
                    ["/usr/bin/trcc-module-devices", "disable", "weather"])]


# ── install.sh --uninstall ────────────────────────────────────────────────


def _install_sh(script: str, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    functions = tmp_path / "install-functions.sh"
    functions.write_text(_INSTALL_SH.read_text(encoding="utf-8").replace(
        '\nmain "$@"\n', "\n"), encoding="utf-8")
    stubs = 'id() { echo 0; }\nsudo() { shift 2; "$@"; }\n'
    return subprocess.run(["bash", "-c", f'source "{functions}"\n{stubs}{script}'],
                          cwd=tmp_path, capture_output=True, text=True, timeout=60,
                          check=False)


def test_install_sh_spells_the_rules_and_helper_as_the_helper_does(
        helper: ModuleType) -> None:
    text = _INSTALL_SH.read_text(encoding="utf-8")
    assert f'MODULE_RULES="{helper.RULES_DIR / helper.RULE_GLOB}"' in text
    assert 'MODULE_DEVICES_HELPER="/usr/bin/trcc-module-devices"' in text
    body = text.split("do_uninstall() {", 1)[1]
    assert body.index("revoke_module_devices") < body.index("pip uninstall")


@pytest.mark.parametrize("granted", [True, False], ids=["granted", "never-granted"])
def test_install_sh_runs_the_helper_only_when_a_module_was_granted(
        tmp_path: Path, granted: bool) -> None:
    ran = tmp_path / "ran"
    stand_in = tmp_path / "helper"
    stand_in.write_text(f"import sys\nopen({str(ran)!r}, 'a').write("
                        "' '.join(sys.argv[1:]) + '\\n')\n")
    rules = tmp_path / "rules"
    rules.mkdir()
    if granted:
        (rules / "70-trcc-module-weather.rules").write_text("rule\n")
    result = _install_sh(f'MODULE_RULES="{rules}/70-trcc-module-*.rules"; '
                         f'MODULE_DEVICES_HELPER="{stand_in}"; revoke_module_devices',
                         tmp_path)
    assert result.returncode == 0, result.stderr
    assert (ran.read_text() if ran.exists() else "") == ("disable-all\n" if granted else "")

