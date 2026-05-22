"""System setup and administration commands."""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

from trcc.legacy._boot import trcc
from trcc.legacy.core.platform import detect_install_method, is_root

log = logging.getLogger(__name__)


def run_setup(auto_yes: bool = False) -> int:
    """Run interactive platform setup. OS handles everything."""
    return trcc().os.run_setup(auto_yes=auto_yes)


# Backward-compat alias — _system.setup() call sites in cli/__init__.py
setup = run_setup


def _sudo_run(cmd):
    """Run a command with sudo prepended. Returns subprocess.CompletedProcess."""
    return subprocess.run(["sudo", *cmd])


def show_info(builder=None, *, preview: bool = False, metric: str | None = None):
    """Show system metrics, optionally as ANSI terminal art."""
    try:
        from trcc.legacy.services.system import format_metric
        from trcc.legacy.ui.cli import _ensure_system

        log.debug("show_info preview=%s metric=%s", preview, metric)
        _ensure_system(builder)
        metrics = trcc().os.metrics

        if preview:
            from trcc.legacy.services import ImageService
            print(ImageService.metrics_to_ansi(metrics, group=metric))
            return 0

        # Text output (original behavior)
        print("System Information")
        print("=" * 40)

        groups = [
            ("CPU", ['cpu_temp', 'cpu_percent', 'cpu_freq', 'cpu_power']),
            ("GPU", ['gpu_temp', 'gpu_usage', 'gpu_clock', 'gpu_power']),
            ("Memory", ['mem_temp', 'mem_percent', 'mem_clock', 'mem_available']),
            ("Disk", ['disk_temp', 'disk_activity', 'disk_read', 'disk_write']),
            ("Network", ['net_up', 'net_down', 'net_total_up', 'net_total_down']),
            ("Fan", ['fan_cpu', 'fan_gpu', 'fan_ssd', 'fan_sys2']),
            ("Date/Time", ['date', 'time', 'weekday']),
        ]

        # Filter if metric specified
        if metric:
            key = metric.lower()
            alias = {'mem': 'Memory', 'cpu': 'CPU', 'gpu': 'GPU',
                     'disk': 'Disk', 'net': 'Network', 'fan': 'Fan',
                     'time': 'Date/Time'}
            target = alias.get(key)
            if target:
                groups = [(lb, ks) for lb, ks in groups if lb == target]

        for label, keys in groups:
            print(f"\n{label}:")
            for key in keys:
                if key in metrics._populated:
                    val = getattr(metrics, key, 0.0)
                    print(f"  {key}: {format_metric(key, val)}")

        return 0
    except Exception as e:
        print(f"Error getting metrics: {e}")
        return 1





def _is_externally_managed() -> bool:
    """Check if the Python environment has PEP 668 EXTERNALLY-MANAGED marker."""
    stdlib = Path(os.__file__).parent
    return (stdlib / "EXTERNALLY-MANAGED").exists()


def uninstall(*, yes: bool = False):
    """Remove all TRCC config, udev rules, autostart, and desktop files."""
    log.debug("uninstall yes=%s", yes)
    from trcc.legacy.conf import Settings

    # Clear resolution markers before wiping config dir
    Settings.clear_installed_resolutions()

    home = Path.home()

    # Files that require root to remove (platform-specific)
    from trcc.legacy._boot import trcc as _trcc
    root_files = _trcc().os.get_system_files()

    # User files/dirs to remove
    user_items = [
        home / ".trcc",
    ]
    # Glob for any trcc desktop files in applications dir (keeps app menu clean)
    applications = home / ".local" / "share" / "applications"
    if applications.is_dir():
        user_items.extend(applications.glob("trcc*.desktop"))

    removed = []

    # Handle root files — auto-elevate with sudo if needed
    root_exists = [p for p in root_files if os.path.exists(p)]
    if root_exists and not is_root():
        print("Root files found — requesting sudo to remove...")
        result = _sudo_run(["rm", "-f", *root_exists])
        if result.returncode == 0:
            removed.extend(root_exists)
            _sudo_run(["udevadm", "control", "--reload-rules"])
            _sudo_run(["udevadm", "trigger"])
    else:
        for path_str in root_exists:
            os.remove(path_str)
            removed.append(path_str)

    # Disable autostart before shutting down logging
    from trcc.legacy._boot import trcc as _trcc
    platform = _trcc().os
    if platform.autostart_enabled():
        platform.autostart_disable()
        removed.append("autostart entry")

    # Shut down logging before deleting ~/.trcc — remove file handlers
    # so subsequent log calls don't try to reopen the deleted log file
    import logging as _logging
    root = _logging.getLogger()
    for h in list(root.handlers):
        if isinstance(h, _logging.FileHandler):
            root.removeHandler(h)
            h.close()
    _logging.shutdown()

    # Handle user files/dirs
    for path in user_items:
        if path.exists():
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
            removed.append(str(path))

    if removed:
        print("Removed:")
        for item in removed:
            print(f"  {item}")
    else:
        print("Nothing to remove — TRCC is already clean.")

    # Reload udev if we removed rules (and we're root)
    if is_root() and any("udev" in r for r in removed):
        subprocess.run(["udevadm", "control", "--reload-rules"], check=False)
        subprocess.run(["udevadm", "trigger"], check=False)

    # Detect install method and uninstall the package accordingly
    install_info = Settings.get_install_info()
    method = install_info.get('method', detect_install_method())

    if method in ('pacman', 'dnf', 'apt'):
        pkg_cmds = {
            'pacman': 'sudo pacman -R trcc-linux',
            'dnf': 'sudo dnf remove trcc-linux',
            'apt': 'sudo apt remove trcc-linux',
        }
        print(f"\nInstalled via {method} — remove with:")
        print(f"  {pkg_cmds[method]}")
    elif method == 'pipx':
        print("\nUninstalling trcc-linux via pipx...")
        subprocess.run(["pipx", "uninstall", "trcc-linux"], check=False)
    else:
        print("\nUninstalling trcc-linux pip package...")
        pip_cmd = [sys.executable, "-m", "pip", "uninstall", "trcc-linux"]
        if yes:
            pip_cmd.append("--yes")
        if _is_externally_managed():
            pip_cmd.append("--break-system-packages")
        subprocess.run(pip_cmd, check=False)

    # Clean stale shadow binary from old pip/pipx installs
    stale_bin = Path.home() / ".local" / "bin" / "trcc"
    if stale_bin.exists():
        stale_bin.unlink()
        print(f"Removed stale binary: {stale_bin}")

    return 0


def report(detect_fn=None):
    """Generate a full diagnostic report for bug reports."""
    log.debug("collecting diagnostic report")
    from trcc.legacy._boot import trcc as _trcc
    from trcc.legacy.adapters.infra.debug_report import DebugReport
    from trcc.legacy.adapters.infra.doctor import run_doctor

    # Boot the singleton Trcc — wires the SCSI transport factory (and
    # everything else) as a side effect, so DebugReport.collect()'s
    # _handshake_scsi pass can succeed.  Replaces the previous
    # `DeviceProtocolFactory.set_scsi_transport(...)` direct wire.
    _trcc()

    rpt = DebugReport(detect_fn=detect_fn)
    rpt.collect()
    print(rpt)
    run_doctor()
    print("Copy everything above and paste it into your GitHub issue:")
    print("  https://github.com/Lexonight1/thermalright-trcc-linux/issues/new")
    return 0


def download_themes(pack=None, show_list=False, force=False, show_info=False):
    """Download theme packs (like spacy download)."""
    log.debug("download_themes pack=%s show_list=%s force=%s", pack, show_list, force)
    try:
        if show_info and pack:
            from trcc.legacy.adapters.infra.theme_downloader import show_info as pack_info
            pack_info(pack)
            return 0

        if force:
            from trcc.legacy.conf import Settings
            Settings.clear_installed_resolutions()

        from trcc.legacy._boot import trcc
        dispatch_pack = "" if show_list else (pack or "")
        return trcc().download_themes(dispatch_pack, force)

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        return 1


def _confirm(prompt: str, auto_yes: bool) -> bool:
    """Ask [Y/n] question. Returns True on yes/enter, False on n."""
    if auto_yes:
        print(f"  {prompt} [Y/n]: y (auto)")
        return True
    try:
        answer = input(f"  {prompt} [Y/n]: ").strip().lower()
        return answer in ('', 'y', 'yes')
    except (EOFError, KeyboardInterrupt):
        print()
        return False


