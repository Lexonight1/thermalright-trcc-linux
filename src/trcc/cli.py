#!/usr/bin/env python3
"""
TRCC Linux — Command Line Interface.

Entry points for the trcc-linux package.
Organized into four command classes:
  DeviceCommands  — detection, selection, probing
  DisplayCommands — LCD frame operations (test, send, color, resume)
  DiagCommands    — HID/LED diagnostics
  SystemCommands  — setup, install, admin, info, download
"""

import argparse
import os
import subprocess
import sys

# =========================================================================
# Sudo helpers (module-level — used by SystemCommands)
# =========================================================================

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


# =========================================================================
# Main entry point + GUI launcher
# =========================================================================

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
    trcc report           Generate diagnostic report for bug reports
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

    # LED diagnostic command
    led_diag_parser = subparsers.add_parser("led-diag", help="Diagnose LED device (handshake, PM byte)")
    led_diag_parser.add_argument("--test", action="store_true", help="Send test colors after handshake")

    # HR10 temperature daemon
    tempd_parser = subparsers.add_parser(
        "hr10-tempd", help="Display NVMe temperature on HR10 (daemon)")
    tempd_parser.add_argument(
        "--brightness", type=int, default=100,
        help="LED brightness 0-100 (default: 100)")
    tempd_parser.add_argument(
        "--drive", default="9100",
        help="NVMe model substring to match (default: 9100)")
    tempd_parser.add_argument(
        "--unit", choices=["C", "F"], default="C",
        help="Temperature unit: C or F (default: C)")

    # Report command (one-liner diagnostic for bug reports)
    subparsers.add_parser("report", help="Generate full diagnostic report for bug reports")

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

    # Dispatch to class methods
    if args.command == "gui":
        return gui(verbose=args.verbose, decorated=args.decorated)
    elif args.command == "detect":
        return DeviceCommands.detect(show_all=args.all)
    elif args.command == "select":
        return DeviceCommands.select(args.number)
    elif args.command == "test":
        return DisplayCommands.test(device=args.device, loop=args.loop)
    elif args.command == "send":
        return DisplayCommands.send_image(args.image, device=args.device)
    elif args.command == "color":
        return DisplayCommands.send_color(args.hex, device=args.device)
    elif args.command == "info":
        return SystemCommands.show_info()
    elif args.command == "reset":
        return DisplayCommands.reset(device=args.device)
    elif args.command == "setup-udev":
        return SystemCommands.setup_udev(dry_run=args.dry_run)
    elif args.command == "install-desktop":
        return SystemCommands.install_desktop()
    elif args.command == "resume":
        return DisplayCommands.resume()
    elif args.command == "uninstall":
        return SystemCommands.uninstall()
    elif args.command == "hid-debug":
        return DiagCommands.hid_debug()
    elif args.command == "led-diag":
        return DiagCommands.led_diag(test=args.test)
    elif args.command == "hr10-tempd":
        return DiagCommands.hr10_tempd(
            brightness=args.brightness,
            drive=args.drive, unit=args.unit,
            verbose=args.verbose)
    elif args.command == "report":
        return SystemCommands.report()
    elif args.command == "download":
        return SystemCommands.download_themes(
            pack=args.pack, show_list=args.list,
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


# =========================================================================
# DeviceCommands — detection, selection, probing
# =========================================================================

class DeviceCommands:
    """Device detection, selection, and probing commands."""

    @staticmethod
    def _ensure_extracted(driver):
        """Extract theme/mask archives for the driver's detected resolution (one-time)."""
        try:
            if driver.implementation:
                w, h = driver.implementation.resolution
                from trcc.paths import ensure_all_data
                ensure_all_data(w, h)
        except Exception:
            pass  # Non-fatal — themes are optional for CLI commands

    @staticmethod
    def _get_driver(device=None):
        """Create an LCDDriver, resolving selected device and extracting archives."""
        from trcc.conf import get_selected_device
        from trcc.driver_lcd import LCDDriver
        if device is None:
            device = get_selected_device()
        driver = LCDDriver(device_path=device)
        DeviceCommands._ensure_extracted(driver)
        return driver

    @staticmethod
    def _probe(dev):
        """Try to resolve device details via HID handshake/cache.

        Returns a dict with resolved fields, or empty dict if no probe available.
        """
        result = {}

        # LED devices: probe via led_device cache/handshake
        if dev.implementation == 'hid_led':
            try:
                from trcc.device_led import probe_led_model
                info = probe_led_model(dev.vid, dev.pid, usb_path=dev.usb_path)
                if info and info.model_name:
                    result['model'] = info.model_name
                    result['pm'] = info.pm
                    result['style'] = info.style
            except Exception:
                pass

        # HID LCD devices: probe via hid_device handshake
        elif dev.implementation in ('hid_type2', 'hid_type3'):
            try:
                from trcc.device_factory import DeviceProtocolFactory
                from trcc.device_hid import HidHandshakeInfo
                device_info = {
                    'vid': dev.vid, 'pid': dev.pid,
                    'protocol': dev.protocol, 'device_type': dev.device_type,
                    'implementation': dev.implementation,
                    'path': f"hid:{dev.vid:04x}:{dev.pid:04x}",
                }
                protocol = DeviceProtocolFactory.get_protocol(device_info)
                raw_info = protocol.handshake()
                if isinstance(raw_info, HidHandshakeInfo):
                    result['pm'] = raw_info.mode_byte_1
                    result['resolution'] = raw_info.resolution
                    if raw_info.serial:
                        result['serial'] = raw_info.serial
            except Exception:
                pass

        # Bulk USB devices: probe via bulk_device handshake
        elif dev.implementation == 'bulk_usblcdnew':
            try:
                from trcc.device_bulk import BulkDevice
                bd = BulkDevice(dev.vid, dev.pid)
                hs = bd.handshake()
                if hs.resolution:
                    result['resolution'] = hs.resolution
                    result['pm'] = hs.model_id
                bd.close()
            except Exception:
                pass

        return result

    @staticmethod
    def _format(dev, probe=False):
        """Format a detected device for display."""
        vid_pid = f"[{dev.vid:04x}:{dev.pid:04x}]"
        proto = dev.protocol.upper()
        if dev.scsi_device:
            path = dev.scsi_device
        elif dev.protocol in ("hid", "bulk"):
            path = f"{dev.vid:04x}:{dev.pid:04x}"
        else:
            path = "No device path found"
        line = f"{path} — {dev.product_name} {vid_pid} ({proto})"

        if not probe:
            return line

        info = DeviceCommands._probe(dev)
        if not info:
            return line

        details = []
        if 'model' in info:
            details.append(f"model: {info['model']}")
        if 'resolution' in info:
            w, h = info['resolution']
            details.append(f"resolution: {w}x{h}")
        if 'pm' in info:
            details.append(f"PM={info['pm']}")
        if 'serial' in info:
            details.append(f"serial: {info['serial'][:16]}")

        if details:
            line += f" ({', '.join(details)})"
        return line

    @staticmethod
    def detect(show_all=False):
        """Detect LCD device."""
        try:
            from trcc.conf import get_selected_device
            from trcc.device_detector import check_udev_rules, detect_devices

            devices = detect_devices()
            if not devices:
                print("No compatible TRCC LCD device detected.")
                return 1

            if show_all:
                selected = get_selected_device()
                for i, dev in enumerate(devices, 1):
                    marker = "*" if dev.scsi_device == selected else " "
                    print(f"{marker} [{i}] {DeviceCommands._format(dev, probe=True)}")
                if len(devices) > 1:
                    print("\nUse 'trcc select N' to switch devices")
            else:
                selected = get_selected_device()
                dev = None
                if selected:
                    dev = next((d for d in devices if d.scsi_device == selected), None)
                if not dev:
                    dev = devices[0]
                print(f"Active: {DeviceCommands._format(dev, probe=True)}")

            # Check for stale/missing udev rules on any device
            for dev in devices:
                if not check_udev_rules(dev):
                    msg = f"\nDevice {dev.vid:04x}:{dev.pid:04x} needs updated udev rules.\n"
                    msg += "Run:  sudo trcc setup-udev"
                    if dev.protocol == "scsi":
                        msg += "\nThen reboot for the USB storage quirk to take effect."
                    print(msg)
                    break

            return 0
        except Exception as e:
            print(f"Error: {e}")
            return 1

    @staticmethod
    def select(number):
        """Select a device by number."""
        try:
            from trcc.conf import save_selected_device
            from trcc.device_detector import detect_devices

            devices = detect_devices()
            if not devices:
                print("No devices found.")
                return 1

            if number < 1 or number > len(devices):
                print(f"Invalid device number. Use 1-{len(devices)}")
                return 1

            device = devices[number - 1]
            save_selected_device(device.scsi_device)
            print(f"Selected: {device.scsi_device} ({device.product_name})")
            return 0
        except Exception as e:
            print(f"Error: {e}")
            return 1


# =========================================================================
# DisplayCommands — LCD frame operations
# =========================================================================

class DisplayCommands:
    """LCD display frame sending commands."""

    @staticmethod
    def test(device=None, loop=False):
        """Test display with color cycle."""
        try:
            import time

            driver = DeviceCommands._get_driver(device)

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

    @staticmethod
    def send_image(image_path, device=None):
        """Send image to LCD."""
        try:
            if not os.path.exists(image_path):
                print(f"Error: File not found: {image_path}")
                return 1

            driver = DeviceCommands._get_driver(device)
            frame = driver.load_image(image_path)
            driver.send_frame(frame)
            print(f"Sent {image_path} to {driver.device_path}")
            return 0
        except Exception as e:
            print(f"Error sending image: {e}")
            return 1

    @staticmethod
    def send_color(hex_color, device=None):
        """Send solid color to LCD."""
        try:
            hex_color = hex_color.lstrip('#')
            if len(hex_color) != 6:
                print("Error: Invalid hex color. Use format: ff0000")
                return 1

            r = int(hex_color[0:2], 16)
            g = int(hex_color[2:4], 16)
            b = int(hex_color[4:6], 16)

            driver = DeviceCommands._get_driver(device)
            frame = driver.create_solid_color(r, g, b)
            driver.send_frame(frame)
            print(f"Sent color #{hex_color} to {driver.device_path}")
            return 0
        except Exception as e:
            print(f"Error sending color: {e}")
            return 1

    @staticmethod
    def reset(device=None):
        """Reset/reinitialize the LCD device."""
        try:
            print("Resetting LCD device...")
            driver = DeviceCommands._get_driver(device)
            print(f"  Device: {driver.device_path}")

            # Send test frame (red) - this will auto-init if needed
            frame = driver.create_solid_color(255, 0, 0)
            driver.send_frame(frame, force_init=True)
            print("[✓] Device reset - displaying RED")
            return 0
        except Exception as e:
            print(f"Error resetting device: {e}")
            return 1

    @staticmethod
    def resume():
        """Send last-used theme to each detected device (headless, no GUI)."""
        try:
            import time

            from trcc.conf import device_config_key, get_device_config
            from trcc.device_detector import detect_devices
            from trcc.driver_lcd import LCDDriver

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
                    if not driver.implementation:
                        continue
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


# =========================================================================
# DiagCommands — HID/LED diagnostics
# =========================================================================

class DiagCommands:
    """HID/LED diagnostic commands."""

    @staticmethod
    def _hex_dump(data: bytes, max_bytes: int = 64) -> None:
        """Print a hex dump of data (for hid-debug diagnostics)."""
        for row in range(0, min(len(data), max_bytes), 16):
            hex_str = ' '.join(f'{b:02x}' for b in data[row:row + 16])
            ascii_str = ''.join(
                chr(b) if 32 <= b < 127 else '.'
                for b in data[row:row + 16]
            )
            print(f"  {row:04x}: {hex_str:<48s} {ascii_str}")

    @staticmethod
    def _hid_debug_lcd(dev) -> None:
        """HID handshake diagnostic for LCD devices (Type 2/3)."""
        from trcc.device_factory import HidProtocol
        from trcc.device_hid import (
            FBL_TO_RESOLUTION,
            HidHandshakeInfo,
            fbl_to_resolution,
            get_button_image,
            pm_to_fbl,
        )

        protocol = HidProtocol(
            vid=dev.vid, pid=dev.pid,
            device_type=dev.device_type,
        )
        info = protocol.handshake()

        if info is None:
            error = protocol.last_error
            if error:
                print(f"  Handshake FAILED: {error}")
            else:
                print("  Handshake returned None (no response from device)")
            protocol.close()
            return

        assert isinstance(info, HidHandshakeInfo)
        pm = info.mode_byte_1
        sub = info.mode_byte_2
        fbl = info.fbl if info.fbl is not None else pm_to_fbl(pm, sub)
        resolution = info.resolution or fbl_to_resolution(fbl, pm)

        print("  Handshake OK!")
        print(f"  PM byte  = {pm} (0x{pm:02x})")
        print(f"  SUB byte = {sub} (0x{sub:02x})")
        print(f"  FBL      = {fbl} (0x{fbl:02x})")
        print(f"  Serial   = {info.serial}")
        print(f"  Resolution = {resolution[0]}x{resolution[1]}")

        # Button image from PM + SUB
        button = get_button_image(pm, sub)
        if button:
            print(f"  Button image = {button}")
        else:
            print(f"  Button image = unknown PM={pm} SUB={sub} (defaulting to CZTV)")

        # Known FBL?
        if fbl in FBL_TO_RESOLUTION:
            print(f"  FBL {fbl} = known resolution")
        else:
            print(f"  FBL {fbl} = UNKNOWN (not in mapping table)")

        # Raw response hex dump
        if info.raw_response:
            print("\n  Raw handshake response (first 64 bytes):")
            DiagCommands._hex_dump(info.raw_response)

        protocol.close()

    @staticmethod
    def _hid_debug_led(dev) -> None:
        """HID handshake diagnostic for LED devices (Type 1)."""
        from trcc.device_factory import LedProtocol
        from trcc.device_led import PM_TO_STYLE, LedHandshakeInfo

        protocol = LedProtocol(vid=dev.vid, pid=dev.pid)
        info = protocol.handshake()

        if info is None:
            error = protocol.last_error
            if error:
                print(f"  Handshake FAILED: {error}")
            else:
                print("  Handshake returned None (no response from device)")
            protocol.close()
            return

        assert isinstance(info, LedHandshakeInfo)
        print("  Handshake OK!")
        print(f"  PM byte    = {info.pm} (0x{info.pm:02x})")
        print(f"  Sub-type   = {info.sub_type} (0x{info.sub_type:02x})")
        print(f"  Model      = {info.model_name}")

        style = info.style
        if style:
            print(f"  Style ID   = {style.style_id}")
            print(f"  LED count  = {style.led_count}")
            print(f"  Segments   = {style.segment_count}")
            print(f"  Zones      = {style.zone_count}")

        if info.pm in PM_TO_STYLE:
            print(f"\n  Status: KNOWN device (PM {info.pm} in tables)")
        else:
            print(f"\n  Status: UNKNOWN PM byte ({info.pm})")
            print("  This device falls back to AX120 defaults.")
            print(f"  Please report PM {info.pm} in your GitHub issue.")

        # Raw response hex dump
        if info.raw_response:
            print("\n  Raw handshake response (first 64 bytes):")
            DiagCommands._hex_dump(info.raw_response)

        protocol.close()

    @staticmethod
    def hid_debug():
        """HID handshake diagnostic — prints hex dump and resolved device info.

        Users can share this output in bug reports to help debug HID device issues.
        Routes LED devices (Type 1) through LedProtocol, LCD devices through HidProtocol.
        """
        try:
            from trcc.device_detector import detect_devices

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
                is_led = dev.implementation == 'hid_led'
                dev_kind = "LED" if is_led else f"LCD (Type {dev.device_type})"

                print(f"\nDevice: {dev.vendor_name} {dev.product_name}")
                print(f"  VID:PID = {dev.vid:04x}:{dev.pid:04x}")
                print(f"  Kind = {dev_kind}")
                print(f"  Implementation = {dev.implementation}")

                print("\n  Attempting handshake...")
                try:
                    if is_led:
                        DiagCommands._hid_debug_led(dev)
                    else:
                        DiagCommands._hid_debug_lcd(dev)
                except ImportError as e:
                    print(f"  Missing dependency: {e}")
                    print("  Install: pip install pyusb  (or pip install hidapi)")
                except Exception as e:
                    print(f"  Handshake FAILED: {e}")
                    import traceback
                    traceback.print_exc()

            print(f"\n{'=' * 60}")
            print("Copy the output above and paste it in your GitHub issue.")
            return 0

        except Exception as e:
            print(f"Error: {e}")
            import traceback
            traceback.print_exc()
            return 1

    @staticmethod
    def led_diag(test=False):
        """Diagnose LED device — handshake, PM byte discovery, optional test colors."""
        try:
            import time

            from trcc.device_factory import create_usb_transport
            from trcc.device_led import (
                LED_PID,
                LED_VID,
                PM_TO_STYLE,
                LedHidSender,
                LedPacketBuilder,
            )

            print("LED Device Diagnostic")
            print("=" * 50)
            print(f"  Target: VID=0x{LED_VID:04x} PID=0x{LED_PID:04x}")

            try:
                transport = create_usb_transport(LED_VID, LED_PID)
            except ImportError as e:
                print(f"Error: {e}")
                return 1

            transport.open()
            print("  Transport: opened")

            sender = LedHidSender(transport)
            try:
                info = sender.handshake()
            except RuntimeError as e:
                print(f"\nHandshake failed: {e}")
                transport.close()
                return 1

            print(f"\n  PM byte:    {info.pm}")
            print(f"  Sub-type:   {info.sub_type}")
            print(f"  Model:      {info.model_name}")
            style = info.style
            if style is None:
                print("  Style:      (unknown — handshake returned no style)")
                transport.close()
                return 1
            print(f"  Style ID:   {style.style_id}")
            print(f"  LED count:  {style.led_count}")
            print(f"  Segments:   {style.segment_count}")
            print(f"  Zones:      {style.zone_count}")

            if info.pm in PM_TO_STYLE:
                print(f"\n  Status: KNOWN device (PM {info.pm} in tables)")
            else:
                print(f"\n  Status: UNKNOWN PM byte ({info.pm})")
                print("  This device falls back to AX120 defaults.")
                print(f"  Add PM {info.pm} to led_device.py _PM_REGISTRY.")

            if test:
                from .device_led import remap_led_colors

                print("\n  Sending test colors...")
                led_count = style.led_count
                for name, color in [("RED", (255, 0, 0)), ("GREEN", (0, 255, 0)),
                                    ("BLUE", (0, 0, 255)), ("WHITE", (255, 255, 255))]:
                    led_colors = remap_led_colors(
                        [color] * led_count, style.style_id)
                    packet = LedPacketBuilder.build_led_packet(led_colors, brightness=100)
                    sender.send_led_data(packet)
                    print(f"    {name}")
                    time.sleep(1.5)
                led_colors = remap_led_colors(
                    [(0, 0, 0)] * led_count, style.style_id)
                packet = LedPacketBuilder.build_led_packet(led_colors, brightness=0)
                sender.send_led_data(packet)
                print("    OFF")

            transport.close()
            print("\nDone.")
            return 0

        except Exception as e:
            print(f"Error: {e}")
            import traceback
            traceback.print_exc()
            return 1

    @staticmethod
    def hr10_tempd(brightness=100, drive="9100", unit="C", verbose=0):
        """Run the HR10 NVMe temperature display daemon."""
        try:
            from trcc.device_led_hr10 import run_hr10_daemon
            return run_hr10_daemon(
                brightness=brightness,
                model_substr=drive,
                unit=unit,
                verbose=verbose > 0,
            )
        except Exception as e:
            print(f"Error: {e}")
            import traceback
            traceback.print_exc()
            return 1


# =========================================================================
# SystemCommands — setup, install, admin
# =========================================================================

class SystemCommands:
    """System setup and administration commands."""

    @staticmethod
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

    @staticmethod
    def setup_udev(dry_run=False):
        """Generate and install udev rules + USB storage quirks from KNOWN_DEVICES.

        Without quirks, UAS claims these LCD devices and the kernel ignores them
        (no /dev/sgX created). The :u quirk forces usb-storage bulk-only transport.
        """
        try:
            from trcc.device_detector import (
                _BULK_DEVICES,
                _HID_LCD_DEVICES,
                _LED_DEVICES,
                KNOWN_DEVICES,
            )

            # Always include ALL devices in udev rules (so hardware is ready
            # when users plug in HID/bulk devices, even without --testing-hid)
            all_devices = {**KNOWN_DEVICES, **_HID_LCD_DEVICES, **_LED_DEVICES, **_BULK_DEVICES}

            # --- 1. udev rules (permissions) ---
            rules_path = "/etc/udev/rules.d/99-trcc-lcd.rules"
            rules_lines = ["# Thermalright LCD/LED cooler devices — auto-generated by trcc setup-udev"]

            for (vid, pid), info in sorted(all_devices.items()):
                vendor = info.vendor
                product = info.product
                protocol = info.protocol
                if protocol == "hid":
                    rules_lines.append(
                        f'# {vendor} {product}\n'
                        f'SUBSYSTEM=="hidraw", '
                        f'ATTRS{{idVendor}}=="{vid:04x}", '
                        f'ATTRS{{idProduct}}=="{pid:04x}", '
                        f'MODE="0666"\n'
                        f'SUBSYSTEM=="usb", '
                        f'ATTR{{idVendor}}=="{vid:04x}", '
                        f'ATTR{{idProduct}}=="{pid:04x}", '
                        f'MODE="0666"'
                    )
                elif protocol == "bulk":
                    rules_lines.append(
                        f'# {vendor} {product}\n'
                        f'SUBSYSTEM=="usb", '
                        f'ATTR{{idVendor}}=="{vid:04x}", '
                        f'ATTR{{idProduct}}=="{pid:04x}", '
                        f'MODE="0666"'
                    )
                else:
                    rules_lines.append(
                        f'# {vendor} {product}\n'
                        f'SUBSYSTEM=="scsi_generic", '
                        f'ATTRS{{idVendor}}=="{vid:04x}", '
                        f'ATTRS{{idProduct}}=="{pid:04x}", '
                        f'MODE="0666"'
                    )

            rules_content = "\n\n".join(rules_lines) + "\n"

            # --- 2. usb-storage quirks (UAS bypass) ---
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

    @staticmethod
    def install_desktop():
        """Install .desktop menu entry and icon for app launchers.

        Works from both pip install and git clone — generates .desktop inline
        and resolves icons from the package tree (src/trcc/assets/icons/).
        """
        import shutil
        from pathlib import Path

        home = Path.home()
        app_dir = home / ".local" / "share" / "applications"

        # Icons live inside the package (works for both pip install and git clone)
        icon_pkg_dir = Path(__file__).parent / "assets" / "icons"

        # Generate .desktop content inline (no dependency on repo root)
        desktop_content = """\
[Desktop Entry]
Name=TRCC Linux
Comment=Thermalright LCD Control Center
Exec=trcc gui
Icon=trcc
Terminal=false
Type=Application
Categories=Utility;System;
Keywords=thermalright;lcd;cooler;aio;cpu;
"""

        # Install .desktop file
        app_dir.mkdir(parents=True, exist_ok=True)
        desktop_dst = app_dir / "trcc.desktop"
        desktop_dst.write_text(desktop_content)
        print(f"Installed {desktop_dst}")

        # Install icons to XDG hicolor theme
        installed_icon = False
        for size in [256, 128, 64, 48]:
            icon_src = icon_pkg_dir / f"trcc_{size}x{size}.png"
            if icon_src.exists():
                icon_dir = home / ".local" / "share" / "icons" / "hicolor" / f"{size}x{size}" / "apps"
                icon_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(icon_src, icon_dir / "trcc.png")
                installed_icon = True

        if installed_icon:
            # Update icon cache
            subprocess.run(
                ["gtk-update-icon-cache", str(home / ".local" / "share" / "icons" / "hicolor")],
                check=False, capture_output=True
            )
        else:
            print("Warning: icons not found, menu entry will use a generic icon")

        print("\nTRCC should now appear in your application menu.")
        print("If it doesn't show up immediately, log out and back in.")
        return 0

    @staticmethod
    def uninstall():
        """Remove all TRCC config, udev rules, autostart, and desktop files."""
        import shutil
        from pathlib import Path

        from trcc.conf import clear_installed_resolutions

        # Clear resolution markers before wiping config dir
        clear_installed_resolutions()

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

    @staticmethod
    def report():
        """Generate a full diagnostic report for bug reports."""
        from trcc.debug_report import DebugReport

        rpt = DebugReport()
        rpt.collect()
        print(rpt)
        return 0

    @staticmethod
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

            if force:
                from trcc.conf import clear_installed_resolutions
                clear_installed_resolutions()

            return download_pack(pack, force=force)

        except Exception as e:
            print(f"Error: {e}")
            import traceback
            traceback.print_exc()
            return 1


# =========================================================================
# Backward-compat aliases (pyproject.toml entry points + tests)
# =========================================================================

# Entry points (pyproject.toml console_scripts)
detect = DeviceCommands.detect
test_display = DisplayCommands.test
select_device = DeviceCommands.select

# Backward-compat for tests and external consumers
_probe_device = DeviceCommands._probe
_format_device = DeviceCommands._format
_ensure_extracted = DeviceCommands._ensure_extracted
_get_driver = DeviceCommands._get_driver
send_image = DisplayCommands.send_image
send_color = DisplayCommands.send_color
reset_device = DisplayCommands.reset
resume = DisplayCommands.resume
show_info = SystemCommands.show_info
hid_debug = DiagCommands.hid_debug
led_diag = DiagCommands.led_diag
hr10_tempd = DiagCommands.hr10_tempd
setup_udev = SystemCommands.setup_udev
install_desktop = SystemCommands.install_desktop
uninstall = SystemCommands.uninstall
report = SystemCommands.report
download_themes = SystemCommands.download_themes
_hex_dump = DiagCommands._hex_dump
_hid_debug_lcd = DiagCommands._hid_debug_lcd
_hid_debug_led = DiagCommands._hid_debug_led


def _get_settings_path():
    """Deprecated — use conf.CONFIG_PATH instead."""
    from trcc.conf import CONFIG_DIR
    return os.path.join(CONFIG_DIR, 'settings.json')


def _get_selected_device():
    """Deprecated — use conf.get_selected_device() instead."""
    from trcc.conf import get_selected_device
    return get_selected_device()


def _set_selected_device(device_path):
    """Deprecated — use conf.save_selected_device() instead."""
    from trcc.conf import save_selected_device
    save_selected_device(device_path)


if __name__ == "__main__":
    sys.exit(main())
