#!/usr/bin/env python3
"""
TRCC Linux - Command Line Interface

Entry points for the trcc-linux package.
"""

import argparse
import os
import subprocess
import sys


def _sudo_reexec(subcommand):
    """Re-exec `trcc <subcommand>` as root via sudo with correct PYTHONPATH."""
    trcc_pkg = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cmd = [
        "sudo", "env", f"PYTHONPATH={trcc_pkg}",
        sys.executable, "-m", "trcc.cli", subcommand,
    ]
    print("Root required — requesting sudo...")
    result = subprocess.run(cmd)
    return result.returncode


def _sudo_run(cmd):
    """Run a command with sudo prepended. Returns subprocess.CompletedProcess."""
    return subprocess.run(["sudo"] + cmd)


def _ensure_extracted(driver):
    """Extract theme/mask archives for the driver's detected resolution."""
    try:
        if driver.implementation:
            w, h = driver.implementation.resolution
            from trcc.paths import (
                ensure_themes_extracted,
                ensure_web_extracted,
                ensure_web_masks_extracted,
            )
            ensure_themes_extracted(w, h)
            ensure_web_extracted(w, h)
            ensure_web_masks_extracted(w, h)
    except Exception:
        pass  # Non-fatal — themes are optional for CLI commands


def _get_driver(device=None):
    """Create an LCDDriver, resolving selected device and extracting archives."""
    from trcc.lcd_driver import LCDDriver
    if device is None:
        device = _get_selected_device()
    driver = LCDDriver(device_path=device)
    _ensure_extracted(driver)
    return driver


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="trcc",
        description="Thermalright LCD Control Center for Linux",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    trcc detect           Show active device
    trcc detect --all     List all devices
    trcc select 2         Switch to device 2
    trcc gui              Launch the GUI
    trcc test             Test display with color cycle
    trcc send image.png   Send image to LCD
    trcc color ff0000     Display solid red color
    trcc info             Show system metrics
        """
    )

    from trcc.__version__ import __version__
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}"
    )
    parser.add_argument(
        "-v", "--verbose",
        action="count",
        default=0,
        help="Increase verbosity (-v, -vv, -vvv)"
    )
    parser.add_argument(
        "--testing-hid",
        action="store_true",
        help="No-op (HID devices are now auto-detected when plugged in)"
    )
    parser.add_argument(
        "--last-one",
        action="store_true",
        help="Start minimized to system tray with last-used theme (autostart)"
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # GUI command
    gui_parser = subparsers.add_parser("gui", help="Launch graphical interface")
    gui_parser.add_argument(
        "--decorated", "-d",
        action="store_true",
        help="Use decorated window (normal window with titlebar, can minimize)"
    )

    # Detect command
    detect_parser = subparsers.add_parser("detect", help="Detect LCD device")
    detect_parser.add_argument("--all", "-a", action="store_true", help="Show all devices")

    # Select command
    select_parser = subparsers.add_parser("select", help="Select device to control")
    select_parser.add_argument("number", type=int, help="Device number from 'trcc detect --all'")

    # Test command
    test_parser = subparsers.add_parser("test", help="Test display with color cycle")
    test_parser.add_argument("--device", "-d", help="Device path (e.g., /dev/sg0)")
    test_parser.add_argument("--loop", "-l", action="store_true", help="Loop colors continuously")

    # Send command
    send_parser = subparsers.add_parser("send", help="Send image to LCD")
    send_parser.add_argument("image", help="Image file to send")
    send_parser.add_argument("--device", "-d", help="Device path")

    # Color command
    color_parser = subparsers.add_parser("color", help="Display solid color")
    color_parser.add_argument("hex", help="Hex color code (e.g., ff0000 for red)")
    color_parser.add_argument("--device", "-d", help="Device path")

    # Info command
    subparsers.add_parser("info", help="Show system metrics")

    # Reset command
    reset_parser = subparsers.add_parser("reset", help="Reset/reinitialize LCD device")
    reset_parser.add_argument("--device", "-d", help="Device path (e.g., /dev/sg0)")

    # Setup udev rules command
    udev_parser = subparsers.add_parser("setup-udev", help="Install udev rules for LCD device access")
    udev_parser.add_argument("--dry-run", action="store_true", help="Print rules without installing")

    # Install desktop entry command
    subparsers.add_parser("install-desktop", help="Install application menu entry and icon")

    # Resume command
    subparsers.add_parser("resume", help="Send last-used theme to each detected device (headless)")

    # Uninstall command
    subparsers.add_parser("uninstall", help="Remove all TRCC config, udev rules, and autostart files")

    # HID debug command
    subparsers.add_parser("hid-debug", help="HID handshake diagnostic (hex dump for bug reports)")

    # Download command (like spacy download)
    download_parser = subparsers.add_parser("download", help="Download theme packs")
    download_parser.add_argument("pack", nargs="?", help="Theme pack name (e.g., themes-320)")
    download_parser.add_argument("--list", "-l", action="store_true", help="List available packs")
    download_parser.add_argument("--force", "-f", action="store_true", help="Force reinstall")
    download_parser.add_argument("--info", "-i", action="store_true", help="Show pack info")

    args = parser.parse_args()

    if args.last_one:
        return gui(verbose=args.verbose, start_hidden=True)

    if args.command is None:
        parser.print_help()
        return 0

    if args.command == "gui":
        return gui(verbose=args.verbose, decorated=args.decorated)
    elif args.command == "detect":
        return detect(show_all=args.all)
    elif args.command == "select":
        return select_device(args.number)
    elif args.command == "test":
        return test_display(device=args.device, loop=args.loop)
    elif args.command == "send":
        return send_image(args.image, device=args.device)
    elif args.command == "color":
        return send_color(args.hex, device=args.device)
    elif args.command == "info":
        return show_info()
    elif args.command == "reset":
        return reset_device(device=args.device)
    elif args.command == "setup-udev":
        return setup_udev(dry_run=args.dry_run)
    elif args.command == "install-desktop":
        return install_desktop()
    elif args.command == "resume":
        return resume()
    elif args.command == "uninstall":
        return uninstall()
    elif args.command == "hid-debug":
        return hid_debug()
    elif args.command == "download":
        return download_themes(pack=args.pack, show_list=args.list,
                              force=args.force, show_info=args.info)

    return 0


def gui(verbose=0, decorated=False, start_hidden=False):
    """Launch the GUI application.

    Args:
        verbose: Logging verbosity (0=warning, 1=info, 2=debug).
        decorated: Use decorated window with titlebar.
        start_hidden: Start minimized to system tray (used by --last-one autostart).
    """
    import logging

    # Set up logging based on verbosity (filter out noisy PIL)
    if verbose >= 2:
        logging.basicConfig(level=logging.DEBUG, format='[%(levelname)s] %(name)s: %(message)s')
        logging.getLogger('PIL').setLevel(logging.WARNING)
    elif verbose == 1:
        logging.basicConfig(level=logging.INFO, format='[%(levelname)s] %(message)s')
    else:
        logging.basicConfig(level=logging.WARNING)

    try:
        from trcc.qt_components.qt_app_mvc import run_mvc_app
        print("[TRCC] Starting LCD Control Center...")
        return run_mvc_app(decorated=decorated, start_hidden=start_hidden)
    except ImportError as e:
        print(f"Error: PyQt6 not available: {e}")
        print("Install with: pip install PyQt6")
        return 1
    except Exception as e:
        print(f"Error launching GUI: {e}")
        import traceback
        traceback.print_exc()
        return 1


def _format_device(dev):
    """Format a detected device for display."""
    vid_pid = f"[{dev.vid:04x}:{dev.pid:04x}]"
    proto = dev.protocol.upper()
    path = dev.scsi_device if dev.scsi_device else "No device path found"
    return f"{path} — {dev.product_name} {vid_pid} ({proto})"


def detect(show_all=False):
    """Detect LCD device."""
    try:
        from trcc.device_detector import detect_devices

        devices = detect_devices()
        if not devices:
            print("No compatible TRCC LCD device detected.")
            return 1

        if show_all:
            selected = _get_selected_device()
            for i, dev in enumerate(devices, 1):
                marker = "*" if dev.scsi_device == selected else " "
                print(f"{marker} [{i}] {_format_device(dev)}")
            if len(devices) > 1:
                print("\nUse 'trcc select N' to switch devices")
        else:
            selected = _get_selected_device()
            dev = None
            if selected:
                dev = next((d for d in devices if d.scsi_device == selected), None)
            if not dev:
                dev = devices[0]
            print(f"Active: {_format_device(dev)}")

        return 0
    except Exception as e:
        print(f"Error: {e}")
        return 1


def _get_settings_path():
    """Get path to settings file."""
    return os.path.expanduser("~/.config/trcc/settings.json")


def _get_selected_device():
    """Get currently selected device from settings."""
    import json
    settings_path = _get_settings_path()
    if os.path.exists(settings_path):
        try:
            with open(settings_path) as f:
                settings = json.load(f)
                return settings.get("selected_device")
        except Exception:
            pass
    return None


def _set_selected_device(device_path):
    """Save selected device to settings."""
    import json
    settings_path = _get_settings_path()
    settings_dir = os.path.dirname(settings_path)
    os.makedirs(settings_dir, exist_ok=True)

    settings = {}
    if os.path.exists(settings_path):
        try:
            with open(settings_path) as f:
                settings = json.load(f)
        except Exception:
            pass

    settings["selected_device"] = device_path
    with open(settings_path, "w") as f:
        json.dump(settings, f, indent=2)


def select_device(number):
    """Select a device by number."""
    try:
        from trcc.device_detector import detect_devices

        devices = detect_devices()
        if not devices:
            print("No devices found.")
            return 1

        if number < 1 or number > len(devices):
            print(f"Invalid device number. Use 1-{len(devices)}")
            return 1

        device = devices[number - 1]
        _set_selected_device(device.scsi_device)
        print(f"Selected: {device.scsi_device} ({device.product_name})")
        return 0
    except Exception as e:
        print(f"Error: {e}")
        return 1


def test_display(device=None, loop=False):
    """Test display with color cycle."""
    try:
        import time

        driver = _get_driver(device)

        colors = [
            ((255, 0, 0), "Red"),
            ((0, 255, 0), "Green"),
            ((0, 0, 255), "Blue"),
            ((255, 255, 0), "Yellow"),
            ((255, 0, 255), "Magenta"),
            ((0, 255, 255), "Cyan"),
            ((255, 255, 255), "White"),
        ]

        print(f"Testing display on {driver.device_path}...")

        while True:
            for color, name in colors:
                print(f"  Displaying: {name}")
                frame = driver.create_solid_color(*color)
                driver.send_frame(frame)
                time.sleep(1)

            if not loop:
                break

        print("Test complete!")
        return 0
    except KeyboardInterrupt:
        print("\nTest interrupted.")
        return 0
    except Exception as e:
        print(f"Error testing display: {e}")
        return 1


def send_image(image_path, device=None):
    """Send image to LCD."""
    try:
        if not os.path.exists(image_path):
            print(f"Error: File not found: {image_path}")
            return 1

        driver = _get_driver(device)
        frame = driver.load_image(image_path)
        driver.send_frame(frame)
        print(f"Sent {image_path} to {driver.device_path}")
        return 0
    except Exception as e:
        print(f"Error sending image: {e}")
        return 1


def send_color(hex_color, device=None):
    """Send solid color to LCD."""
    try:
        # Parse hex color
        hex_color = hex_color.lstrip('#')
        if len(hex_color) != 6:
            print("Error: Invalid hex color. Use format: ff0000")
            return 1

        r = int(hex_color[0:2], 16)
        g = int(hex_color[2:4], 16)
        b = int(hex_color[4:6], 16)

        driver = _get_driver(device)
        frame = driver.create_solid_color(r, g, b)
        driver.send_frame(frame)
        print(f"Sent color #{hex_color} to {driver.device_path}")
        return 0
    except Exception as e:
        print(f"Error sending color: {e}")
        return 1


def resume():
    """Send last-used theme to each detected device (headless, no GUI)."""
    try:
        import time

        from trcc.device_detector import detect_devices
        from trcc.lcd_driver import LCDDriver
        from trcc.paths import device_config_key, get_device_config

        # Wait for USB devices to appear (they may not be ready at boot)
        devices = []
        for attempt in range(10):
            devices = detect_devices()
            if devices:
                break
            print(f"Waiting for device... ({attempt + 1}/10)")
            time.sleep(2)

        if not devices:
            print("No compatible TRCC device detected.")
            return 1

        sent = 0
        for i, dev in enumerate(devices):
            if dev.protocol != "scsi":
                continue

            key = device_config_key(i, dev.vid, dev.pid)
            cfg = get_device_config(key)
            theme_path = cfg.get("theme_path")

            if not theme_path:
                print(f"  [{dev.product_name}] No saved theme, skipping")
                continue

            # Find the image to send (00.png in theme dir, or direct file)
            image_path = None
            if os.path.isdir(theme_path):
                candidate = os.path.join(theme_path, "00.png")
                if os.path.exists(candidate):
                    image_path = candidate
            elif os.path.isfile(theme_path):
                image_path = theme_path

            if not image_path:
                print(f"  [{dev.product_name}] Theme not found: {theme_path}")
                continue

            try:
                from PIL import Image, ImageEnhance

                driver = LCDDriver(device_path=dev.scsi_device)
                w, h = driver.implementation.resolution

                img = Image.open(image_path).convert("RGB").resize((w, h))

                # Apply brightness
                brightness_level = cfg.get("brightness_level", 3)
                brightness_pct = {1: 25, 2: 50, 3: 100}.get(brightness_level, 100)
                if brightness_pct < 100:
                    img = ImageEnhance.Brightness(img).enhance(brightness_pct / 100.0)

                # Apply rotation + convert to RGB565
                from trcc.core.controllers import apply_rotation, image_to_rgb565
                rotation = cfg.get("rotation", 0)
                img = apply_rotation(img, rotation)
                frame = image_to_rgb565(img)

                driver.send_frame(frame)
                print(f"  [{dev.product_name}] Sent: {os.path.basename(theme_path)}")
                sent += 1
            except Exception as e:
                print(f"  [{dev.product_name}] Error: {e}")

        if sent == 0:
            print("No themes were sent. Use the GUI to set a theme first.")
            return 1

        print(f"Resumed {sent} device(s).")
        return 0

    except Exception as e:
        print(f"Error: {e}")
        return 1


def reset_device(device=None):
    """Reset/reinitialize the LCD device."""
    try:
        print("Resetting LCD device...")
        driver = _get_driver(device)
        print(f"  Device: {driver.device_path}")

        # Send test frame (red) - this will auto-init if needed
        frame = driver.create_solid_color(255, 0, 0)
        driver.send_frame(frame, force_init=True)
        print("[✓] Device reset - displaying RED")
        return 0
    except Exception as e:
        print(f"Error resetting device: {e}")
        return 1


def show_info():
    """Show system metrics."""
    try:
        from trcc.system_info import format_metric, get_all_metrics

        metrics = get_all_metrics()

        print("System Information")
        print("=" * 40)

        groups = [
            ("CPU", ['cpu_temp', 'cpu_percent', 'cpu_freq']),
            ("GPU", ['gpu_temp', 'gpu_usage', 'gpu_clock']),
            ("Memory", ['mem_percent', 'mem_used', 'mem_total']),
            ("Date/Time", ['date', 'time', 'weekday']),
        ]
        for label, keys in groups:
            print(f"\n{label}:")
            for key in keys:
                if key in metrics:
                    print(f"  {key}: {format_metric(key, metrics[key])}")

        return 0
    except Exception as e:
        print(f"Error getting metrics: {e}")
        return 1


def hid_debug():
    """HID handshake diagnostic — prints hex dump and resolved device info.

    Users can share this output in bug reports to help debug HID device issues.
    """
    try:
        from trcc.device_detector import detect_devices
        from trcc.hid_device import (
            FBL_TO_RESOLUTION, PM_TO_BUTTON_IMAGE,
            fbl_to_resolution, pm_to_fbl,
        )

        print("HID Debug — Handshake Diagnostic")
        print("=" * 60)

        devices = detect_devices()
        hid_devices = [d for d in devices if d.protocol == 'hid']

        if not hid_devices:
            print("\nNo HID devices found.")
            print("Make sure the device is plugged in and try:")
            print("  trcc setup-udev   (then unplug/replug USB cable)")
            return 0

        for dev in hid_devices:
            print(f"\nDevice: {dev.vendor_name} {dev.product_name}")
            print(f"  VID:PID = {dev.vid:04x}:{dev.pid:04x}")
            print(f"  Type = {dev.device_type}")
            print(f"  Implementation = {dev.implementation}")

            # Attempt handshake
            print(f"\n  Attempting handshake...")
            try:
                from trcc.device_factory import HidProtocol
                protocol = HidProtocol(
                    vid=dev.vid, pid=dev.pid,
                    device_type=dev.device_type,
                )
                info = protocol.handshake()
                if info is None:
                    print("  Handshake returned None (protocol error)")
                    protocol.close()
                    continue

                pm = info.mode_byte_1
                sub = info.mode_byte_2
                fbl = info.fbl if info.fbl is not None else pm_to_fbl(pm, sub)
                resolution = info.resolution or fbl_to_resolution(fbl, pm)

                print(f"  Handshake OK!")
                print(f"  PM byte  = {pm} (0x{pm:02x})")
                print(f"  SUB byte = {sub} (0x{sub:02x})")
                print(f"  FBL      = {fbl} (0x{fbl:02x})")
                print(f"  Serial   = {info.serial}")
                print(f"  Resolution = {resolution[0]}x{resolution[1]}")

                # Button image from PM
                button = PM_TO_BUTTON_IMAGE.get(pm)
                if button:
                    print(f"  Button image = {button}")
                else:
                    print(f"  Button image = unknown PM={pm} (defaulting to CZTV)")

                # Known FBL?
                if fbl in FBL_TO_RESOLUTION:
                    print(f"  FBL {fbl} = known resolution")
                else:
                    print(f"  FBL {fbl} = UNKNOWN (not in mapping table)")

                # Raw response hex dump
                if info.raw_response:
                    print(f"\n  Raw handshake response (first 64 bytes):")
                    raw = info.raw_response
                    for row in range(0, min(len(raw), 64), 16):
                        hex_str = ' '.join(f'{b:02x}' for b in raw[row:row+16])
                        ascii_str = ''.join(
                            chr(b) if 32 <= b < 127 else '.'
                            for b in raw[row:row+16]
                        )
                        print(f"  {row:04x}: {hex_str:<48s} {ascii_str}")

                protocol.close()

            except ImportError as e:
                print(f"  Missing dependency: {e}")
                print("  Install: pip install pyusb  (or pip install hidapi)")
            except Exception as e:
                print(f"  Handshake FAILED: {e}")
                print(f"  (This error is what causes 'Send failed' in the GUI)")

        print(f"\n{'=' * 60}")
        print("Copy the output above and paste it in your GitHub issue.")
        return 0

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        return 1


def setup_udev(dry_run=False):
    """Generate and install udev rules + USB storage quirks from KNOWN_DEVICES.

    Without quirks, UAS claims these LCD devices and the kernel ignores them
    (no /dev/sgX created). The :u quirk forces usb-storage bulk-only transport.
    """
    try:
        from trcc.device_detector import _HID_LCD_DEVICES, _LED_DEVICES, KNOWN_DEVICES

        # Always include ALL devices in udev rules (so hardware is ready
        # when users plug in HID devices, even without --testing-hid)
        all_devices = {**KNOWN_DEVICES, **_HID_LCD_DEVICES, **_LED_DEVICES}

        # --- 1. udev rules (permissions) ---
        rules_path = "/etc/udev/rules.d/99-trcc-lcd.rules"
        rules_lines = ["# Thermalright LCD/LED cooler devices — auto-generated by trcc setup-udev"]

        for (vid, pid), info in sorted(all_devices.items()):
            vendor = info.get("vendor", "Unknown")
            product = info.get("product", "Unknown")
            protocol = info.get("protocol", "scsi")
            if protocol == "hid":
                # HID devices need hidraw/usb subsystem rules
                rules_lines.append(
                    f'# {vendor} {product}\n'
                    f'SUBSYSTEM=="hidraw", '
                    f'ATTRS{{idVendor}}=="{vid:04x}", '
                    f'ATTRS{{idProduct}}=="{pid:04x}", '
                    f'MODE="0660", TAG+="uaccess"\n'
                    f'SUBSYSTEM=="usb", '
                    f'ATTR{{idVendor}}=="{vid:04x}", '
                    f'ATTR{{idProduct}}=="{pid:04x}", '
                    f'MODE="0660", TAG+="uaccess"'
                )
            else:
                rules_lines.append(
                    f'# {vendor} {product}\n'
                    f'SUBSYSTEM=="scsi_generic", '
                    f'ATTRS{{idVendor}}=="{vid:04x}", '
                    f'ATTRS{{idProduct}}=="{pid:04x}", '
                    f'MODE="0660", TAG+="uaccess"'
                )

        rules_content = "\n\n".join(rules_lines) + "\n"

        # --- 2. usb-storage quirks (UAS bypass) ---
        # Format: vid:pid:u (u = US_FL_IGNORE_UAS, forces bulk-only transport)
        quirk_entries = [f"{vid:04x}:{pid:04x}:u" for vid, pid in sorted(KNOWN_DEVICES)]
        quirks_param = ",".join(quirk_entries)

        # modprobe config (persistent across reboots)
        modprobe_path = "/etc/modprobe.d/trcc-lcd.conf"
        modprobe_content = (
            "# Thermalright LCD — force usb-storage bulk-only (bypass UAS)\n"
            "# Without this, devices are ignored and /dev/sgX is never created\n"
            "# Auto-generated by trcc setup-udev\n"
            f"options usb-storage quirks={quirks_param}\n"
        )

        if dry_run:
            print("=== udev rules ===")
            print(rules_content)
            print(f"# Would write to {rules_path}\n")
            print("=== usb-storage quirks ===")
            print(modprobe_content)
            print(f"# Would write to {modprobe_path}")
            return 0

        # Need root — re-exec with sudo automatically
        if os.geteuid() != 0:
            return _sudo_reexec("setup-udev")

        # Write udev rules
        with open(rules_path, "w") as f:
            f.write(rules_content)
        print(f"Wrote {rules_path}")

        # Write modprobe config
        with open(modprobe_path, "w") as f:
            f.write(modprobe_content)
        print(f"Wrote {modprobe_path}")

        # Apply quirks immediately (without reboot)
        quirks_sysfs = "/sys/module/usb_storage/parameters/quirks"
        if os.path.exists(quirks_sysfs):
            with open(quirks_sysfs, "w") as f:
                f.write(quirks_param)
            print(f"Applied quirks: {quirks_param}")

        # Reload udev
        subprocess.run(["udevadm", "control", "--reload-rules"], check=False)
        subprocess.run(["udevadm", "trigger"], check=False)
        print("\nDone. Unplug and replug the USB cable (or reboot if it's not easily accessible).")
        return 0

    except Exception as e:
        print(f"Error: {e}")
        return 1


def install_desktop():
    """Install .desktop menu entry and icon for app launchers."""
    import shutil
    from pathlib import Path

    home = Path.home()
    app_dir = home / ".local" / "share" / "applications"
    icon_dir = home / ".local" / "share" / "icons" / "hicolor" / "256x256" / "apps"

    # Find repo root (where trcc.desktop lives)
    repo_root = Path(__file__).parent.parent.parent
    desktop_src = repo_root / "trcc.desktop"
    icon_src = repo_root / "src" / "assets" / "icons" / "trcc_256x256.png"

    if not desktop_src.exists():
        print(f"Error: {desktop_src} not found. Run from the repo directory.")
        return 1

    # Install .desktop file
    app_dir.mkdir(parents=True, exist_ok=True)
    desktop_dst = app_dir / "trcc.desktop"
    shutil.copy2(desktop_src, desktop_dst)
    print(f"Installed {desktop_dst}")

    # Install icon
    if icon_src.exists():
        icon_dir.mkdir(parents=True, exist_ok=True)
        icon_dst = icon_dir / "trcc.png"
        shutil.copy2(icon_src, icon_dst)
        print(f"Installed {icon_dst}")

        # Also install smaller sizes
        for size in [48, 64, 128]:
            small_src = icon_src.parent / f"trcc_{size}x{size}.png"
            if small_src.exists():
                small_dir = home / ".local" / "share" / "icons" / "hicolor" / f"{size}x{size}" / "apps"
                small_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(small_src, small_dir / "trcc.png")
    else:
        print("Warning: icon not found, menu entry will use a generic icon")

    # Update icon cache
    subprocess.run(
        ["gtk-update-icon-cache", str(home / ".local" / "share" / "icons" / "hicolor")],
        check=False, capture_output=True
    )

    print("\nTRCC should now appear in your application menu.")
    print("If it doesn't show up immediately, log out and back in.")
    return 0


def uninstall():
    """Remove all TRCC config, udev rules, autostart, and desktop files."""
    import shutil
    from pathlib import Path

    home = Path.home()

    # Files that require root to remove
    root_files = [
        "/etc/udev/rules.d/99-trcc-lcd.rules",
        "/etc/modprobe.d/trcc-lcd.conf",
    ]

    # User files/dirs to remove
    user_items = [
        home / ".config" / "trcc",                          # config dir
        home / ".trcc",                                      # legacy config dir
        home / ".config" / "autostart" / "trcc.desktop",     # autostart
        home / ".local" / "share" / "applications" / "trcc.desktop",  # desktop shortcut
    ]

    removed = []

    # Handle root files — auto-elevate with sudo if needed
    root_exists = [p for p in root_files if os.path.exists(p)]
    if root_exists and os.geteuid() != 0:
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

    # Reload udev if we removed rules (and we're root — non-root already did it above)
    if os.geteuid() == 0 and any("udev" in r for r in removed):
        subprocess.run(["udevadm", "control", "--reload-rules"], check=False)
        subprocess.run(["udevadm", "trigger"], check=False)

    return 0


def download_themes(pack=None, show_list=False, force=False, show_info=False):
    """Download theme packs (like spacy download)."""
    try:
        from trcc.theme_downloader import download_pack, list_available
        from trcc.theme_downloader import show_info as pack_info

        if show_list or pack is None:
            list_available()
            return 0

        if show_info:
            pack_info(pack)
            return 0

        return download_pack(pack, force=force)

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
