# TRCC Linux - Installation & Setup Guide

A beginner-friendly guide to getting Thermalright LCD Control Center running on Linux.

---

## Table of Contents

1. [What is TRCC?](#what-is-trcc)
2. [Compatible Coolers](#compatible-coolers)
3. [Prerequisites](#prerequisites)
4. [Step 1 - Install System Dependencies](#step-1---install-system-dependencies)
5. [Step 2 - Download TRCC](#step-2---download-trcc)
6. [Step 3 - Install Python Dependencies](#step-3---install-python-dependencies)
7. [Step 4 - Set Up Device Permissions](#step-4---set-up-device-permissions)
8. [Step 5 - Connect Your Cooler](#step-5---connect-your-cooler)
9. [Step 6 - Run TRCC](#step-6---run-trcc)
10. [Using the GUI](#using-the-gui)
11. [Command Line Usage](#command-line-usage)
12. [Troubleshooting](#troubleshooting)
13. [Wayland-Specific Notes](#wayland-specific-notes)
14. [Uninstalling](#uninstalling)

---

## What is TRCC?

TRCC (Thermalright LCD Control Center) is software that controls the small LCD screen built into certain Thermalright CPU coolers and AIO liquid coolers. It lets you display custom images, animations, live system stats (CPU/GPU temperature, usage), clocks, and more on the cooler's built-in LCD.

This is the Linux version, ported from the official Windows TRCC 2.0.3 application.

---

## Compatible Coolers

TRCC Linux works with these Thermalright products that have a built-in LCD display:

**Air Coolers:**
- FROZEN WARFRAME / FROZEN WARFRAME SE
- FROZEN HORIZON PRO / FROZEN MAGIC PRO
- FROZEN VISION V2 / CORE VISION / ELITE VISION
- AK120 DIGITAL / AX120 DIGITAL / PA120 DIGITAL
- Wonder Vision (CZTV)

**AIO Liquid Coolers:**
- LC1 / LC2 / LC3 / LC5 (pump head display)

**Supported LCD Resolutions:**
- 240x240 pixels
- 320x320 pixels (most common)
- 480x480 pixels
- 640x480 pixels

> **Note:** If your cooler came with a Windows-only CD or download link for "TRCC" or "CZTV" software, it's compatible.

---

## Prerequisites

Before starting, make sure you have:

- A Linux distribution (Fedora, Ubuntu, Debian, Arch, openSUSE, etc.)
- Python 3.9 or newer (check with `python3 --version`)
- A Thermalright cooler with LCD, connected via the included USB cable
- Internet connection (for downloading dependencies)

### Check your Python version

Open a terminal and type:

```bash
python3 --version
```

You should see something like `Python 3.11.6` or higher. If you get "command not found" or a version below 3.9, you'll need to install or update Python first:

```bash
# Fedora
sudo dnf install python3

# Ubuntu/Debian
sudo apt install python3

# Arch
sudo pacman -S python
```

---

## Step 1 - Install System Dependencies

These are system-level packages that TRCC needs. Open a terminal and run the commands for your Linux distribution.

### Fedora / RHEL / CentOS Stream

```bash
# Required (GUI + device communication + video playback)
sudo dnf install python3-pip sg3_utils PyQt6 ffmpeg

# Optional (screen capture on Wayland, system tray)
sudo dnf install grim python3-gobject python3-dbus pipewire-devel
```

### Ubuntu / Debian / Linux Mint / Pop!_OS

```bash
# Required (GUI + device communication + video playback)
sudo apt install python3-pip python3-venv sg3-utils python3-pyqt6 ffmpeg

# Optional (screen capture on Wayland, system tray)
sudo apt install grim python3-gi python3-dbus python3-gst-1.0
```

### Arch Linux / Manjaro / EndeavourOS

```bash
# Required (GUI + device communication + video playback)
sudo pacman -S python-pip sg3_utils python-pyqt6 ffmpeg

# Optional (screen capture on Wayland, system tray)
sudo pacman -S grim python-gobject python-dbus python-gst
```

### openSUSE

```bash
# Required (GUI + device communication + video playback)
sudo zypper install python3-pip sg3_utils python3-qt6 ffmpeg

# Optional (screen capture on Wayland, system tray)
sudo zypper install grim python3-gobject python3-dbus-python python3-gstreamer
```

### What each package does

| Package | Why it's needed |
|---------|----------------|
| `python3-pip` | Installs Python packages (like TRCC itself) |
| `sg3_utils` | Sends data to the LCD over USB (SCSI commands) |
| `PyQt6` / `python3-pyqt6` | The graphical user interface (GUI) toolkit |
| `ffmpeg` | Video and GIF playback on the LCD |
| `grim` | Screen capture on Wayland desktops (optional) |
| `python3-gobject` / `python3-dbus` | PipeWire screen capture for GNOME/KDE Wayland (optional) |

---

## Step 2 - Download TRCC

### Option A: Clone with Git (recommended)

If you have `git` installed (most distros include it):

```bash
git clone https://github.com/Lexonight1/thermalright-trcc-linux.git
cd thermalright-trcc-linux
```

### Option B: Download ZIP

1. Go to the project page
2. Click the green "Code" button, then "Download ZIP"
3. Extract the ZIP file
4. Open a terminal and navigate to the extracted folder:

```bash
cd ~/Downloads/thermalright-trcc-linux-main
```

---

## Step 3 - Install Python Dependencies

From inside the `thermalright-trcc-linux` folder, install the Python packages:

```bash
pip install -e .
```

**What this does:** Installs TRCC and its Python dependencies (Pillow for image processing, psutil for system sensors, requests for cloud themes). The `-e` flag means "editable" - if you update the code later (with `git pull`), you don't need to reinstall.

> **Note:** Some distributions require using a virtual environment. If the `pip install` command shows an "externally-managed-environment" error, use one of these approaches:
>
> ```bash
> # Option 1: Use --break-system-packages (simpler)
> pip install --break-system-packages -e .
>
> # Option 2: Use a virtual environment (cleaner)
> python3 -m venv venv
> source venv/bin/activate
> pip install -e .
> # You'll need to run 'source venv/bin/activate' each time you open a new terminal
> ```

---

## Step 4 - Set Up Device Permissions

Linux needs permission rules to let TRCC talk to the LCD without requiring `sudo` every time. TRCC includes an automatic setup command for this.

### Run the setup command

```bash
sudo PYTHONPATH=src python3 -m trcc.cli setup-udev
```

Or, if you installed with `pip install -e .`:

```bash
sudo trcc setup-udev
```

**What this does:**
1. Creates a **udev rule** (`/etc/udev/rules.d/99-trcc-lcd.rules`) that gives your user account permission to access the LCD's USB device
2. Creates a **USB quirk** (`/etc/modprobe.d/trcc-lcd.conf`) that tells the kernel to use the correct USB protocol for the LCD

### Preview first (optional)

If you want to see what the command will do before running it:

```bash
trcc setup-udev --dry-run
```

### After running setup-udev

**Unplug and replug your cooler's USB cable.** The new permissions take effect when the device reconnects.

> **Why is this needed?** On Linux, USB devices default to root-only access. The udev rule changes this for Thermalright LCDs specifically. The USB quirk is needed because the kernel otherwise tries to use a protocol (UAS) that these displays don't support, which prevents the LCD from being detected at all.

---

## Step 5 - Connect Your Cooler

1. **Plug in the USB cable** from your cooler to your computer
2. **Wait a few seconds** for Linux to detect the device
3. **Verify it's detected:**

```bash
trcc detect
```

You should see something like:

```
Active: /dev/sg1
```

If you have multiple Thermalright LCD devices:

```bash
trcc detect --all
```

Shows all connected devices with numbers you can use to switch between them:

```
* [1] /dev/sg1 (LCD Display (USBLCD))
  [2] /dev/sg2 (FROZEN WARFRAME)
```

### Quick test

Send a test pattern to make sure everything works:

```bash
trcc test
```

This cycles through red, green, blue, yellow, magenta, cyan, and white. If you see the colors on your cooler's LCD, everything is set up correctly.

---

## Step 6 - Run TRCC

Launch the GUI:

```bash
trcc gui
```

Or, if you didn't install with pip:

```bash
PYTHONPATH=src python3 -m trcc.cli gui
```

The application window will appear, showing the same interface as the Windows version.

> **Tip:** To run with a normal window title bar (for easier resizing/moving while getting used to the app):
> ```bash
> trcc gui --decorated
> ```

---

## Using the GUI

The interface has several main areas:

### Left Sidebar (Device Panel)

Shows your connected Thermalright cooler(s). Click a device to select it. The blue highlighted device is the one currently being controlled.

- **Sensor** button: Opens a live system info display
- **About** button: Settings (LCD resolution, language, auto-start, temperature units)

### Top Tabs

Four tabs to switch between different modes:

| Tab | What it does |
|-----|-------------|
| **Local** | Browse themes saved on your computer |
| **Masks** | Download and apply mask overlays (clocks, gauges, etc.) |
| **Cloud** | Browse and download themes from the Thermalright cloud server |
| **Settings** | Configure overlay elements (text, sensors, masks, display modes) |

### Preview Area (Center)

Shows a live preview of what's currently displayed (or about to be displayed) on the LCD. Below the preview are video playback controls when playing animated themes.

### Bottom Bar

- **Rotation** dropdown: Rotate the display (0/90/180/270 degrees)
- **Brightness** button: Adjust LCD brightness
- **Theme name** field + **Save** button: Name and save your current theme
- **Export/Import** buttons: Share themes as files

### Common Workflow

1. **Pick a theme:** Click the "Local" tab, then click a theme thumbnail
2. **Preview it:** The preview area updates to show the theme
3. **Customize it:** Switch to the "Settings" tab to add overlays (CPU temp, clock, custom text, etc.)
4. **Send to LCD:** The preview automatically sends to the connected LCD. Or click a theme to apply it.

### Display Modes (Settings Tab)

The Settings tab has several display mode panels at the bottom:

| Mode | What it does |
|------|-------------|
| **Mask** | Enable/disable the mask overlay layer |
| **Background** | Toggle background image on/off |
| **Screen Cast** | Mirror a region of your desktop to the LCD in real-time |
| **Video** | Play video/GIF files on the LCD |

### Screen Cast (Desktop Mirroring)

This mirrors a portion of your screen onto the LCD in real-time:

1. Go to the **Settings** tab
2. Find the **Screen Cast** panel
3. Set the **X, Y, W, H** values to define which part of your screen to capture
4. Toggle the screencast on

Example: X=0, Y=0, W=500, H=500 captures a 500x500 square from the top-left of your screen.

---

## Command Line Usage

TRCC also works from the terminal without the GUI:

```bash
# Show all available commands
trcc --help

# Detect connected devices
trcc detect
trcc detect --all

# Select a specific device (by number from detect --all)
trcc select 2

# Send an image to the LCD
trcc send /path/to/image.png

# Display a solid color (hex code, no # needed)
trcc color ff0000          # Red
trcc color 00ff00          # Green
trcc color 0000ff          # Blue

# Test the display with a color cycle
trcc test
trcc test --loop           # Loop continuously (Ctrl+C to stop)

# Show system info (CPU/GPU temps, memory, etc.)
trcc info

# Reset/reinitialize the LCD
trcc reset

# Download cloud theme packs
trcc download --list       # See available packs
trcc download themes-320   # Download 320x320 themes
```

---

## Troubleshooting

### "No compatible TRCC LCD device detected"

**Cause:** The LCD isn't showing up as a SCSI device.

**Fix:**
1. Make sure the USB cable is plugged into both the cooler and your computer
2. Run the udev setup if you haven't already: `sudo trcc setup-udev`
3. Unplug and replug the USB cable
4. Check if the device appears: `ls /dev/sg*`
5. Check `dmesg | tail -20` right after plugging in to see kernel messages

### "Permission denied" when accessing the device

**Cause:** Udev rules not set up, or you need to replug the device after setup.

**Fix:**
```bash
sudo trcc setup-udev
# Then unplug and replug the USB cable
```

### "Error: PyQt6 not available"

**Cause:** PyQt6 isn't installed.

**Fix:** Install it for your distro (see Step 1), or:
```bash
pip install PyQt6
```

### LCD stays blank or shows old image

**Cause:** The device may need a reset.

**Fix:**
```bash
trcc reset
```

### Video/GIF playback not working

**Cause:** FFmpeg not installed.

**Fix:** Install ffmpeg for your distro (see Step 1):
```bash
# Fedora
sudo dnf install ffmpeg

# Ubuntu/Debian
sudo apt install ffmpeg

# Arch
sudo pacman -S ffmpeg
```

### GUI looks wrong / elements overlapping

**Cause:** Display scaling (HiDPI) interfering with the fixed-size layout.

**Fix:** Try running with scaling disabled:
```bash
QT_AUTO_SCREEN_SCALE_FACTOR=0 trcc gui
```

### "externally-managed-environment" error during pip install

**Cause:** Your distro protects system Python packages (common on Fedora 38+, Ubuntu 23.04+).

**Fix:** Use one of these approaches:
```bash
# Approach 1: Allow pip to install alongside system packages
pip install --break-system-packages -e .

# Approach 2: Use a virtual environment
python3 -m venv ~/trcc-env
source ~/trcc-env/bin/activate
pip install -e .
# Remember to activate the venv each time: source ~/trcc-env/bin/activate
```

### Screen cast shows black/blank on Wayland (GNOME/KDE)

**Cause:** GNOME and KDE Wayland don't allow direct screen capture for security. PipeWire portal is needed.

**Fix:** Install PipeWire dependencies:
```bash
# Fedora
sudo dnf install python3-gobject python3-dbus pipewire-devel

# Ubuntu/Debian
sudo apt install python3-gi python3-dbus python3-gst-1.0

# Arch
sudo pacman -S python-gobject python-dbus python-gst
```

When you enable Screen Cast, a system dialog will pop up asking you to grant permission. This is normal — select the screen/monitor you want to capture and click "Share".

> **Note:** Screen cast works automatically on X11 and Wayland with wlroots-based compositors (Sway, Hyprland). The PipeWire portal is only needed for GNOME and KDE Wayland sessions.

---

## Wayland-Specific Notes

Linux has two display systems: **X11** (older, but widely supported) and **Wayland** (newer, more secure). Most features work on both, but there are some differences:

### How to check which one you're using

```bash
echo $XDG_SESSION_TYPE
```

This prints either `x11` or `wayland`.

### Wayland differences

- **Screen capture:** Works via PipeWire portal (requires permission dialog on first use)
- **Eyedropper color picker:** Uses the same PipeWire portal for screen access
- **Window decorations:** The app uses its own custom title bar by default on both X11 and Wayland. Use `--decorated` if you prefer your desktop's native title bar.

Everything else (themes, overlays, video playback, device communication) works identically on both X11 and Wayland.

---

## Uninstalling

### Remove TRCC

```bash
pip uninstall trcc-linux
```

### Remove udev rules (optional)

```bash
sudo rm /etc/udev/rules.d/99-trcc-lcd.rules
sudo rm /etc/modprobe.d/trcc-lcd.conf
sudo udevadm control --reload-rules
```

### Remove config files (optional)

```bash
rm -rf ~/.config/trcc
```

---

## Getting Help

- Check the [Troubleshooting](#troubleshooting) section above
- Look at the terminal output for error messages (run `trcc gui -v` for verbose output, or `trcc gui -vv` for debug output)
- File an issue at https://github.com/Lexonight1/thermalright-trcc-linux/issues
