"""Live IMC timing reader + override wiring in the Linux platform adapter,
and the privileged-helper path every root-only probe goes through (#312)."""
from __future__ import annotations

import ast
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

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


# ── Privileged helpers and the polkit policy they need (#312) ────────────

_POLICY = (Path(linux.__file__).resolve().parents[2] / "assets"
           / "com.github.lexonight1.trcc.policy")
_EXEC_PATH = "org.freedesktop.policykit.exec.path"


def _policy_exec_paths() -> set[str]:
    """Every program the shipped policy lets pkexec run password-free."""
    return {a.text or "" for a in ET.parse(_POLICY).getroot().iter("annotate")
            if a.get("key") == _EXEC_PATH}


def _privileged_tools() -> set[str]:
    """Every tool the adapter hands ``_privileged_cmd`` — derived, not listed."""
    tree = ast.parse(Path(linux.__file__).read_text(encoding="utf-8"))
    return {call.args[0].value for call in ast.walk(tree)
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
            and call.func.id == "_privileged_cmd" and call.args
            and isinstance(call.args[0], ast.Constant)}


def test_every_privileged_tool_is_declared_at_both_homes() -> None:
    """pkexec needs an exact match between the tool's resolved path and an
    ``exec.path`` — and that path is /usr/sbin/ on Debian and Ubuntu but
    /usr/bin/ where /usr/sbin is a symlink (Arch, Fedora 42+).  The policy
    named /usr/bin alone, so every Debian/Ubuntu launch asked for a password.
    MUTATION CHECK: drop a ``-sbin`` action from the policy → this fails.
    """
    tools = _privileged_tools()
    assert {"dmidecode", "smartctl"} <= tools     # the instrument sees them
    paths = _policy_exec_paths()

    missing = {f"{home}/{tool}" for tool in tools
               for home in ("/usr/bin", "/usr/sbin")} - paths

    assert missing == set()
    assert linux._IMC_HELPER in paths             # called by absolute path


def test_pkexec_is_handed_the_resolved_path(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Older pkexec compares the raw path it is given (no realpath), so a
    PATH hit through a symlink (``/sbin`` -> ``/usr/sbin``) matched nothing.
    MUTATION CHECK: pass ``shutil.which``'s result unresolved → this fails.
    """
    real = tmp_path / "usr" / "sbin" / "dmidecode"
    real.parent.mkdir(parents=True)
    real.write_text("")
    link = tmp_path / "sbin" / "dmidecode"
    link.parent.mkdir()
    link.symlink_to(real)
    policy = tmp_path / "trcc.policy"
    policy.write_text("")
    monkeypatch.setattr(linux.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(linux, "_POLKIT_POLICY", str(policy))
    monkeypatch.setattr(shutil, "which", lambda name: str(
        link if name == "dmidecode" else tmp_path / "pkexec"))

    cmd = linux._privileged_cmd("dmidecode", ["-t", "memory"])

    assert cmd == ["pkexec", str(real.resolve()), "-t", "memory"]
