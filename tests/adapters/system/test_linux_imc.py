"""Live IMC timing reader + override wiring in the Linux platform adapter,
and the privileged-helper path every root-only probe goes through (#312)."""
from __future__ import annotations

import shutil
import subprocess
import xml.etree.ElementTree as ET
from importlib.machinery import SourceFileLoader
from pathlib import Path
from types import ModuleType

import pytest

from trcc.adapters.system import linux
from trcc.adapters.system._imc_timings import ImcTimings

# Real helper stdout (hex registers captured from the dev box).
_HELPER_OUT = ("tc_pre=0x141307500422028 odt=0x8026280000 "
               "refresh=0x5fc1249 bios_ddr=0x79b81118")


@pytest.fixture(autouse=True)
def _reset_cache() -> None:
    linux._live_imc_cache = linux._UNREAD
    yield
    linux._live_imc_cache = linux._UNREAD


def _force_supported(monkeypatch: pytest.MonkeyPatch) -> None:
    """CPU is ADL/RPL, helper present, running as root (skip pkexec/policy)."""
    monkeypatch.setattr(linux, "_cpu_is_adl_rpl", lambda: True)
    monkeypatch.setattr(linux.Path, "is_file", lambda self: True)
    monkeypatch.setattr(linux.os, "geteuid", lambda: 0)


def test_reader_decodes_helper_output(monkeypatch: pytest.MonkeyPatch) -> None:
    _force_supported(monkeypatch)
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=_HELPER_OUT),
    )

    t = linux._read_live_imc_timings()

    assert t is not None
    assert (t.mts, t.tcas, t.trcd, t.trp, t.tras, t.trc) == (4800, 40, 40, 40, 76, 116)


def test_reader_caches_one_helper_call(monkeypatch: pytest.MonkeyPatch) -> None:
    _force_supported(monkeypatch)
    calls = {"n": 0}

    def _run(*a, **k):
        calls["n"] += 1
        return subprocess.CompletedProcess(a, 0, stdout=_HELPER_OUT)

    monkeypatch.setattr(subprocess, "run", _run)

    linux._read_live_imc_timings()
    linux._read_live_imc_timings()

    assert calls["n"] == 1   # second call served from the module cache


def test_reader_skips_unsupported_cpu(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(linux, "_cpu_is_adl_rpl", lambda: False)

    def _boom(*a, **k):
        raise AssertionError("must not spawn the helper on an unsupported CPU")

    monkeypatch.setattr(subprocess, "run", _boom)

    assert linux._read_live_imc_timings() is None


def test_reader_skips_when_helper_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(linux, "_cpu_is_adl_rpl", lambda: True)
    monkeypatch.setattr(linux.Path, "is_file", lambda self: False)

    def _boom(*a, **k):
        raise AssertionError("must not spawn the helper when it isn't installed")

    monkeypatch.setattr(subprocess, "run", _boom)

    assert linux._read_live_imc_timings() is None


def test_reader_env_var_disables(monkeypatch: pytest.MonkeyPatch) -> None:
    _force_supported(monkeypatch)
    monkeypatch.setenv("TRCC_DISABLE_LIVE_IMC", "1")

    def _boom(*a, **k):
        raise AssertionError("must not spawn the helper when disabled")

    monkeypatch.setattr(subprocess, "run", _boom)

    assert linux._read_live_imc_timings() is None


def test_reader_none_on_helper_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _force_supported(monkeypatch)
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 1, stdout=""),
    )

    assert linux._read_live_imc_timings() is None


def test_reader_none_on_garbage_output(monkeypatch: pytest.MonkeyPatch) -> None:
    _force_supported(monkeypatch)
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout="tc_pre=nope"),
    )

    assert linux._read_live_imc_timings() is None


def test_enrich_overrides_spd_but_keeps_trfc(monkeypatch: pytest.MonkeyPatch) -> None:
    live = ImcTimings(mts=6000, tcas=30, tcwl=36, trcd=38, trp=38,
                      tras=70, trc=108, trfc=560)
    monkeypatch.setattr(linux, "_read_live_imc_timings", lambda: live)

    slots = [{
        "size": "16 GiB", "tcas": "40", "trcd": "40", "trp": "40",
        "tras": "77", "trc": "117", "trfc": "709",
    }]
    linux._enrich_with_live_imc_timings(slots)

    assert slots[0]["tcas"] == "30"
    assert slots[0]["trcd"] == "38"
    assert slots[0]["tras"] == "70"
    assert slots[0]["trc"] == "108"
    assert slots[0]["trfc"] == "709"   # SPD tRFC1 kept (live is tRFC2)
    assert slots[0]["size"] == "16 GiB"


def test_enrich_noop_when_no_live(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(linux, "_read_live_imc_timings", lambda: None)
    slots = [{"tcas": "40", "trfc": "709"}]

    linux._enrich_with_live_imc_timings(slots)

    assert slots == [{"tcas": "40", "trfc": "709"}]


# ── Privileged helpers and the polkit policy they need ───────────────────
#
# pkexec does no validation of the ARGUMENTS it is given (``man 1 pkexec``), so
# an ``allow_active=yes`` action on a general-purpose program hands every
# process in the session that program as root, with any arguments: the old
# policy did that for dmidecode (``--dump-bin FILE`` creates a root-owned file
# anywhere) and smartctl (``-s``/``--set`` change drive state).  Measured on the
# dev box before the fix: ``pkcheck --action-id
# com.github.lexonight1.trcc.dmidecode --process $$`` -> ``polkit.result=yes``.
# The policy may only name OUR helpers, each of which refuses every argument.

_ASSETS = Path(linux.__file__).resolve().parents[2] / "assets"
_POLICY = _ASSETS / "com.github.lexonight1.trcc.policy"
_EXEC_PATH = "org.freedesktop.policykit.exec.path"
_RELEASE_YML = (Path(__file__).resolve().parents[3] / ".github" / "workflows"
                / "release.yml")


def _policy_exec_paths() -> set[str]:
    """Every program the shipped policy lets pkexec run."""
    return {a.text or "" for a in ET.parse(_POLICY).getroot().iter("annotate")
            if a.get("key") == _EXEC_PATH}


def _load_helper(name: str) -> ModuleType:
    """Import a helper script from ``assets/`` without running it."""
    loader = SourceFileLoader(f"_helper_{name.replace('-', '_')}",
                              str(_ASSETS / name))
    module = ModuleType(loader.name)
    loader.exec_module(module)
    return module


def test_the_policy_runs_only_our_own_helpers() -> None:
    """MUTATION CHECK: re-add a dmidecode action to the policy -> this fails."""
    paths = _policy_exec_paths()
    assert paths, "the instrument read no exec.path from the policy"

    foreign = {p for p in paths if not (p.startswith("/usr/bin/trcc-")
                                        and (_ASSETS / Path(p).name).is_file())}

    assert foreign == set()
    assert {linux._IMC_HELPER, linux._DMI_HELPER} <= paths


@pytest.mark.parametrize("path", sorted(_policy_exec_paths()))
def test_every_policy_helper_refuses_arguments(
        path: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """A caller-supplied argument is the whole attack, so the helper must stop
    before it spawns or opens anything.
    MUTATION CHECK: delete a helper's argv guard -> this fails.
    """
    helper = _load_helper(Path(path).name)

    class _Refused:
        """Stands in for the helper's own ``os`` / ``subprocess`` binding --
        never the global modules, which the rest of the process still uses."""

        def __getattr__(self, name: str):
            raise AssertionError(
                f"the helper used {name} on a refused invocation")

    for module in ("subprocess", "os"):
        if hasattr(helper, module):
            monkeypatch.setattr(helper, module, _Refused())
    monkeypatch.setattr(helper.sys, "argv", [path, "--dump-bin", "/tmp/x"])

    assert helper.main() == 2


_DMIDECODE_17 = """\
# dmidecode 3.6
Getting SMBIOS data from sysfs.
SMBIOS 3.5.0 present.

Handle 0x0040, DMI type 17, 92 bytes
Memory Device
\tArray Handle: 0x003F
\tTotal Width: 64 bits
\tData Width: 64 bits
\tSize: 16 GB
\tForm Factor: DIMM
\tLocator: DIMMA2
\tType: DDR5
\tSpeed: 4800 MT/s
\tManufacturer: Corsair
\tSerial Number: 00000000
\tAsset Tag: 9876543210
\tPart Number: CMH32GX5M2B6000C30
\tRank: 1
\tConfigured Memory Speed: 6000 MT/s
\tConfigured Voltage: 1.4 V

Handle 0x0041, DMI type 17, 92 bytes
Memory Device
\tSize: No Module Installed
\tLocator: DIMMA1
\tSerial Number: Not Specified
"""


def test_trcc_dmi_passes_only_the_fields_trcc_reads() -> None:
    """The helper runs as root, so what it prints is what it discloses: the
    memory fields, never a serial number or asset tag."""
    out = _load_helper("trcc-dmi").filter_memory_devices(_DMIDECODE_17)

    assert "Serial Number" not in out
    assert "Asset Tag" not in out
    assert "Array Handle" not in out
    slots = linux._parse_dmi_memory(out)
    assert len(slots) == 1
    assert slots[0]["configured_memory_speed"] == "6000 MT/s"
    assert slots[0]["part_number"] == "CMH32GX5M2B6000C30"


def test_the_helper_and_the_parser_keep_the_same_fields() -> None:
    """One fact in two files (the helper imports nothing from trcc): gated."""
    fields = _load_helper("trcc-dmi").FIELDS
    normalised = {f.lower().replace(" ", "_") for f in fields}

    assert normalised == set(linux._DMI_MEMORY_FIELDS)


def _record_run(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    calls: list[list[str]] = []

    def _run(argv, *a, **k):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout="")

    monkeypatch.setattr(subprocess, "run", _run)
    return calls


def test_a_user_reads_dmi_only_through_the_helper(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """MUTATION CHECK: hand pkexec ``dmidecode`` again -> this fails."""
    helper = tmp_path / "trcc-dmi"
    helper.write_text("")
    policy = tmp_path / "trcc.policy"
    policy.write_text("")
    monkeypatch.setattr(linux.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(linux, "_DMI_HELPER", str(helper))
    monkeypatch.setattr(linux, "_POLKIT_POLICY", str(policy))
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    calls = _record_run(monkeypatch)

    linux._dmi_memory_slots()

    assert calls == [["pkexec", str(helper)]]


def test_without_the_helper_a_user_runs_nothing(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A pip install has no helper: no spawn, so never a password prompt."""
    monkeypatch.setattr(linux.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(linux, "_DMI_HELPER", str(tmp_path / "absent"))
    calls = _record_run(monkeypatch)

    assert linux._dmi_memory_slots() == []
    assert calls == []


def test_root_reads_dmidecode_itself(monkeypatch: pytest.MonkeyPatch) -> None:
    """Root needs no helper, and a pip install run as root has none."""
    monkeypatch.setattr(linux.os, "geteuid", lambda: 0)
    calls = _record_run(monkeypatch)

    linux._dmi_memory_slots()

    assert calls == [["dmidecode", "-t", "17"]]


def test_smartctl_is_never_elevated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(linux.os, "geteuid", lambda: 1000)
    calls = _record_run(monkeypatch)

    linux._smart_health("sda")

    assert calls == [["smartctl", "-H", "/dev/sda"]]


_OLD_RULE = """\
// TRCC Linux — passwordless dmidecode/smartctl for installing user
polkit.addRule(function(action, subject) {
    if ((action.id == "com.github.lexonight1.trcc.dmidecode" ||
         action.id == "com.github.lexonight1.trcc.smartctl") &&
        subject.user == "someone") {
        return polkit.Result.YES;
    }
});
"""


def _legacy_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                  rule: str, policy: str) -> tuple[Path, Path]:
    rule_file = tmp_path / "50-trcc.rules"
    rule_file.write_text(rule)
    policy_file = tmp_path / "com.github.lexonight1.trcc.policy"
    policy_file.write_text(policy)
    monkeypatch.setattr(linux, "_LEGACY_POLKIT_RULE", str(rule_file))
    monkeypatch.setattr(linux, "_POLKIT_POLICY", str(policy_file))
    monkeypatch.setattr(linux.os, "geteuid", lambda: 0)
    return rule_file, policy_file


def test_setup_retires_the_old_grants(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """v5.3.3-v9.6.5 ``setup-polkit`` wrote a rule granting dmidecode/smartctl
    in ANY session (SSH too), and a policy naming the programs themselves;
    both outlive an upgrade on a pip install."""
    old_policy = _POLICY.read_text(encoding="utf-8").replace(
        "/usr/bin/trcc-dmi", "/usr/bin/dmidecode")
    rule_file, policy_file = _legacy_paths(tmp_path, monkeypatch,
                                           _OLD_RULE, old_policy)

    assert linux.retire_legacy_polkit() == 0

    assert not rule_file.exists()
    assert not policy_file.exists()


def test_setup_keeps_what_is_not_an_old_trcc_grant(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Somebody else's rule under our file name, and the current policy."""
    rule_file, policy_file = _legacy_paths(
        tmp_path, monkeypatch, "// the admin's own rule\n",
        _POLICY.read_text(encoding="utf-8"))

    assert linux.retire_legacy_polkit() == 0

    assert rule_file.exists()
    assert policy_file.exists()


def _release_blocks() -> dict[str, str]:
    """release.yml cut into the four package-build steps, by step name."""
    text = _RELEASE_YML.read_text(encoding="utf-8")
    steps = {"rpm": "- name: Build RPM (Fedora)",
             "deb": "- name: Build DEB (Ubuntu/Debian)",
             "legacy": "- name: Build DEB legacy",
             "arch": "- name: Build Arch package"}
    starts = {name: text.index(mark) for name, mark in steps.items()}
    end = text.index("- name: Fix package directory permissions")
    bounds = [*sorted(starts.values()), end]
    return {name: text[at: bounds[bounds.index(at) + 1]]
            for name, at in starts.items()}


def test_every_package_installs_every_policy_helper() -> None:
    """A policy action whose helper is not installed is a dead feature."""
    helpers = {Path(p).name for p in _policy_exec_paths()}
    blocks = _release_blocks()

    for helper in helpers:
        for name, block in blocks.items():
            assert f"/src/src/trcc/assets/{helper} " in block, (
                f"{name} does not install {helper}")
        assert f"/usr/bin/{helper}\n" in blocks["rpm"], (
            f"{helper} missing from the RPM %files")


def test_every_package_removes_the_old_rule_and_names_dmidecode() -> None:
    """The packages replace the policy file themselves; the old rule file in
    /etc is nobody's, so each package's install script removes it -- only when
    it names our actions."""
    for name, block in _release_blocks().items():
        assert "50-trcc.rules" in block, f"{name}: old rule not removed"
        assert "com.github.lexonight1.trcc" in block, f"{name}: unsigned rm"
        assert "dmidecode" in block, f"{name}: dmidecode not declared"


# ── trcc-ram-access: the one helper that GRANTS something ───────────────────

def _policy_action(action_id: str) -> ET.Element:
    action = next((a for a in ET.parse(_POLICY).getroot().iter("action")
                   if a.get("id") == action_id), None)
    assert action is not None, f"no {action_id} in the policy"
    return action


def test_ram_access_asks_for_a_password_every_time() -> None:
    """The action opens the memory's bus to the seat's user.  ``yes`` here
    would let any program in the session grant itself that without a word.

    MUTATION CHECK: set any default to ``yes`` or ``auth_admin_keep``."""
    action = _policy_action("com.github.lexonight1.trcc.ram-access")
    defaults_element = action.find("defaults")
    assert defaults_element is not None
    defaults = {d.tag: d.text for d in defaults_element}
    assert defaults == {"allow_any": "auth_admin",
                        "allow_inactive": "auth_admin",
                        "allow_active": "auth_admin"}
    exec_path = [a.text for a in action.iter("annotate")
                 if a.get("key") == _EXEC_PATH]
    assert exec_path == ["/usr/bin/trcc-ram-access"]


def test_ram_access_grants_the_bus_trcc_drives_and_no_other() -> None:
    """The rule's name match is the RAM driver's: access goes to exactly the
    bus TRCC talks to -- never the GPU's DDC or the DesignWare buses."""
    from trcc.adapters.rgb.smbus import SMBUS_PREFIX
    helper = _load_helper("trcc-ram-access")
    assert helper.SMBUS_PREFIX == SMBUS_PREFIX
    rule = [line for line in helper.RULE_TEXT.splitlines()
            if not line.startswith("#")]
    assert rule == [
        'SUBSYSTEM=="i2c-dev", ATTR{name}=="SMBus*", TAG+="uaccess"']
    # Before 73-seat-late.rules, where udev turns the tag into an ACL.
    assert helper.RULE_PATH.name < "73"
    assert helper.MODULES_TEXT.splitlines()[1:] == ["i2c-dev"]


def test_ram_access_writes_and_removes_only_its_own_files(tmp_path: Path) -> None:
    helper = _load_helper("trcc-ram-access")
    helper.write(tmp_path)
    rule = tmp_path / "etc/udev/rules.d/70-trcc-ram-lighting.rules"
    modules = tmp_path / "etc/modules-load.d/trcc-i2c.conf"
    assert rule.read_text(encoding="utf-8") == helper.RULE_TEXT
    assert modules.read_text(encoding="utf-8") == helper.MODULES_TEXT
    assert oct(rule.stat().st_mode & 0o777) == "0o644"
    helper.remove(tmp_path)
    assert not rule.exists() and not modules.exists()
    # An admin's file at the same path is not ours to delete.
    rule.write_text("# my own rule\n", encoding="utf-8")
    helper.remove(tmp_path)
    assert rule.read_text(encoding="utf-8") == "# my own rule\n"


def test_ram_access_finds_only_the_chipset_smbus(tmp_path: Path) -> None:
    helper = _load_helper("trcc-ram-access")
    for n, name in ((0, "Synopsys DesignWare I2C adapter"),
                    (3, "SMBus I801 adapter at 0000:00:1f.4"),
                    (4, "NVIDIA i2c adapter 1 at 1:00.0"),
                    (9, "SMBus PIIX4 adapter port 0 at 0b00")):
        (tmp_path / f"i2c-{n}").mkdir()
        (tmp_path / f"i2c-{n}" / "name").write_text(name + "\n", encoding="utf-8")
    assert helper.smbus_nodes(tmp_path) == ["/dev/i2c-3", "/dev/i2c-9"]


def test_ram_access_disable_takes_the_access_back_now(
        monkeypatch: pytest.MonkeyPatch) -> None:
    """udev only ever ADDS a uaccess grant, so disable strips the SMBus nodes'
    ACLs before re-triggering -- otherwise access lasts until the next login.

    MUTATION CHECK: drop the setfacl loop in ``apply``."""
    helper = _load_helper("trcc-ram-access")
    ran: list[tuple[str, ...]] = []
    monkeypatch.setattr(helper, "_tool", lambda name: f"/x/{name}")
    monkeypatch.setattr(helper, "_run", lambda *argv: ran.append(argv) or True)
    monkeypatch.setattr(helper, "smbus_nodes", lambda: ["/dev/i2c-3"])
    assert helper.apply(False)
    assert ran == [
        ("/x/udevadm", "control", "--reload"),
        ("/x/setfacl", "-b", "/dev/i2c-3"),
        ("/x/udevadm", "trigger", "--subsystem-match=i2c-dev", "--action=change"),
        ("/x/udevadm", "settle", "--timeout=10")]
    ran.clear()
    assert helper.apply(True)
    assert ran[0] == ("/x/modprobe", "i2c-dev")
    assert not [r for r in ran if "setfacl" in r[0]]


@pytest.mark.parametrize("argv, code", [
    (["enable", "disable"], 2), (["ENABLE"], 2), ([], 2), (["enable"], 3)],
    ids=["two-words", "wrong-case", "none", "not-root"])
def test_ram_access_refuses_before_touching_anything(
        argv: list[str], code: int, monkeypatch: pytest.MonkeyPatch) -> None:
    helper = _load_helper("trcc-ram-access")
    monkeypatch.setattr(helper.sys, "argv", ["trcc-ram-access", *argv])
    monkeypatch.setattr(helper.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(helper, "write", lambda *a: pytest.fail("wrote"))
    monkeypatch.setattr(helper, "apply", lambda *a: pytest.fail("applied"))
    assert helper.main() == code


#: Each package's uninstall hook: the line that must run there, never on upgrade.
_UNINSTALL_HOOKS = {
    "rpm": ("%preun", "if [ \\$1 -eq 0 ]; then /usr/bin/trcc-ram-access disable"),
    "deb": ("DEBIAN/prerm", 'if [ "\\$1" = remove ]; then /usr/bin/trcc-ram-access disable'),
    "legacy": ("DEBIAN/prerm", 'if [ "\\$1" = remove ]; then /usr/bin/trcc-ram-access disable'),
    "arch": ("pre_remove()", "/usr/bin/trcc-ram-access disable"),
}


@pytest.mark.parametrize("package", sorted(_UNINSTALL_HOOKS))
def test_every_package_takes_the_ram_grant_back_on_uninstall(package: str) -> None:
    """The helper writes its rule into /etc, where no package owns it: without
    a hook, uninstalling TRCC would leave the RAM bus open to the seat's user.

    MUTATION CHECK: delete any package's hook line."""
    hook, line = _UNINSTALL_HOOKS[package]
    block = _release_blocks()[package]
    assert hook in block, f"{package}: no {hook}"
    assert line in block.split(hook, 1)[1], f"{package}: {hook} lacks the disable"


def test_trcc_reads_the_rule_the_helper_writes() -> None:
    """The helper may import nothing from trcc, so the path, header and
    install location are spelled twice -- and must agree."""
    from trcc.adapters.system import _ram_access
    helper = _load_helper("trcc-ram-access")
    assert _ram_access.RULE_PATH == helper.RULE_PATH
    assert _ram_access.HEADER == helper.HEADER
    assert _ram_access.HELPER == "/usr/bin/trcc-ram-access"
    assert _ram_access.HELPER_ASSET == _ASSETS / "trcc-ram-access"
