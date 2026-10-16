"""The Windows uninstaller removes every autostart the app can create.

An autostart's NAME is its identity: the uninstaller can only delete what it
names exactly.  The sign-in task arrived in 78a7f568 and the uninstaller never
learned of it, so uninstalling left a task that ran a deleted program at every
sign-in.  The names live in ``_autostart.py``; this keeps the installer in step.
"""
from __future__ import annotations

import re
from pathlib import Path

from trcc.adapters.system import _autostart

_ISS = Path(__file__).resolve().parent.parent / "installer" / "trcc.iss"


def _uninstall_step() -> str:
    """The body of ``CurUninstallStepChanged``, where the clean-up runs."""
    text = _ISS.read_text(encoding="utf-8")
    found = re.search(r"procedure CurUninstallStepChanged.*?\nend;", text, re.S)
    assert found, "the installer lost its uninstall step"
    return found.group(0)


def test_the_uninstaller_deletes_the_sign_in_task() -> None:
    """MUTATION CHECK: rename ``_TASK_NAME`` and this fails."""
    step = _uninstall_step()

    assert f'/delete /tn "{_autostart._TASK_NAME}" /f' in step


def test_the_uninstaller_deletes_the_run_value() -> None:
    step = _uninstall_step()
    run_key = _autostart._WIN_RUN_KEY_PATH

    assert f"'{run_key}',\n      '{_autostart._DEFAULT_VALUE_NAME}');" in step


def test_setup_broadcasts_the_path_change() -> None:
    """#218: the installer adds itself to the machine PATH, but without
    ChangesEnvironment Setup never broadcasts WM_SETTINGCHANGE -- Explorer
    and every terminal it opens keep the old PATH, so 'trcc' is "not
    recognized" until the user signs out."""
    iss = _ISS.read_text(encoding="utf-8")
    setup = iss.split("[Setup]", 1)[1].split("\n[", 1)[0]

    assert "Session Manager\\Environment" in iss          # it does edit PATH
    assert "ChangesEnvironment=yes" in setup.splitlines()


def test_setup_installs_pawnio_silently_unless_it_is_there() -> None:
    """Without PawnIO, Windows gives TRCC no CPU temperature or power (#319),
    so setup lays it down -- no question asked, skipped when another app
    already did, and never removed: other monitors share it."""
    iss = _ISS.read_text(encoding="utf-8")
    assert ('Filename: "{tmp}\\PawnIO_setup.exe"; Parameters: "-install -silent"'
            in iss)
    assert "Check: not PawnIOInstalled" in iss
    assert ("RegKeyExists(HKLM, 'SOFTWARE\\Microsoft\\Windows\\CurrentVersion"
            "\\Uninstall\\PawnIO')") in iss
    uninstall = iss.split("[UninstallRun]", 1)[1].split("[Code]", 1)[0]
    assert "PawnIO" not in uninstall


def test_the_build_ships_the_modules_where_the_code_looks() -> None:
    """The folder and file names live in the code and in windows.yml; this
    is what keeps the two from drifting apart.  Both downloads stay pinned.

    MUTATION CHECK: rename _IntelPackage.MODULE."""
    from trcc.adapters.sensors._pawnio_cpu import (
        MODULE_DIR_NAME,
        _AmdPackage,
        _IntelPackage,
    )
    workflow = (_ISS.parent.parent / ".github" / "workflows"
                / "windows.yml").read_text(encoding="utf-8")
    for module in (_IntelPackage.MODULE, _AmdPackage.MODULE):
        assert f'"{module}"' in workflow
        assert f"dist/trcc/{MODULE_DIR_NAME}/$f" in workflow
    assert "43608CB89BC84247FEF1368A139013F7D043E17DB6D6C8DFC9B46BF0905A81F4" in workflow
    assert "1F519A22E47187F70A1379A48CA604981C4FCF694F4E65B734AAA74A9FBA3032" in workflow


def test_setup_stops_only_the_lhm_an_older_trcc_launched() -> None:
    """Killing the old TRCC orphans the LibreHardwareMonitor it launched,
    which then holds {app}\\lhm open so [InstallDelete] cannot remove it --
    measured in the VM, 2026-10-16.  It is stopped by PATH: a user's own
    LHM, run from anywhere else, is never touched.

    MUTATION CHECK: replace it with taskkill /IM LibreHardwareMonitor.exe."""
    iss = _ISS.read_text(encoding="utf-8")
    kill = iss.split("procedure KillRunningProcesses;", 1)[1].split("end;", 1)[0]
    assert "Where-Object { $_.Path -like '''" in kill
    assert "ExpandConstant('{app}') + '\\lhm\\*'' }" in kill
    assert "LibreHardwareMonitor.exe" not in kill
    assert 'Type: filesandordirs; Name: "{app}\\lhm"' in iss


def test_uninstall_removes_the_data_folder_windows_trcc_really_uses(
        monkeypatch) -> None:
    """WindowsPaths keeps TRCC's data in %APPDATA%\\trcc; the uninstaller
    named only ~/.trcc, an older home, so an uninstall left the real data
    behind.  The user's own content in %LOCALAPPDATA%\\trcc-user stays.

    MUTATION CHECK: rename WindowsPaths' root folder."""
    from trcc.adapters.system.windows import WindowsPaths

    monkeypatch.setenv("APPDATA", "C:\\Users\\u\\AppData\\Roaming")
    monkeypatch.setenv("LOCALAPPDATA", "C:\\Users\\u\\AppData\\Local")
    paths = WindowsPaths()
    iss = _ISS.read_text(encoding="utf-8")
    section = iss.split("[UninstallDelete]", 1)[1].split("\n[", 1)[0]
    removed = "\n".join(line for line in section.splitlines()
                        if line.startswith("Type:"))
    assert f'Name: "{{userappdata}}\\{paths._root.name}"' in removed
    assert paths._user_content.name not in removed
