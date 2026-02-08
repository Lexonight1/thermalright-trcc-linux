# TRCC Linux - Installation & Setup Guide

A beginner-friendly guide to getting Thermalright LCD Control Center running on Linux.

---

## Table of Contents

1. [What is TRCC?](#what-is-trcc)
2. [Compatible Coolers](#compatible-coolers)
3. [HID Device Support (Testing Branch)](#hid-device-support-testing-branch)
4. [Prerequisites](#prerequisites)
5. [Step 1 - Install System Dependencies](#step-1---install-system-dependencies)
   - [Fedora / RHEL / CentOS Stream / Rocky / Alma](#fedora--rhel--centos-stream--rocky--alma)
   - [Ubuntu / Debian / Linux Mint / Pop!_OS / Zorin / elementary](#ubuntu--debian--linux-mint--pop_os--zorin--elementary)
   - [Arch Linux / Manjaro / EndeavourOS / CachyOS / Garuda](#arch-linux--manjaro--endeavouros--cachyos--garuda)
   - [openSUSE Tumbleweed / Leap](#opensuse-tumbleweed--leap)
   - [Nobara](#nobara)
   - [NixOS](#nixos)
   - [Void Linux](#void-linux)
   - [Gentoo](#gentoo)
   - [Alpine Linux](#alpine-linux)
   - [Solus](#solus)
   - [Clear Linux](#clear-linux)
6. [Step 2 - Download TRCC](#step-2---download-trcc)
7. [Step 3 - Install Python Dependencies](#step-3---install-python-dependencies)
8. [Step 4 - Set Up Device Permissions](#step-4---set-up-device-permissions)
9. [Step 5 - Connect Your Cooler](#step-5---connect-your-cooler)
10. [Step 6 - Run TRCC](#step-6---run-trcc)
11. [Immutable / Atomic Distros](#immutable--atomic-distros)
    - [Bazzite / Fedora Atomic / Aurora / Bluefin](#bazzite--fedora-atomic--aurora--bluefin)
    - [SteamOS (Steam Deck)](#steamos-steam-deck)
    - [Vanilla OS](#vanilla-os)
    - [ChromeOS (Crostini)](#chromeos-crostini)
12. [Special Hardware](#special-hardware)
    - [Asahi Linux (Apple Silicon)](#asahi-linux-apple-silicon)
    - [Raspberry Pi / ARM SBCs](#raspberry-pi--arm-sbcs)
    - [WSL2 (Windows Subsystem for Linux)](#wsl2-windows-subsystem-for-linux)
13. [Using the GUI](#using-the-gui)
14. [Command Line Usage](#command-line-usage)
15. [Troubleshooting](#troubleshooting)
16. [Wayland-Specific Notes](#wayland-specific-notes)
17. [Uninstalling](#uninstalling)

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

## HID Device Support (Experimental)

> **WE NEED TESTERS!** HID device support is implemented with 563 automated tests but **not validated with real hardware**. If you have an HID device, please test and report results at https://github.com/Lexonight1/thermalright-trcc-linux/issues

HID devices are auto-detected — no special flags needed. Just install TRCC normally and run:

```bash
trcc detect       # Check if your device is found
trcc gui          # Launch the GUI (HID auto-detected)
```

See the **[HID Testing Guide](HID_TESTING.md)** for supported devices and what to report.

---

> **New to Linux?** If you're coming from Windows or Mac, see [New to Linux?](NEW_TO_LINUX.md) for a quick primer on terminals, package managers, and other concepts used in this guide.

---

## Prerequisites

Before starting, make sure you have:

- A Linux distribution (see supported list below)
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
# Fedora / RHEL
sudo dnf install python3

# Ubuntu / Debian
sudo apt install python3

# Arch
sudo pacman -S python

# openSUSE
sudo zypper install python3

# Void
sudo xbps-install python3

# Alpine
sudo apk add python3
```

---

## Step 1 - Install System Dependencies

These are system-level packages that TRCC needs. Find your distro below and run the commands.

> **Important: Use system PyQt6 when possible.** Installing PyQt6 from your distro's package manager avoids Qt6 version mismatches that cause `Qt_6_PRIVATE_API` errors. Only fall back to `pip install PyQt6` if your distro doesn't package it.

---

### Fedora / RHEL / CentOS Stream / Rocky / Alma

Covers: Fedora 39-43, RHEL 9+, CentOS Stream 9+, Rocky Linux 9+, AlmaLinux 9+

```bash
# Required
sudo dnf install python3-pip sg3_utils python3-pyqt6 ffmpeg

# Optional (hardware sensors, screen capture on Wayland, NVIDIA GPU sensors)
sudo dnf install lm_sensors grim python3-gobject python3-dbus pipewire-devel
```

> **RHEL/Rocky/Alma note:** You may need to enable EPEL and CRB repositories for `ffmpeg` and `python3-pyqt6`:
> ```bash
> sudo dnf install epel-release
> sudo dnf config-manager --set-enabled crb
> ```
> If `python3-pyqt6` isn't available, use `pip install PyQt6` instead.

---

### Ubuntu / Debian / Linux Mint / Pop!_OS / Zorin / elementary

Covers: Ubuntu 22.04+, Debian 12+, Linux Mint 21+, Pop!_OS 22.04+, Zorin OS 17+, elementary OS 7+, KDE neon, Kubuntu, Xubuntu, Lubuntu

```bash
# Required
sudo apt install python3-pip python3-venv sg3-utils python3-pyqt6 ffmpeg

# Optional (hardware sensors, screen capture on Wayland, system tray)
sudo apt install lm-sensors grim python3-gi python3-dbus python3-gst-1.0
```

> **Debian 12 (Bookworm) note:** `python3-pyqt6` is available in the repo. On older Debian/Ubuntu releases where it's missing, use `pip install PyQt6`.

> **Ubuntu 23.04+ / Debian 12+ note:** pip may show "externally-managed-environment" errors. See [Step 3](#step-3---install-python-dependencies) for the fix.

---

### Arch Linux / Manjaro / EndeavourOS / CachyOS / Garuda

Covers: Arch Linux, Manjaro, EndeavourOS, CachyOS, Garuda Linux, Artix Linux, ArcoLinux, BlackArch

```bash
# Required
sudo pacman -S python-pip sg3_utils python-pyqt6 ffmpeg

# Optional (hardware sensors, screen capture on Wayland, NVIDIA GPU sensors)
sudo pacman -S lm_sensors grim python-gobject python-dbus python-gst
```

> **CachyOS note:** CachyOS ships its own optimized repos. The package names are the same as Arch. If you use the CachyOS kernel, `sg3_utils` works out of the box.

> **Garuda note:** Garuda includes `chaotic-aur` by default, so most packages are available without building from source.

---

### openSUSE Tumbleweed / Leap

Covers: openSUSE Tumbleweed, openSUSE Leap 15.5+, openSUSE MicroOS

```bash
# Required
sudo zypper install python3-pip sg3_utils python3-qt6 ffmpeg

# Optional (hardware sensors, screen capture on Wayland)
sudo zypper install sensors grim python3-gobject python3-dbus-python python3-gstreamer
```

> **Leap note:** Leap's repos may have older PyQt6 versions. If you get import errors, use `pip install PyQt6` instead.

> **MicroOS note:** openSUSE MicroOS is immutable. Use `transactional-update` instead of `zypper`:
> ```bash
> sudo transactional-update pkg install sg3_utils python3-pip python3-qt6 ffmpeg
> sudo reboot
> ```

---

### Nobara

Covers: Nobara 39-41 (Fedora-based gaming distro by GloriousEggroll)

Nobara uses the same package manager as Fedora, with extra multimedia repos pre-configured:

```bash
# Required (ffmpeg is usually pre-installed on Nobara)
sudo dnf install python3-pip sg3_utils python3-pyqt6 ffmpeg

# Optional (hardware sensors, screen capture on Wayland)
sudo dnf install lm_sensors grim python3-gobject python3-dbus pipewire-devel
```

---

### NixOS

Covers: NixOS 24.05+, NixOS unstable

NixOS uses a declarative configuration model. You have two approaches:

**Option A: Add to system configuration** (persistent, recommended)

Edit `/etc/nixos/configuration.nix`:

```nix
{ pkgs, ... }:
{
  environment.systemPackages = with pkgs; [
    python3
    python3Packages.pip
    python3Packages.pyqt6
    python3Packages.pillow
    python3Packages.psutil
    sg3_utils
    lm_sensors
    ffmpeg
    p7zip
  ];

  # Allow your user to access SCSI generic devices
  services.udev.extraRules = ''
    # Thermalright LCD displays
    SUBSYSTEM=="scsi_generic", ATTRS{idVendor}=="87cd", ATTRS{idProduct}=="70db", MODE="0660"
    SUBSYSTEM=="scsi_generic", ATTRS{idVendor}=="0416", ATTRS{idProduct}=="5406", MODE="0660"
    SUBSYSTEM=="scsi_generic", ATTRS{idVendor}=="0402", ATTRS{idProduct}=="3922", MODE="0660"
  '';
}
```

Then rebuild:

```bash
sudo nixos-rebuild switch
```

**Option B: Use nix-shell** (temporary, for testing)

```bash
nix-shell -p python3 python3Packages.pip python3Packages.pyqt6 python3Packages.pillow python3Packages.psutil sg3_utils ffmpeg
```

Then follow [Step 2](#step-2---download-trcc) and [Step 3](#step-3---install-python-dependencies) from inside the shell.

> **NixOS note:** The `trcc setup-udev` command won't work on NixOS because udev rules are managed declaratively. Add the rules to your `configuration.nix` as shown in Option A instead.

---

### Void Linux

Covers: Void Linux (glibc and musl)

```bash
# Required
sudo xbps-install sg3_utils python3-pip python3-PyQt6 ffmpeg

# Optional (hardware sensors, screen capture on Wayland)
sudo xbps-install lm_sensors grim python3-gobject python3-dbus python3-gst
```

> **Void musl note:** Some Python packages may not have pre-built wheels for musl. You may need `python3-devel` and a C compiler to build them:
> ```bash
> sudo xbps-install python3-devel gcc
> ```

> **Void note:** If `python3-PyQt6` is not in the repo, install via pip:
> ```bash
> sudo xbps-install python3-pip qt6-base
> pip install PyQt6
> ```

---

### Gentoo

Covers: Gentoo Linux, Funtoo, Calculate Linux

```bash
# Required
sudo emerge --ask sg3_utils dev-python/pip dev-python/PyQt6 media-video/ffmpeg

# Optional (hardware sensors, screen capture on Wayland)
sudo emerge --ask sys-apps/lm-sensors gui-apps/grim dev-python/pygobject dev-python/dbus-python
```

> **USE flags:** Make sure your PyQt6 package has the `widgets` and `gui` USE flags enabled:
> ```bash
> echo "dev-python/PyQt6 widgets gui" | sudo tee -a /etc/portage/package.use/trcc
> ```

> **Gentoo note:** If `dev-python/PyQt6` is masked, you may need to unmask it:
> ```bash
> echo "dev-python/PyQt6 ~amd64" | sudo tee -a /etc/portage/package.accept_keywords/trcc
> ```

---

### Alpine Linux

Covers: Alpine Linux 3.18+, postmarketOS

```bash
# Required
sudo apk add python3 py3-pip sg3_utils py3-pyqt6 ffmpeg

# Optional (hardware sensors, screen capture on Wayland)
sudo apk add lm-sensors grim py3-gobject3 py3-dbus
```

> **Alpine note:** Alpine uses musl libc. If `py3-pyqt6` isn't available in your release, you'll need to install from pip with build dependencies:
> ```bash
> sudo apk add python3-dev gcc musl-dev qt6-qtbase-dev
> pip install PyQt6
> ```

---

### Solus

Covers: Solus 4.x (Budgie, GNOME, MATE, Plasma editions)

```bash
# Required
sudo eopkg install sg3_utils python3-pip ffmpeg

# PyQt6 (may need pip)
pip install PyQt6

# Optional (hardware sensors, screen capture on Wayland)
sudo eopkg install lm-sensors grim python3-gobject python3-dbus
```

---

### Clear Linux

Covers: Clear Linux OS (Intel)

```bash
# Required
sudo swupd bundle-add python3-basic devpkg-sg3_utils ffmpeg

# PyQt6 via pip (not bundled in Clear Linux)
pip install PyQt6

# Optional (hardware sensors, screen capture on Wayland)
sudo swupd bundle-add sysadmin-basic devpkg-pipewire
```

> **Clear Linux note:** You may need to install `sg3_utils` from source or find it in an alternative bundle. Check `sudo swupd search sg3` for the current bundle name.

---

### What each package does

| Package | Why it's needed |
|---------|----------------|
| `python3-pip` | Installs Python packages (like TRCC itself) |
| `sg3_utils` | Sends data to the LCD over USB (SCSI commands) — **required for SCSI devices** |
| `lm-sensors` / `lm_sensors` | Hardware sensor readings (CPU/GPU temps, fan speeds) — improves sensor accuracy |
| `PyQt6` / `python3-pyqt6` | The graphical user interface (GUI) toolkit |
| `ffmpeg` | Video and GIF playback on the LCD |
| `p7zip` / `7zip` | Extracts bundled theme `.7z` archives (optional if `py7zr` is installed) |
| `grim` | Screen capture on Wayland desktops (optional) |
| `python3-gobject` / `python3-dbus` | PipeWire screen capture for GNOME/KDE Wayland (optional) |
| `pyusb` + `libusb` | USB communication for HID LCD devices (optional, testing branch only) |
| `hidapi` + `libhidapi` | Fallback USB backend for HID LCD devices (optional, testing branch only) |

---

## Step 2 - Download TRCC

### Option A: Clone with Git (recommended)

If you have `git` installed (most distros include it):

```bash
[ -d thermalright-trcc-linux ] && git -C thermalright-trcc-linux pull || git clone https://github.com/Lexonight1/thermalright-trcc-linux.git
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

**What this does:** Installs TRCC and its Python dependencies (Pillow for image processing, psutil for system sensors, py7zr for extracting bundled theme archives). The `-e` flag means "editable" - if you update the code later (with `git pull`), you don't need to reinstall.

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

> **Distros that enforce this by default:** Fedora 38+, Ubuntu 23.04+, Debian 12+, Arch (with python 3.11+), openSUSE Tumbleweed, Void Linux

### Ensure `~/.local/bin` is in your PATH

When you install with `pip install`, the `trcc` command is placed in `~/.local/bin/`. On many distros this directory is **not** in your `PATH` by default, so the `trcc` command won't be found after a reboot.

Add it to your shell config:

```bash
# Bash (~/.bashrc)
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc

# Zsh (~/.zshrc) — default on Arch, Garuda, some Manjaro
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc

# Fish (~/.config/fish/config.fish)
fish_add_path ~/.local/bin
```

| Distro | `~/.local/bin` in PATH by default? |
|--------|-----------------------------------|
| Fedora | Yes (usually works without this step) |
| Ubuntu / Debian | Conditionally — only if the directory exists at login time |
| Arch / Manjaro / EndeavourOS | No |
| openSUSE | No |
| Void / Alpine | No |

> **Tip:** You can verify with `echo $PATH | tr ':' '\n' | grep local`. If you see `~/.local/bin` (or `/home/yourname/.local/bin`), you're good.

---

## Step 4 - Set Up Device Permissions

Linux needs permission rules to let TRCC talk to the LCD without requiring `sudo` every time. TRCC includes an automatic setup command for this.

### Run the setup command

```bash
trcc setup-udev
```

Or, if you installed with `pip install -e .`:

```bash
trcc setup-udev
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

**Unplug and replug the USB cable.** If the cable isn't easily accessible (internal header), reboot your computer instead. The new permissions take effect when the device reconnects.

> **Why is this needed?** On Linux, USB devices default to root-only access. The udev rule changes this for Thermalright LCDs specifically. The USB quirk is needed because the kernel otherwise tries to use a protocol (UAS) that these displays don't support, which prevents the LCD from being detected at all.

> **NixOS users:** Skip this step. Add the udev rules to your `configuration.nix` instead (see [NixOS section](#nixos)).

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

### Create a desktop shortcut (optional)

If you'd rather launch TRCC from your app menu instead of typing a command every time, create a `.desktop` file:

```bash
mkdir -p ~/.local/share/applications
cat > ~/.local/share/applications/trcc.desktop << 'EOF'
[Desktop Entry]
Name=TRCC LCD Control
Comment=Thermalright LCD Control Center
Exec=trcc gui
Icon=preferences-desktop-display
Terminal=false
Type=Application
Categories=Utility;System;
EOF
```

This adds "TRCC LCD Control" to your application menu. On most desktops it appears immediately; on some you may need to log out and back in.

> **Using a venv?** If you installed TRCC in a virtual environment, change the `Exec` line to:
> ```
> Exec=bash -c 'source ~/trcc-env/bin/activate && trcc gui'
> ```

---

## Immutable / Atomic Distros

These distros have read-only root filesystems. Standard package installation doesn't work the same way.

---

### Bazzite / Fedora Atomic / Aurora / Bluefin

Covers: Bazzite, Aurora, Bluefin, Fedora Silverblue, Fedora Kinoite, and all Universal Blue / Fedora Atomic desktops

These use an **immutable root filesystem**. Standard `dnf install` doesn't work — you layer packages with `rpm-ostree` (requires reboot) or install userspace tools via `brew`, `pip`, or containers.

#### Why it's different

| Normal Fedora | Bazzite / Fedora Atomic |
|---------------|-------------------------|
| `sudo dnf install pkg` | `rpm-ostree install pkg` + reboot |
| Packages available immediately | Layered packages available after reboot |
| System Python is writable | System Python is read-only — use a venv |

The goal is to layer as little as possible onto the base image and do everything else in a Python virtual environment.

#### Step 1 — Layer `sg3_utils`

`sg3_utils` provides the `sg_raw` command that TRCC uses to send SCSI commands to the LCD over USB. This **must** be on the host system (not inside a container) because it needs direct access to `/dev/sg*` devices.

```bash
rpm-ostree install sg3_utils
systemctl reboot
```

After rebooting, verify it's available:

```bash
which sg_raw
```

> **Note:** If you want to avoid layering and rebooting, you can also use `brew install sg3_utils` on Bazzite (Homebrew is pre-installed). However, the `rpm-ostree` approach is more reliable for system-level hardware tools.

#### Step 2 — Clone TRCC

```bash
[ -d thermalright-trcc-linux ] && git -C thermalright-trcc-linux pull || git clone https://github.com/Lexonight1/thermalright-trcc-linux.git
cd thermalright-trcc-linux
```

#### Step 3 — Create a Python virtual environment

Bazzite's system Python is read-only, so a venv is **required** (not optional like on normal Fedora):

```bash
python3 -m venv ~/trcc-env
source ~/trcc-env/bin/activate
```

Install TRCC and its dependencies inside the venv:

```bash
pip install -e .
```

This pulls in PyQt6, Pillow, psutil, py7zr, and everything else TRCC needs.

> **Tip:** Add the activation to your shell profile so it's automatic:
> ```bash
> echo 'alias trcc-env="source ~/trcc-env/bin/activate"' >> ~/.bashrc
> ```

#### Step 4 — Install FFmpeg (for video/GIF playback)

Bazzite ships FFmpeg by default. Verify with:

```bash
ffmpeg -version
```

If for some reason it's missing:

```bash
brew install ffmpeg
```

#### Step 5 — Set up device permissions

Udev rules live on the host filesystem and work the same as on normal Fedora:

```bash
source ~/trcc-env/bin/activate
trcc setup-udev
```

Or if installed via pip:

```bash
trcc setup-udev
```

Then **unplug and replug the USB cable** (or reboot if it's not easily accessible).

#### Step 6 — Run TRCC

```bash
source ~/trcc-env/bin/activate
trcc gui
```

#### Optional: Create a desktop shortcut

So you don't need to activate the venv manually each time:

```bash
mkdir -p ~/.local/share/applications
cat > ~/.local/share/applications/trcc.desktop << 'EOF'
[Desktop Entry]
Name=TRCC LCD Control
Comment=Thermalright LCD Control Center
Exec=bash -c 'source ~/trcc-env/bin/activate && trcc gui'
Icon=preferences-desktop-display
Terminal=false
Type=Application
Categories=Utility;System;
EOF
```

#### Optional: Wayland screen capture

Bazzite uses Wayland (KDE or GNOME) by default. For screen cast / eyedropper features, install the PipeWire bindings inside your venv:

```bash
source ~/trcc-env/bin/activate
pip install dbus-python PyGObject
```

> **Note:** `pipewire` and `pipewire-devel` are already included in Bazzite's base image.

#### Alternative: Distrobox approach

If you prefer full isolation, you can run TRCC inside a Distrobox container. This avoids layering anything with `rpm-ostree`:

```bash
# Create a Fedora container
distrobox create --name trcc --image fedora:latest
distrobox enter trcc

# Inside the container — normal Fedora commands work
sudo dnf install python3-pip sg3_utils python3-pyqt6 ffmpeg
[ -d thermalright-trcc-linux ] && git -C thermalright-trcc-linux pull || git clone https://github.com/Lexonight1/thermalright-trcc-linux.git
cd thermalright-trcc-linux
pip install -e .

# Set up device permissions (must run on the host)
exit
sudo distrobox-host-exec trcc setup-udev
# Unplug/replug USB cable, or reboot

# Run the GUI from the container
distrobox enter trcc -- trcc gui
```

> **Caveat:** The udev rules and USB quirk still need to be set up on the **host** system. The Distrobox container can access `/dev/sg*` devices through the host, but permissions must be configured on the host side. You may need to run `setup-udev` directly on the host rather than through `distrobox-host-exec`.

#### Uninstalling on Bazzite

```bash
# Remove the venv
rm -rf ~/trcc-env

# Remove the cloned repo
rm -rf ~/thermalright-trcc-linux

# Remove desktop shortcut (if created)
rm ~/.local/share/applications/trcc.desktop

# Unlayer sg3_utils (optional)
rpm-ostree uninstall sg3_utils
systemctl reboot

# Remove udev rules (optional)
sudo rm /etc/udev/rules.d/99-trcc-lcd.rules
sudo rm /etc/modprobe.d/trcc-lcd.conf
sudo udevadm control --reload-rules
```

---

### SteamOS (Steam Deck)

Covers: SteamOS 3.x on Steam Deck (LCD and OLED models)

SteamOS is an immutable Arch-based distro. The root filesystem is read-only by default, but you can temporarily unlock it.

#### Option A: Unlock root filesystem (simpler, lost on SteamOS updates)

Switch to Desktop Mode (hold Power button > Desktop Mode), then open Konsole:

```bash
# Disable read-only filesystem
sudo steamos-readonly disable

# Set a password if you haven't already
passwd

# Install system deps
sudo pacman -S --needed sg3_utils python-pip python-pyqt6 ffmpeg

# Clone and install
[ -d thermalright-trcc-linux ] && git -C thermalright-trcc-linux pull || git clone https://github.com/Lexonight1/thermalright-trcc-linux.git
cd thermalright-trcc-linux
pip install --break-system-packages -e .

# Set up device permissions
trcc setup-udev
# Unplug/replug USB cable, or reboot

# Re-enable read-only (optional, recommended)
sudo steamos-readonly enable

# Launch
trcc gui
```

> **Warning:** `steamos-readonly disable` changes are lost when SteamOS updates. You'll need to re-install system packages after each update. Python packages installed with `pip --break-system-packages` persist in your home directory.

#### Option B: Distrobox (survives updates)

```bash
# In Desktop Mode, open Konsole
distrobox create --name trcc --image archlinux:latest
distrobox enter trcc

# Inside the container
sudo pacman -S python-pip sg3_utils python-pyqt6 ffmpeg
[ -d thermalright-trcc-linux ] && git -C thermalright-trcc-linux pull || git clone https://github.com/Lexonight1/thermalright-trcc-linux.git
cd thermalright-trcc-linux
pip install -e .
exit

# Set up udev on the HOST (requires steamos-readonly disable temporarily)
sudo steamos-readonly disable
sudo distrobox-host-exec trcc setup-udev
sudo steamos-readonly enable
# Unplug/replug USB cable, or reboot

# Run from Distrobox
distrobox enter trcc -- trcc gui
```

---

### Vanilla OS

Covers: Vanilla OS 2.x (Orchid)

Vanilla OS uses `apx` (based on Distrobox) for package management:

```bash
# Create a Fedora subsystem
apx subsystems create --name trcc-system --stack fedora

# Install dependencies inside the subsystem
apx trcc-system install python3-pip sg3_utils python3-pyqt6 ffmpeg

# Clone and install
[ -d thermalright-trcc-linux ] && git -C thermalright-trcc-linux pull || git clone https://github.com/Lexonight1/thermalright-trcc-linux.git
cd thermalright-trcc-linux

# Enter the subsystem and install
apx trcc-system enter
pip install -e .
exit

# Udev rules must be applied on the host
# Copy the rules manually (setup-udev won't work from inside apx)
sudo cp /path/to/99-trcc-lcd.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
# Unplug/replug USB cable, or reboot

# Run
apx trcc-system run -- trcc gui
```

---

### ChromeOS (Crostini)

Covers: ChromeOS with Linux development environment enabled (Crostini / Debian container)

1. Enable Linux: Settings > Advanced > Developers > Turn On Linux development environment
2. Open the Linux terminal, then follow the Debian instructions:

```bash
sudo apt update
sudo apt install python3-pip python3-venv sg3-utils python3-pyqt6 ffmpeg

[ -d thermalright-trcc-linux ] && git -C thermalright-trcc-linux pull || git clone https://github.com/Lexonight1/thermalright-trcc-linux.git
cd thermalright-trcc-linux
pip install --break-system-packages -e .
```

> **ChromeOS limitation:** USB device passthrough to the Linux container requires enabling it in ChromeOS settings. Go to Settings > Advanced > Developers > Linux > Manage USB devices, and enable your Thermalright LCD device. You may also need to run `trcc setup-udev` inside the container and replug the USB device.

```bash
trcc setup-udev
trcc gui
```

---

## Special Hardware

---

### Asahi Linux (Apple Silicon)

Covers: Fedora Asahi Remix on Apple M1/M2/M3/M4 Macs

Asahi Linux uses the Fedora Asahi Remix. Follow the standard [Fedora instructions](#fedora--rhel--centos-stream--rocky--alma):

```bash
sudo dnf install python3-pip sg3_utils python3-pyqt6 ffmpeg
[ -d thermalright-trcc-linux ] && git -C thermalright-trcc-linux pull || git clone https://github.com/Lexonight1/thermalright-trcc-linux.git
cd thermalright-trcc-linux
pip install -e .
trcc setup-udev
```

> **Apple Silicon note:** USB-A ports on Apple Silicon Macs work through Thunderbolt hubs/docks. Make sure your USB connection to the cooler is going through a compatible hub. Direct USB-C adapters should also work.

---

### Raspberry Pi / ARM SBCs

Covers: Raspberry Pi OS (Bookworm), Ubuntu for Raspberry Pi, Armbian

TRCC works on ARM64 (aarch64) systems. The SCSI protocol and LCD communication are architecture-independent.

```bash
# Raspberry Pi OS / Armbian (Debian-based)
sudo apt install python3-pip python3-venv sg3-utils python3-pyqt6 ffmpeg

[ -d thermalright-trcc-linux ] && git -C thermalright-trcc-linux pull || git clone https://github.com/Lexonight1/thermalright-trcc-linux.git
cd thermalright-trcc-linux
pip install -e .
trcc setup-udev
```

> **ARM note:** PyQt6 wheels may not be available for ARM. If `pip install PyQt6` fails, use the system package (`python3-pyqt6`) or build from source. The CLI commands (`trcc send`, `trcc test`, `trcc color`) work without PyQt6 — only the GUI requires it.

> **Headless usage:** If you're running on a Pi without a display, you can still use the CLI to send images to the LCD:
> ```bash
> trcc send /path/to/image.png
> trcc color ff0000
> ```

---

### WSL2 (Windows Subsystem for Linux)

Covers: WSL2 on Windows 10/11

> **You probably want the Windows version instead.** WSL2 has limited USB passthrough and you'd need the official Windows TRCC app for the best experience. However, if you want to use the Linux version:

WSL2 requires `usbipd-win` to pass USB devices through:

1. **On Windows:** Install [usbipd-win](https://github.com/dorssel/usbipd-win) from the Microsoft Store or GitHub
2. **On Windows (PowerShell as admin):**
   ```powershell
   usbipd list                          # Find your Thermalright device
   usbipd bind --busid <BUSID>          # Bind it
   usbipd attach --wsl --busid <BUSID>  # Attach to WSL
   ```
3. **Inside WSL2:** Follow the [Ubuntu/Debian instructions](#ubuntu--debian--linux-mint--pop_os--zorin--elementary)

> **WSL2 note:** You need to re-attach the USB device every time you restart WSL or unplug the device. GUI apps require WSLg (included in Windows 11 and recent Windows 10 updates).

---

## Using the GUI

The interface has several main areas:

### Left Sidebar (Device Panel)

Shows your connected Thermalright cooler(s). Click a device to select it. The blue highlighted device is the one currently being controlled.

Each device remembers its own settings (theme, brightness, rotation). Switching devices restores that device's configuration automatically.

- **Sensor** button: Opens a live system info display
- **About** button: Settings (LCD resolution, language, auto-start, temperature units). Auto-start is enabled automatically on first launch — it creates `~/.config/autostart/trcc.desktop` which runs `trcc --last-one` on login to send the last-used theme to your device without opening the GUI.

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

### "trcc: command not found" after reboot

**Cause:** `pip install` puts the `trcc` script in `~/.local/bin/`, which isn't in your shell's `PATH` on many distros. It may work right after install but disappear after a reboot.

**Fix:** Add `~/.local/bin` to your PATH permanently:
```bash
# Bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc

# Zsh (Arch, Garuda, some Manjaro)
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc

# Fish
fish_add_path ~/.local/bin
```

> **Note:** Fedora typically includes `~/.local/bin` in PATH automatically. Ubuntu/Debian add it conditionally in `~/.profile`, but only if the directory already exists at login time — so the first install may not take effect until a second reboot. Adding it to `~/.bashrc` avoids this race condition.

### "No compatible TRCC LCD device detected"

**Cause:** The LCD isn't showing up as a SCSI device.

**Fix:**
1. Make sure the USB cable is plugged into both the cooler and your computer
2. Run the udev setup if you haven't already: `trcc setup-udev`
3. Unplug and replug the USB cable (or reboot)
4. Check if the device appears: `ls /dev/sg*`
5. Check `dmesg | tail -20` right after plugging in to see kernel messages

### "Permission denied" when accessing the device

**Cause:** Udev rules not set up, or you need to reboot after setup.

**Fix:**
```bash
trcc setup-udev
# Then unplug/replug USB cable, or reboot
```

### "Error: PyQt6 not available"

**Cause:** PyQt6 isn't installed.

**Fix:** Install it for your distro (see Step 1), or:
```bash
pip install PyQt6
```

### No system tray icon on GNOME

**Cause:** GNOME removed built-in system tray support. Without it, TRCC can't show a tray icon.

**Impact:** The app will still work — closing the window quits normally instead of minimizing to tray.

**Fix (optional):** Install the AppIndicator extension to get tray support:
```bash
# Fedora
sudo dnf install gnome-shell-extension-appindicator

# Ubuntu / Debian
sudo apt install gnome-shell-extension-appindicator-support
```
Then enable it in the **Extensions** app and log out/in.

### "Qt_6_PRIVATE_API not found" when launching GUI

**Cause:** The pip-installed PyQt6 bundles its own Qt6 libraries, but your system's Qt6 (`/lib64/libQt6Core.so.6`) is being loaded instead. The version mismatch causes a symbol error. This is common on Fedora 42+ and other distros with newer system Qt6.

**Fix (recommended):** Use the system PyQt6 package instead of pip, so it matches the system Qt6:
```bash
# Fedora
sudo dnf install python3-pyqt6
pip uninstall PyQt6 PyQt6-Qt6 PyQt6-sip

# Ubuntu/Debian
sudo apt install python3-pyqt6
pip uninstall PyQt6 PyQt6-Qt6 PyQt6-sip

# Arch
sudo pacman -S python-pyqt6
pip uninstall PyQt6 PyQt6-Qt6 PyQt6-sip

# openSUSE
sudo zypper install python3-qt6
pip uninstall PyQt6 PyQt6-Qt6 PyQt6-sip
```

**Fix (alternative):** If the system package isn't available, force the pip PyQt6 to use its own bundled Qt6 libraries:
```bash
LD_LIBRARY_PATH=$(python3 -c "import PyQt6; print(PyQt6.__path__[0])")/Qt6/lib trcc gui
```

> **Why this happens:** pip's PyQt6 wheel is compiled against a specific Qt6 version. When your system has a different Qt6 version in `/lib64/`, the linker finds the system one first, causing the `Qt_6_PRIVATE_API` mismatch. The system `python3-pyqt6` package is built against the same Qt6, so they always match.

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
# Fedora / Nobara
sudo dnf install ffmpeg

# Ubuntu / Debian
sudo apt install ffmpeg

# Arch / CachyOS / Garuda
sudo pacman -S ffmpeg

# openSUSE
sudo zypper install ffmpeg

# Void
sudo xbps-install ffmpeg

# Gentoo
sudo emerge media-video/ffmpeg

# Alpine
sudo apk add ffmpeg

# NixOS — add to configuration.nix:
#   environment.systemPackages = [ pkgs.ffmpeg ];
```

### GUI looks wrong / elements overlapping

**Cause:** Display scaling (HiDPI) interfering with the fixed-size layout.

**Fix:** Try running with scaling disabled:
```bash
QT_AUTO_SCREEN_SCALE_FACTOR=0 trcc gui
```

### "externally-managed-environment" error during pip install

**Cause:** Your distro protects system Python packages (common on Fedora 38+, Ubuntu 23.04+, Debian 12+, Arch, openSUSE Tumbleweed).

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
# Fedora / Nobara
sudo dnf install python3-gobject python3-dbus pipewire-devel

# Ubuntu / Debian
sudo apt install python3-gi python3-dbus python3-gst-1.0

# Arch / CachyOS / Garuda
sudo pacman -S python-gobject python-dbus python-gst

# openSUSE
sudo zypper install python3-gobject python3-dbus-python python3-gstreamer
```

When you enable Screen Cast, a system dialog will pop up asking you to grant permission. This is normal — select the screen/monitor you want to capture and click "Share".

> **Note:** Screen cast works automatically on X11 and Wayland with wlroots-based compositors (Sway, Hyprland). The PipeWire portal is only needed for GNOME and KDE Wayland sessions.

### Device detected but nothing displays / sg_raw errors

**Cause:** The UAS (USB Attached SCSI) kernel driver interferes with these LCD devices.

**Fix:** The `trcc setup-udev` command should have created a USB quirk file. Verify it exists:
```bash
cat /etc/modprobe.d/trcc-lcd.conf
```

If it's missing, recreate it:
```bash
trcc setup-udev
# Unplug/replug USB cable, or reboot
```

If the problem persists, try manually blacklisting UAS for your device:
```bash
echo "options usb-storage quirks=87cd:70db:u,0416:5406:u,0402:3922:u" | sudo tee /etc/modprobe.d/trcc-lcd.conf
sudo update-initramfs -u  # Debian/Ubuntu
# or
sudo dracut --force       # Fedora/RHEL
```

### HID device detected but "No USB backend available"

**Cause:** Neither `pyusb` nor `hidapi` is installed. HID devices need one of these.

**Fix:**
```bash
# Install pyusb (preferred)
pip install pyusb
# Also need the system library:
sudo apt install libusb-1.0-0-dev    # Debian/Ubuntu
sudo dnf install libusb1-devel       # Fedora
sudo pacman -S libusb                # Arch

# Or install hidapi (alternative)
pip install hidapi
sudo apt install libhidapi-dev       # Debian/Ubuntu
sudo dnf install hidapi-devel        # Fedora
sudo pacman -S hidapi                # Arch
```

### HID device detected but "Permission denied" on USB

**Cause:** udev rules not set up for HID USB devices, or missing group membership.

**Fix:**
```bash
# Set up udev rules (covers both SCSI and HID)
trcc setup-udev
# Unplug and replug USB cable

# If that doesn't work, add the rule manually:
echo 'SUBSYSTEM=="usb", ATTR{idVendor}=="0416", ATTR{idProduct}=="5302", MODE="0660"' | sudo tee -a /etc/udev/rules.d/99-trcc-lcd.rules
echo 'SUBSYSTEM=="usb", ATTR{idVendor}=="0418", ATTR{idProduct}=="5303", MODE="0660"' | sudo tee -a /etc/udev/rules.d/99-trcc-lcd.rules
echo 'SUBSYSTEM=="usb", ATTR{idVendor}=="0418", ATTR{idProduct}=="5304", MODE="0660"' | sudo tee -a /etc/udev/rules.d/99-trcc-lcd.rules
sudo udevadm control --reload-rules
# Unplug and replug USB cable
```

### NixOS: "trcc setup-udev" doesn't work

**Cause:** NixOS manages udev rules declaratively. The `setup-udev` command can't write to `/etc/udev/rules.d/`.

**Fix:** Add the rules to your `configuration.nix` (see [NixOS section](#nixos)).

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

### Compositors and screen capture

| Compositor | Screen capture method | Notes |
|------------|----------------------|-------|
| GNOME (Mutter) | PipeWire portal | Needs `python3-gobject` + `python3-dbus` |
| KDE (KWin) | PipeWire portal | Needs `python3-gobject` + `python3-dbus` |
| Sway | `grim` / wlr-screencopy | Works out of the box with `grim` installed |
| Hyprland | `grim` / wlr-screencopy | Works out of the box with `grim` installed |
| Wayfire | `grim` / wlr-screencopy | Works out of the box with `grim` installed |
| River | `grim` / wlr-screencopy | Works out of the box with `grim` installed |
| X11 (any WM/DE) | Native X11 capture | Works everywhere, no extra deps |

Everything else (themes, overlays, video playback, device communication) works identically on both X11 and Wayland.

---

## Uninstalling

### Quick uninstall

```bash
# Remove config, autostart, desktop files
trcc uninstall

# Remove udev rules (requires root — run from the repo directory)
sudo PYTHONPATH=src python3 -m trcc.cli uninstall

# Remove the Python package
pip uninstall trcc-linux
```

### Manual removal (if trcc command is unavailable)

```bash
pip uninstall trcc-linux
sudo rm /etc/udev/rules.d/99-trcc-lcd.rules
sudo rm /etc/modprobe.d/trcc-lcd.conf
sudo udevadm control --reload-rules
rm -rf ~/.config/trcc
```

---

## Getting Help

- Check the [Troubleshooting](#troubleshooting) section above
- Look at the terminal output for error messages (run `trcc gui -v` for verbose output, or `trcc gui -vv` for debug output)
- File an issue at https://github.com/Lexonight1/thermalright-trcc-linux/issues
