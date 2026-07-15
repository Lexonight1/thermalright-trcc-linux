"""Install-integrity collector — the "which trcc is actually running?" facts.

The stale-bytecode test below reproduces the real mechanism rather than
mocking it: a ``.pyc`` whose recorded mtime+size still match the source
is treated as authoritative by Python, so a same-length edit to the source
is served from cache forever.  That is not hypothetical — it had this repo
importing 9.9.9 from a file that read 9.8.8.
"""
from __future__ import annotations

import compileall
import importlib
import sys
from pathlib import Path

import pytest

from trcc.adapters.diagnostics.install import (
    Executable,
    InstallInfo,
    _find_executables,
    _read_source_version,
    collect_install_info,
)


def _info(**over) -> InstallInfo:
    base = dict(
        version="9.8.8", source_version="9.8.8", module_path=None,
        interpreter="/usr/bin/python3", python="3.12.13", installer="pip",
        executables=(),
    )
    base.update(over)
    return InstallInfo(**base)  # type: ignore[arg-type]


# ── bytecode_stale ────────────────────────────────────────────────────

def test_bytecode_stale_when_import_disagrees_with_source() -> None:
    assert _info(version="9.9.9", source_version="9.8.8").bytecode_stale is True


def test_bytecode_not_stale_when_they_agree() -> None:
    assert _info(version="9.8.8", source_version="9.8.8").bytecode_stale is False


def test_bytecode_stale_is_false_when_source_unreadable() -> None:
    """Unknown source must not masquerade as a mismatch — no false alarms."""
    assert _info(version="9.8.8", source_version="unknown").bytecode_stale is False


# ── duplicates / healthy ──────────────────────────────────────────────

def test_duplicates_false_for_single_executable() -> None:
    assert _info(executables=(Executable(Path("/usr/bin/trcc")),)).duplicates is False


def test_duplicates_true_for_two_executables() -> None:
    info = _info(executables=(
        Executable(Path("/home/u/.local/bin/trcc"), "/usr/bin/python3.12"),
        Executable(Path("/usr/bin/trcc"), "/usr/bin/python"),
    ))
    assert info.duplicates is True
    assert info.healthy is False


def test_healthy_only_when_single_and_fresh() -> None:
    assert _info(executables=(Executable(Path("/usr/bin/trcc")),)).healthy is True


# ── _read_source_version ──────────────────────────────────────────────

@pytest.mark.parametrize("body,expected", [
    ('__version__ = "9.8.8"\n', "9.8.8"),
    ("__version__ = '1.2.3'\n", "1.2.3"),
    ('"""doc"""\n__version__ = "0.1.0"\nother = 1\n', "0.1.0"),
    ("version = '9.9.9'\n", "unknown"),          # not the __version__ name
    ("", "unknown"),                              # empty file
])
def test_read_source_version_parses(tmp_path: Path, body: str, expected: str) -> None:
    (tmp_path / "__version__.py").write_text(body)
    assert _read_source_version(tmp_path) == expected


def test_read_source_version_missing_file_is_unknown(tmp_path: Path) -> None:
    assert _read_source_version(tmp_path) == "unknown"


def test_read_source_version_no_module_path_is_unknown() -> None:
    assert _read_source_version(None) == "unknown"


def test_read_source_version_does_not_execute_the_file(tmp_path: Path) -> None:
    """Text-parsed, never imported — an import would consult the very cache
    we are checking, and would always agree with itself."""
    (tmp_path / "__version__.py").write_text(
        'raise SystemExit("must not run")\n__version__ = "9.8.8"\n'
    )
    assert _read_source_version(tmp_path) == "9.8.8"


# ── the real thing: a genuinely stale .pyc ────────────────────────────

def test_stale_pyc_is_detected_against_the_real_mechanism(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reproduce the cache lie, then prove the collector's logic catches it.

    Same-length version strings keep the source's size identical, and we
    restore its mtime, so Python's timestamp check passes and serves the
    stale bytecode.  The import says 9.9.9; the file says 9.8.8.
    """
    pkg = tmp_path / "stalepkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("from .__version__ import __version__\n")
    version_py = pkg / "__version__.py"

    version_py.write_text('__version__ = "9.9.9"\n')
    stat_before = version_py.stat()
    compileall.compile_dir(str(pkg), quiet=1)

    # Same byte length, mtime restored → the .pyc still looks current.
    version_py.write_text('__version__ = "9.8.8"\n')
    import os
    os.utime(version_py, (stat_before.st_atime, stat_before.st_mtime))
    assert version_py.stat().st_size == stat_before.st_size

    monkeypatch.syspath_prepend(str(tmp_path))
    for mod in [m for m in sys.modules if m.startswith("stalepkg")]:
        del sys.modules[mod]
    imported = importlib.import_module("stalepkg")

    # The cache is lying: import says 9.9.9, the file on disk says 9.8.8.
    assert imported.__version__ == "9.9.9", "cache did not go stale — test is void"
    assert _read_source_version(pkg) == "9.8.8"

    # That disagreement is exactly what bytecode_stale reports.
    assert _info(version=imported.__version__,
                 source_version=_read_source_version(pkg)).bytecode_stale is True


# ── PATH scanning ─────────────────────────────────────────────────────

def test_find_executables_reports_every_match_not_just_the_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """shutil.which stops at the first hit — which is what hides the bug."""
    import os
    first, second = tmp_path / "a", tmp_path / "b"
    for d, interp in ((first, "/usr/bin/python3.12"), (second, "/usr/bin/python")):
        d.mkdir()
        exe = d / "trcc"
        exe.write_text(f"#!{interp}\n")
        exe.chmod(0o755)
    monkeypatch.setenv("PATH", os.pathsep.join([str(first), str(second)]))

    found = _find_executables("trcc")
    assert len(found) == 2
    assert [str(e.path) for e in found] == [str(first / "trcc"), str(second / "trcc")]
    assert [e.interpreter for e in found] == ["/usr/bin/python3.12", "/usr/bin/python"]


def test_find_executables_empty_when_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    assert _find_executables("trcc") == ()


def test_find_executables_skips_non_executable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "trcc").write_text("#!/usr/bin/python3\n")  # no +x
    monkeypatch.setenv("PATH", str(tmp_path))
    assert _find_executables("trcc") == ()


# ── end to end ────────────────────────────────────────────────────────

def test_collect_install_info_describes_the_running_package() -> None:
    import trcc
    info = collect_install_info()
    assert info.version == trcc.__version__
    assert info.module_path is not None
    assert (info.module_path / "__version__.py").is_file()
    assert info.interpreter == sys.executable
    assert info.bytecode_stale is False, (
        f"this checkout is serving stale bytecode: imported {info.version} but "
        f"{info.module_path} says {info.source_version} — clear __pycache__"
    )


# ── report rendering ──────────────────────────────────────────────────

def test_render_install_warns_loudly_on_stale_bytecode() -> None:
    from trcc.adapters.diagnostics.debug_report import _render_install
    out = _render_install(_info(version="9.9.9", source_version="9.8.8",
                                module_path=Path("/x/trcc")))
    assert "STALE BYTECODE" in out
    assert "9.9.9" in out and "9.8.8" in out
    assert "__pycache__" in out


def test_render_install_warns_on_duplicate_binaries() -> None:
    from trcc.adapters.diagnostics.debug_report import _render_install
    out = _render_install(_info(executables=(
        Executable(Path("/home/u/.local/bin/trcc"), "/usr/bin/python3.12"),
        Executable(Path("/usr/bin/trcc"), "/usr/bin/python"),
    )))
    assert "2 trcc found on PATH" in out
    assert "/home/u/.local/bin/trcc" in out and "/usr/bin/trcc" in out


def test_render_install_is_quiet_when_healthy() -> None:
    from trcc.adapters.diagnostics.debug_report import _render_install
    out = _render_install(_info(executables=(Executable(Path("/usr/bin/trcc")),)))
    assert "!!" not in out
    assert "9.8.8" in out


def test_render_install_handles_collection_failure() -> None:
    from trcc.adapters.diagnostics.debug_report import _render_install
    assert "(unavailable)" in _render_install(None)


def test_report_leads_with_install_section(tmp_path: Path) -> None:
    """Install must come first — every later section is suspect if it's wrong."""
    import trcc
    from tests.mock_platform import MockPlatform
    from trcc.adapters.diagnostics.debug_report import build_debug_report

    text = build_debug_report(MockPlatform([], tmp_path)).render_text()

    assert "## Install" in text
    assert text.index("## Install") < text.index("## Platform"), \
        "Install section must precede Platform"
    # and it must carry the facts that were missing when this was written
    assert trcc.__version__ in text
    assert "interpreter" in text and "installed_by" in text


# ── doctor check ──────────────────────────────────────────────────────

def test_health_check_fails_on_stale_bytecode(monkeypatch: pytest.MonkeyPatch) -> None:
    from trcc.adapters.diagnostics import health
    monkeypatch.setattr(health, "collect_install_info",
                        lambda: _info(version="9.9.9", source_version="9.8.8"))
    r = health.check_install_integrity()
    assert r.severity == "FAIL"
    assert "Stale bytecode" in r.message
    assert "__pycache__" in r.fix_hint


def test_health_check_warns_on_duplicates(monkeypatch: pytest.MonkeyPatch) -> None:
    from trcc.adapters.diagnostics import health
    dupes = _info(executables=(
        Executable(Path("/home/u/.local/bin/trcc")), Executable(Path("/usr/bin/trcc")),
    ))
    monkeypatch.setattr(health, "collect_install_info", lambda: dupes)
    r = health.check_install_integrity()
    assert r.severity == "WARN"
    assert "2 trcc on PATH" in r.message
    assert "/home/u/.local/bin/trcc" in r.fix_hint  # names the one that wins


def test_health_check_ok_when_clean(monkeypatch: pytest.MonkeyPatch) -> None:
    from trcc.adapters.diagnostics import health
    monkeypatch.setattr(health, "collect_install_info",
                        lambda: _info(executables=(Executable(Path("/usr/bin/trcc")),)))
    r = health.check_install_integrity()
    assert r.severity == "OK"


def test_health_check_never_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """A broken install must still produce a report, not a crash."""
    from trcc.adapters.diagnostics import health

    def _boom() -> None:
        raise RuntimeError("no metadata")

    monkeypatch.setattr(health, "collect_install_info", _boom)
    r = health.check_install_integrity()
    assert r.severity == "WARN"
    assert "no metadata" in r.message


def test_install_integrity_runs_in_the_doctor(monkeypatch: pytest.MonkeyPatch) -> None:
    """Registration = inclusion: the check must actually be in the suite."""
    import tempfile

    from tests.mock_platform import MockPlatform
    from trcc.adapters.diagnostics.health import run_health_checks
    with tempfile.TemporaryDirectory() as td:
        report = run_health_checks(MockPlatform([], Path(td)))
    assert any(c.name == "install-integrity" for c in report.checks)
