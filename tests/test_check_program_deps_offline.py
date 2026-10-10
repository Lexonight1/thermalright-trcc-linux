"""The release's dependency check says "unverified", never a false STALE.

Release step 3 runs ``dev/tools/check_program_deps.py`` against the live
package indexes, and a STALE row blocks the release.  A network error read as
"package absent": one failed request to Fedora's mdapi on 2026-10-09 produced
a STALE row for a package Fedora 44 ships, and forcing mdapi to fail produced
four.  Deliberately optional deps (recorded with their reasons) also came back
as GAPs on every run, so the output always said "N claims need attention".
All offline: the network is faked.
"""
from __future__ import annotations

import io
import sys
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dev" / "tools"))
import check_program_deps as deps


def _network_down(monkeypatch: pytest.MonkeyPatch) -> None:
    def urlopen(*a, **k):
        raise urllib.error.URLError("timed out")
    monkeypatch.setattr(deps.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(deps.time, "sleep", lambda s: None)


def test_a_network_error_is_unreachable_not_absent(monkeypatch) -> None:
    """MUTATION CHECK: return None on a URLError again -> fails."""
    _network_down(monkeypatch)

    with pytest.raises(deps.Unreachable):
        deps.in_fedora("python3-python-multipart")


def test_a_404_is_still_absent(monkeypatch) -> None:
    def urlopen(url, *a, **k):
        raise urllib.error.HTTPError(url, 404, "Not Found", None, io.BytesIO(b""))
    monkeypatch.setattr(deps.urllib.request, "urlopen", urlopen)

    assert deps.in_fedora("no-such-package") is None


def test_mdapi_says_absent_with_a_400(monkeypatch) -> None:
    """Measured: mdapi answers ``400 Bad Request`` for ANY package it does
    not have (python3-nvidia-ml-py, no-such-package-xyz), 200 for one it has."""
    def urlopen(url, *a, **k):
        raise urllib.error.HTTPError(url, 400, "Bad Request", None, io.BytesIO(b""))
    monkeypatch.setattr(deps.urllib.request, "urlopen", urlopen)

    assert deps.in_fedora("python3-nvidia-ml-py") is None


def test_an_unreachable_index_makes_no_stale_row(monkeypatch, capsys) -> None:
    _network_down(monkeypatch)

    findings = deps.check()

    assert [f for f in findings if f.severity == "STALE"] == []
    assert {f.severity for f in findings} == {"UNVERIFIED"}


def test_a_deliberately_optional_dep_is_not_a_gap(monkeypatch, capsys) -> None:
    """Every index answers "present"; the optional deps stay quiet."""
    for probe in ("in_arch", "in_fedora", "in_ubuntu", "in_debian"):
        monkeypatch.setattr(deps, probe, lambda pkg: "present")
    monkeypatch.setattr(deps, "fedora_provides_module", lambda module: [])

    gaps = {f.dep for f in deps.check() if f.severity == "GAP"}

    assert gaps & set(deps.DELIBERATELY_OPTIONAL) == set()
