"""``trcc system setup`` installs TRCC's polkit policy and RAM helpers for a
pip / source install -- never over a distro package's copy."""
from __future__ import annotations

from pathlib import Path

import pytest

from trcc.adapters.system import _polkit_install
from trcc.adapters.system._polkit_install import FILES, install
from trcc.adapters.system._ram_access import HELPER, HELPER_OFF


@pytest.fixture
def as_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_polkit_install.os, "geteuid", lambda: 0)
    monkeypatch.setattr(_polkit_install, "reexec_as_root",
                        lambda _code: pytest.fail("re-ran as root while root"))


def _files(tmp_path: Path) -> dict[Path, tuple[Path, int]]:
    """The real sources, aimed at a sandbox instead of /usr."""
    return {tmp_path / dest.name: source for dest, source in FILES.items()}


def test_the_three_files_are_what_the_packages_install() -> None:
    assert set(FILES) == {Path(HELPER), Path(HELPER_OFF),
                          Path("/usr/share/polkit-1/actions/"
                               "com.github.lexonight1.trcc.policy")}
    assert all(src.is_file() for src, _mode in FILES.values())
    assert {mode for _src, mode in FILES.values()} == {0o755, 0o644}


def test_missing_files_are_installed_with_their_modes(tmp_path: Path, as_root) -> None:  # type: ignore[no-untyped-def]
    files = _files(tmp_path)
    assert install(files=files) == 0
    for dest, (src, mode) in files.items():
        assert dest.read_bytes() == src.read_bytes()
        assert dest.stat().st_mode & 0o777 == mode


def test_a_packages_copy_is_never_overwritten(tmp_path: Path, as_root) -> None:  # type: ignore[no-untyped-def]
    """MUTATION CHECK: drop the ``owns`` check in ``_needs``."""
    files = _files(tmp_path)
    for dest in files:
        dest.write_bytes(b"the package's own, older copy")
    assert install(files=files, owns=lambda path: "trcc-linux-9.10.0") == 0
    assert all(d.read_bytes() == b"the package's own, older copy" for d in files)


def test_an_older_copy_setup_made_is_updated(tmp_path: Path, as_root) -> None:  # type: ignore[no-untyped-def]
    files = _files(tmp_path)
    for dest in files:
        dest.write_bytes(b"an older setup's copy")
    assert install(files=files) == 0
    assert all(d.read_bytes() == s.read_bytes() for d, (s, _m) in files.items())


def test_current_files_need_no_root_at_all(tmp_path: Path,
                                           monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing to do is nothing to ask a password for."""
    files = _files(tmp_path)
    for dest, (src, _mode) in files.items():
        dest.write_bytes(src.read_bytes())
    monkeypatch.setattr(_polkit_install.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(_polkit_install, "reexec_as_root",
                        lambda _code: pytest.fail("asked for root with nothing to do"))
    assert install(files=files) == 0


def test_a_dry_run_writes_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    files = _files(tmp_path)
    assert install(dry_run=True, files=files) == 0
    assert not any(d.exists() for d in files)
    assert capsys.readouterr().out.count("--- would install") == 3
