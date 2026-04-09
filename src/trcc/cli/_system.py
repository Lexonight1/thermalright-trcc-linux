"""System setup and administration commands."""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

from trcc.core.platform import LINUX, detect_install_method, is_root

log = logging.getLogger(__name__)



def _require_linux(command: str) -> int | None:
    """Return error code if not on Linux, None if OK to proceed."""
    if not LINUX:
        log.debug("command '%s' skipped: not on Linux", command)
        print(f"'{command}' is for Linux only.")
        from trcc.core.builder import ControllerBuilder
        hint = ControllerBuilder.for_current_os().build_setup().linux_command_hint()
        if hint:
            print(hint)
        return 1
    return None


def _real_user_home():
    """Lazy proxy — only available on Linux."""
    from trcc.adapters.system.linux.setup import _real_user_home as _fn
    return _fn()


def setup_udev(dry_run: bool = False) -> int:
    """Install udev rules for LCD device access (Linux only)."""
    log.debug("setup_udev dry_run=%s", dry_run)
    err = _require_linux("setup-udev")
    if err is not None:
        return err
    from trcc.core.app import TrccApp
    return TrccApp.get().setup_udev(dry_run=dry_run)


def setup_selinux() -> int:
    """Install SELinux policy module (Linux only)."""
    err = _require_linux("setup-selinux")
    if err is not None:
        return err
    from trcc.core.app import TrccApp
    return TrccApp.get().setup_selinux()


def setup_polkit() -> int:
    """Install polkit policy for passwordless dmidecode/smartctl (Linux only)."""
    err = _require_linux("setup-polkit")
    if err is not None:
        return err
    from trcc.core.app import TrccApp
    return TrccApp.get().setup_polkit()


def install_desktop() -> int:
    """Install .desktop menu entry and icon (Linux only)."""
    err = _require_linux("install-desktop")
    if err is not None:
        return err
    from trcc.core.app import TrccApp
    return TrccApp.get().install_desktop()


def _sudo_run(cmd):
    """Run a command with sudo prepended. Returns subprocess.CompletedProcess."""
    return subprocess.run(["sudo"] + cmd)


def show_info(builder=None, *, preview: bool = False, metric: str | None = None):
    """Show system metrics, optionally as ANSI terminal art."""
    try:
        from trcc.cli import _ensure_system
        from trcc.services.system import format_metric, get_all_metrics

        log.debug("show_info preview=%s metric=%s", preview, metric)
        if builder is not None:
            _ensure_system(builder)
        metrics = get_all_metrics()

        if preview:
            from trcc.services import ImageService
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
                val = getattr(metrics, key, None)
                if val is not None and (val != 0.0 or key in ('date', 'time', 'weekday')):
                    print(f"  {key}: {format_metric(key, val)}")

        return 0
    except Exception as e:
        print(f"Error getting metrics: {e}")
        return 1


def setup_winusb() -> int:
    """Guide WinUSB driver installation for Thermalright USB devices (Windows only)."""
    from trcc.core.builder import ControllerBuilder
    if not ControllerBuilder.for_current_os().build_setup().supports_winusb():
        print("This command is for Windows only.")
        print("On Linux, use: trcc setup-udev")
        return 1
    from trcc.core.app import TrccApp
    return TrccApp.get().setup_winusb()




def _is_externally_managed() -> bool:
    """Check if the Python environment has PEP 668 EXTERNALLY-MANAGED marker."""
    stdlib = Path(os.__file__).parent
    return (stdlib / "EXTERNALLY-MANAGED").exists()


def uninstall(*, yes: bool = False):
    """Remove all TRCC config, udev rules, autostart, and desktop files."""
    log.debug("uninstall yes=%s", yes)
    from trcc.conf import Settings

    # Clear resolution markers before wiping config dir
    Settings.clear_installed_resolutions()

    home = _real_user_home()

    # Files that require root to remove (platform-specific)
    from trcc.core.builder import ControllerBuilder
    root_files = ControllerBuilder.for_current_os().build_setup().get_system_files()

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
        result = _sudo_run(["rm", "-f"] + root_exists)
        if result.returncode == 0:
            removed.extend(root_exists)
            _sudo_run(["udevadm", "control", "--reload-rules"])
            _sudo_run(["udevadm", "trigger"])
    else:
        for path_str in root_exists:
            os.remove(path_str)
            removed.append(path_str)

    # Disable autostart before shutting down logging
    from trcc.core.builder import ControllerBuilder
    autostart = ControllerBuilder.for_current_os().build_autostart()
    if autostart.is_enabled():
        autostart.disable()
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
    stale_bin = _real_user_home() / ".local" / "bin" / "trcc"
    if stale_bin.exists():
        stale_bin.unlink()
        print(f"Removed stale binary: {stale_bin}")

    return 0


def report(detect_fn=None):
    """Generate a full diagnostic report for bug reports."""
    log.debug("collecting diagnostic report")
    from trcc.adapters.infra.debug_report import DebugReport
    from trcc.adapters.infra.doctor import run_doctor

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
            from trcc.adapters.infra.theme_downloader import show_info as pack_info
            pack_info(pack)
            return 0

        if force:
            from trcc.conf import Settings
            Settings.clear_installed_resolutions()

        from trcc.core.app import TrccApp
        dispatch_pack = "" if show_list else (pack or "")
        return TrccApp.get().download_themes(dispatch_pack, force)

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


def run_setup(auto_yes: bool = False) -> int:
    """Interactive setup wizard — dispatches to platform-specific adapter."""
    from trcc.core.app import TrccApp
    return TrccApp.get().setup_platform(auto_yes=auto_yes)


def set_gpu() -> int:
    """Interactive GPU selector for multi-GPU systems."""
    if not LINUX:
        print("GPU selection is currently supported on Linux only.")
        return 1

    from trcc.adapters.system.linux.sensors import detect_gpus, SensorEnumerator
    from trcc.conf import Settings

    gpus = detect_gpus()
    if not gpus:
        print("No GPUs detected.")
        return 1

    if len(gpus) == 1:
        print(f"Only one GPU detected: {gpus[0]['name']}")
        print("No selection needed.")
        Settings.set_gpu(gpus[0]['pci_slot'])
        SensorEnumerator._default_map = None
        return 0

    # Multi-GPU: assign to slots
    print("Detected GPUs:\n")
    for i, gpu in enumerate(gpus, 1):
        print(f"  {i}) {gpu['name']}")

    current_slots = Settings._get_saved_gpu_slots()
    slot_names = ["top", "middle", "bottom"]
    slots: dict[str, str] = {}

    print("\nAssign GPUs to indicator slots (enter to skip):\n")
    for slot in slot_names:
        current_pci = current_slots.get(slot)
        current_label = ""
        if current_pci:
            for g in gpus:
                if g['pci_slot'] == current_pci:
                    current_label = f" ← current: {g['name']}"
                    break

        try:
            choice = input(f"  {slot.capitalize()} slot [1-{len(gpus)}]{current_label}: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 1

        if not choice:
            continue
        try:
            idx = int(choice) - 1
            if not (0 <= idx < len(gpus)):
                raise ValueError
        except ValueError:
            print(f"  Skipping {slot} (invalid input).")
            continue
        slots[slot] = gpus[idx]['pci_slot']

    if not slots:
        print("\nNo slots assigned.")
        return 1

    # Cycle frequency
    try:
        freq_input = input("\nCycle frequency in seconds (enter for 5s): ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return 1
    cycle_seconds = int(freq_input) if freq_input.isdigit() and int(freq_input) > 0 else 5

    # Indicator color
    named_colors = {
        'red': '#FF0000', 'green': '#00FF00', 'blue': '#0000FF',
        'yellow': '#FFFF00', 'cyan': '#00FFFF', 'magenta': '#FF00FF',
        'white': '#FFFFFF', 'orange': '#FFA500', 'purple': '#800080',
        'pink': '#FFC0CB', 'lime': '#00FF00', 'teal': '#008080',
        'aqua': '#00FFFF', 'coral': '#FF7F50', 'gold': '#FFD700',
        'violet': '#EE82EE', 'indigo': '#4B0082', 'crimson': '#DC143C',
        'turquoise': '#40E0D0', 'salmon': '#FA8072',
    }
    current_color = Settings._get_saved_gpu_indicator_color()
    try:
        color_input = input(
            f"\nIndicator color — name or hex (enter for {current_color}): "
        ).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return 1
    if color_input:
        lowered = color_input.lower()
        if lowered in named_colors:
            indicator_color = named_colors[lowered]
        else:
            raw = color_input.lstrip('#')
            if len(raw) == 6 and all(c in '0123456789abcdefABCDEF' for c in raw):
                indicator_color = f"#{raw.upper()}"
            else:
                print(f"  Unknown color '{color_input}', using {current_color}.")
                indicator_color = current_color
    else:
        indicator_color = current_color

    Settings.set_gpu_slots(slots, cycle_seconds)
    Settings.set_gpu_indicator_color(indicator_color)
    first_slot_pci = next(iter(slots.values()))
    Settings.set_gpu(first_slot_pci)
    SensorEnumerator._default_map = None

    print("\nGPU slots configured:")
    for slot, pci in slots.items():
        name = pci
        for g in gpus:
            if g['pci_slot'] == pci:
                name = g['name']
                break
        print(f"  {slot}: {name}")
    print(f"Cycle: every {cycle_seconds}s")
    print(f"Indicator color: {indicator_color}")
    print("Restart trcc for the change to take effect.")
    return 0
