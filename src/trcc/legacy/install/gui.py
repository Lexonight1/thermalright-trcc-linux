"""TRCC Linux — Setup Wizard GUI.

Standalone PySide6 window that wraps ``trcc setup`` checks.
Install buttons run CLI commands via QProcess and stream output
to an embedded terminal pane.

Can run independently of trcc — if the package is not installed,
shows only the pip install step. After installing, Re-check reveals
the full system checks.
"""

from __future__ import annotations

import importlib.metadata
import os
import shutil
import sys
from pathlib import Path

from PySide6.QtCore import QProcess, Slot
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

# ── Status colours ────────────────────────────────────────────────────

_C_OK = '#2ecc71'
_C_MISS = '#e74c3c'
_C_OPT = '#f39c12'
_C_GREY = '#95a5a6'


def _trcc_version() -> str:
    """Return installed trcc-linux version or empty string."""
    try:
        return importlib.metadata.version('trcc-linux')
    except importlib.metadata.PackageNotFoundError:
        return ''


def _distro_name() -> str:
    """Best-effort distro name without requiring trcc."""
    try:
        with open('/etc/os-release') as f:
            for line in f:
                if line.startswith('PRETTY_NAME='):
                    return line.split('=', 1)[1].strip().strip('"')
    except OSError:
        pass
    return 'Linux'


# ── Single dependency row ─────────────────────────────────────────────

class _DepRow(QWidget):
    """Status icon + name + optional [Install] button."""

    def __init__(
        self,
        name: str,
        ok: bool,
        required: bool,
        version: str = '',
        note: str = '',
        install_cmd: str = '',
    ) -> None:
        super().__init__()
        self.dep_name = name
        self.ok = ok
        self.install_cmd = install_cmd

        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 2, 4, 2)

        # Tag
        if ok:
            tag, colour = '[OK]', _C_OK
        elif required:
            tag, colour = '[!!]', _C_MISS
        else:
            tag, colour = '[--]', _C_OPT
        lbl_tag = QLabel(tag)
        lbl_tag.setStyleSheet(
            f'color:{colour}; font-weight:bold; font-family:monospace;'
        )
        lbl_tag.setFixedWidth(36)
        lay.addWidget(lbl_tag)

        # Name / version / note
        text = name
        if version:
            text += f'  {version}'
        if note and not ok:
            text += f'  \u2014  {note}'
        lay.addWidget(QLabel(text), stretch=1)

        # Install button (only when actionable)
        self.btn: QPushButton | None = None
        if not ok and install_cmd:
            self.btn = QPushButton('Install')
            self.btn.setFixedWidth(80)
            lay.addWidget(self.btn)


# ── Wizard window ─────────────────────────────────────────────────────

class SetupWizard(QWidget):
    """Interactive setup wizard — mirrors ``trcc setup`` with a GUI."""

    def __init__(self) -> None:
        super().__init__()
        self._process: QProcess | None = None
        self._queue: list[str] = []
        self._rows: list[_DepRow] = []
        self._build_ui()
        self._run_checks()

    # ── UI construction ───────────────────────────────────────────────

    def _build_ui(self) -> None:
        self.setWindowTitle('TRCC Setup')
        self.setMinimumSize(620, 520)
        self.resize(720, 620)

        # Center on screen
        if (screen := QApplication.primaryScreen()):
            geo = screen.availableGeometry()
            self.move(
                geo.x() + (geo.width() - self.width()) // 2,
                geo.y() + (geo.height() - self.height()) // 2,
            )

        root = QVBoxLayout(self)
        root.setSpacing(10)

        # Header — works without trcc
        hdr = QLabel(f'TRCC Setup \u2014 {_distro_name()}')
        hdr.setStyleSheet('font-size:15px; font-weight:bold; padding:6px 0;')
        root.addWidget(hdr)

        # Scrollable checks area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMaximumHeight(320)
        self._checks_w = QWidget()
        self._checks_lay = QVBoxLayout(self._checks_w)
        self._checks_lay.setSpacing(2)
        self._checks_lay.setContentsMargins(4, 4, 4, 4)
        scroll.setWidget(self._checks_w)
        root.addWidget(scroll)

        # Terminal output
        lbl = QLabel('Output:')
        lbl.setStyleSheet('font-weight:bold;')
        root.addWidget(lbl)

        self._term = QPlainTextEdit()
        self._term.setReadOnly(True)
        self._term.setFont(QFont('monospace', 10))
        self._term.setStyleSheet(
            'QPlainTextEdit{background:#1e1e1e; color:#d4d4d4; padding:6px;}'
        )
        root.addWidget(self._term, stretch=1)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        self._all_btn = QPushButton('Install All Missing')
        self._all_btn.clicked.connect(self._on_install_all)
        btn_row.addWidget(self._all_btn)

        recheck = QPushButton('Re-check')
        recheck.clicked.connect(self._on_recheck)
        btn_row.addWidget(recheck)

        uninstall_btn = QPushButton('Uninstall')
        uninstall_btn.setStyleSheet(f'color:{_C_MISS};')
        uninstall_btn.clicked.connect(self._on_uninstall)
        btn_row.addWidget(uninstall_btn)

        close_btn = QPushButton('Close')
        close_btn.clicked.connect(self.close)
        btn_row.addWidget(close_btn)

        root.addLayout(btn_row)

    # ── Dependency checks ─────────────────────────────────────────────

    def _run_checks(self) -> None:
        """Populate the checks panel.

        If trcc is installed, uses doctor.py for full system checks.
        If not, shows a dialog offering to install it first.
        """
        # Clear previous
        self._rows.clear()
        while self._checks_lay.count():
            item = self._checks_lay.takeAt(0)
            if item is None:
                continue
            if (w := item.widget()) is not None:
                w.deleteLater()

        if not (ver := _trcc_version()):
            self._prompt_install()
            return

        self._section(f'TRCC {ver}')
        self._run_full_checks()

        self._checks_lay.addStretch()
        self._all_btn.setEnabled(any(r.btn for r in self._rows))
        self._log_summary()

    def _prompt_install(self) -> None:
        """Show a dialog when trcc-linux is not installed."""
        reply = QMessageBox.question(
            self, 'TRCC Not Installed',
            'trcc-linux is not installed.\n\n'
            'Install it now via pip?',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._exec(self._gui_cmd('pip install trcc-linux'))
        else:
            lbl = QLabel('trcc-linux is not installed.')
            lbl.setStyleSheet(f'color:{_C_MISS}; font-weight:bold; padding:8px;')
            self._checks_lay.addWidget(lbl)
            self._checks_lay.addStretch()

    def _run_full_checks(self) -> None:
        """Full system checks — only called when trcc is importable.

        Uses DoctorPlatformConfig flags to show only OS-relevant checks.
        """
        from trcc.legacy.adapters.infra.doctor import (
            check_desktop_entry,
            check_gpu,
            check_selinux,
            check_system_deps,
            check_udev,
            get_setup_info,
        )
        from trcc.legacy.adapters.system import PlatformFactory

        config = PlatformFactory.current().doctor_config()
        info = get_setup_info(config)

        # System deps — always (already platform-aware via DoctorPlatformConfig)
        self._section('System Dependencies')
        for d in check_system_deps(info.pkg_manager, config):
            self._add_dep(
                d.name, d.ok, d.required, d.version, d.note, d.install_cmd,
            )

        # GPU — Linux sysfs only
        if config.run_gpu_check:
            self._section('GPU Detection')
            if not (gpus := check_gpu()):
                lbl = QLabel('    No discrete GPU detected')
                lbl.setStyleSheet(f'color:{_C_GREY};')
                self._checks_lay.addWidget(lbl)
            for g in gpus:
                self._add_dep(
                    g.label, g.package_installed, False,
                    install_cmd=g.install_cmd,
                )

        # udev — Linux only
        if config.run_udev_check:
            self._section('USB Device Permissions')
            udev = check_udev()
            udev_cmd = (
                '' if udev.ok
                else 'sudo ' + self._trcc_prefix() + ' setup-udev'
            )
            self._add_dep(
                'udev rules', udev.ok, True,
                note='' if udev.ok else udev.message,
                install_cmd=udev_cmd,
            )

        # SELinux — Linux only
        if config.run_selinux_check:
            se = check_selinux()
            if se.enforcing:
                self._section('SELinux Policy')
                se_cmd = (
                    '' if se.ok
                    else 'sudo ' + self._trcc_prefix() + ' setup-selinux'
                )
                self._add_dep(
                    'SELinux USB policy', se.ok, True,
                    note='' if se.ok else se.message,
                    install_cmd=se_cmd,
                )

        # Desktop entry — Linux only (.desktop files)
        if config.run_udev_check:
            self._section('Desktop Integration')
            desk = check_desktop_entry()
            desk_cmd = '' if desk else self._trcc_prefix() + ' install-desktop'
            self._add_dep(
                'Application menu entry', desk, False,
                install_cmd=desk_cmd,
            )

    def _section(self, title: str) -> None:
        lbl = QLabel(title)
        lbl.setStyleSheet('font-weight:bold; margin-top:6px;')
        self._checks_lay.addWidget(lbl)

    def _add_dep(
        self,
        name: str,
        ok: bool,
        required: bool,
        version: str = '',
        note: str = '',
        install_cmd: str = '',
    ) -> None:
        gui_cmd = self._gui_cmd(install_cmd) if install_cmd else ''
        # Non-actionable hint — show it in note instead of a button
        if not ok and install_cmd and not gui_cmd:
            note = install_cmd if not note else f'{note} \u2014 {install_cmd}'
        row = _DepRow(name, ok, required, version, note, gui_cmd)
        if row.btn:
            row.btn.clicked.connect(
                lambda _=False, c=gui_cmd: self._exec(c)
            )
        self._checks_lay.addWidget(row)
        self._rows.append(row)

    # ── Command adaptation ────────────────────────────────────────────

    @staticmethod
    def _trcc_prefix() -> str:
        if shutil.which('trcc'):
            return 'trcc'
        return f'{sys.executable} -m trcc.cli'

    @staticmethod
    def _trcc_pythonpath() -> str:
        """PYTHONPATH for root commands — includes trcc + deps.

        For pip installs, trcc and all deps share site-packages → one path.
        For source runs, src/ has trcc but deps are in user site-packages
        → include both so root can find typer, PySide6, etc.
        """
        import site
        # __file__ = .../src/trcc/install/gui.py → 2 parents up = .../src/
        trcc_root = str(Path(__file__).resolve().parents[2])
        user_sp = site.getusersitepackages()
        if user_sp and os.path.isdir(user_sp) and user_sp != trcc_root:
            return f'{trcc_root}:{user_sp}'
        return trcc_root

    @staticmethod
    def _gui_cmd(cli_cmd: str) -> str:
        """Adapt a CLI install command for GUI execution.

        - ``pip install X``  -> ``{python} -m pip install X``
        - ``sudo trcc …``   -> ``pkexec env PYTHONPATH=… {python} -m trcc.cli …``
        - ``install ...``    -> ``''``  (non-actionable hint)
        """
        if cli_cmd.startswith('pip install'):
            pkg = cli_cmd[len('pip install '):]
            return f'{sys.executable} -m pip install {pkg}'
        if cli_cmd.startswith('sudo '):
            inner = cli_cmd[5:]
            pypath = SetupWizard._trcc_pythonpath()
            # Replace `trcc <subcmd>` with full python invocation
            if inner.startswith('trcc '):
                subcmd = inner[5:]
                return (
                    f'pkexec env PYTHONPATH={pypath}'
                    f' {sys.executable} -m trcc.cli {subcmd}'
                )
            return f'pkexec env PYTHONPATH={pypath} {inner}'
        if cli_cmd.startswith('install '):
            return ''
        return cli_cmd

    # ── Process execution ─────────────────────────────────────────────

    def _exec(self, cmd: str) -> None:
        """Run *cmd* via QProcess, streaming output to terminal."""
        if (
            self._process
            and self._process.state() != QProcess.ProcessState.NotRunning
        ):
            self._queue.append(cmd)
            self._log(f'[queued] {cmd}')
            return

        self._log(f'$ {cmd}')
        p = QProcess(self)
        p.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        p.readyReadStandardOutput.connect(self._on_stdout)
        p.finished.connect(self._on_finished)
        self._process = p
        p.start('bash', ['-c', cmd])

    @Slot()
    def _on_stdout(self) -> None:
        if not self._process:
            return
        data = bytes(self._process.readAllStandardOutput().data()).decode(
            errors='replace',
        )
        cursor = self._term.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self._term.setTextCursor(cursor)
        self._term.insertPlainText(data)
        self._term.ensureCursorVisible()

    def _on_finished(self, code: int, _status: QProcess.ExitStatus) -> None:
        self._log('[done]\n' if code == 0 else f'[failed \u2014 exit {code}]\n')
        if self._queue:
            self._exec(self._queue.pop(0))
        elif code == 0:
            self._run_checks()

    def _log(self, text: str) -> None:
        self._term.appendPlainText(text)

    def _log_summary(self) -> None:
        """Log a text summary of all check results to the terminal."""
        for row in self._rows:
            tag = '[OK]' if row.ok else '[!!]'
            self._log(f'  {tag}  {row.dep_name}')
        ok_count = sum(1 for r in self._rows if r.ok)
        self._log(f'{ok_count}/{len(self._rows)} checks passed\n')

    # ── Button handlers ───────────────────────────────────────────────

    @Slot()
    def _on_install_all(self) -> None:
        for row in self._rows:
            if row.btn and row.install_cmd:
                self._exec(row.install_cmd)

    @Slot()
    def _on_recheck(self) -> None:
        self._log('--- Re-checking ---\n')
        self._run_checks()

    @Slot()
    def _on_uninstall(self) -> None:
        reply = QMessageBox.warning(
            self, 'Confirm Uninstall',
            'This will remove TRCC config, udev rules, desktop entry,\n'
            'and the pip package. Continue?',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._log('--- Uninstalling TRCC ---')
        cmd = 'sudo ' + self._trcc_prefix() + ' uninstall --yes'
        self._exec(self._gui_cmd(cmd))


# ── Entry point ───────────────────────────────────────────────────────

def main() -> int:
    """Launch the setup wizard GUI."""
    import signal

    app = QApplication.instance() or QApplication(sys.argv)
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    w = SetupWizard()
    w.show()
    w.raise_()
    w.activateWindow()
    return app.exec()
