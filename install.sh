#!/usr/bin/env bash
# TRCC Linux — Auto-install / uninstall script
# Detects distro, installs system packages, pip deps, udev rules.
# Usage:
#   sudo ./install.sh              # install
#   sudo ./install.sh --uninstall  # uninstall
#   ./install.sh --help

# Re-exec under bash if invoked via 'sh install.sh' (dash doesn't support pipefail)
if [ -z "${BASH_VERSION:-}" ]; then
    exec bash "$0" "$@"
fi
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TRCC_VERSION="$(grep '^__version__ =' "$SCRIPT_DIR/src/trcc/__version__.py" 2>/dev/null | sed 's/.*"\(.*\)"/\1/' || echo "unknown")"

# Paths
UDEV_RULES="/etc/udev/rules.d/99-trcc-lcd.rules"
MODPROBE_CONF="/etc/modprobe.d/trcc-lcd.conf"
VENV_DIR=""  # set per-user below

# Resolve real user when running under sudo
REAL_USER="${SUDO_USER:-$USER}"
REAL_HOME="$(getent passwd "$REAL_USER" | cut -d: -f6)"
DESKTOP_FILE="$REAL_HOME/.local/share/applications/trcc.desktop"
AUTOSTART_FILE="$REAL_HOME/.config/autostart/trcc.desktop"
CONFIG_DIR="$REAL_HOME/.config/trcc"
LEGACY_CONFIG_DIR="$REAL_HOME/.trcc"
VENV_DIR="$REAL_HOME/trcc-env"

# Distro detection results
DISTRO_ID=""
DISTRO_ID_LIKE=""
PKG_MANAGER=""
IS_IMMUTABLE=false
USE_VENV=false

# ── Colors ───────────────────────────────────────────────────────────────────

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
    RED='\033[0;31m'
    GREEN='\033[0;32m'
    YELLOW='\033[0;33m'
    BOLD='\033[1m'
    RESET='\033[0m'
else
    RED='' GREEN='' YELLOW='' BOLD='' RESET=''
fi

info()    { echo -e "${GREEN}[TRCC]${RESET} $*"; }
warn()    { echo -e "${YELLOW}[WARN]${RESET} $*"; }
error()   { echo -e "${RED}[ERROR]${RESET} $*" >&2; }
step()    { echo -e "\n${BOLD}==> $1: $2${RESET}"; }

ask_yn() {
    local prompt="$1" default="${2:-n}"
    if [ "$default" = "y" ]; then
        prompt="$prompt [Y/n] "
    else
        prompt="$prompt [y/N] "
    fi
    read -rp "$prompt" answer
    answer="${answer:-$default}"
    [[ "$answer" =~ ^[Yy] ]]
}

# ── Checks ───────────────────────────────────────────────────────────────────

check_bash_version() {
    if [ "${BASH_VERSINFO[0]}" -lt 4 ]; then
        error "Bash 4+ required (you have $BASH_VERSION)"
        exit 1
    fi
}

check_python() {
    if ! command -v python3 &>/dev/null; then
        error "Python 3 not found. Install it with your package manager first."
        exit 1
    fi
    local py_ver
    py_ver="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
    local major minor
    major="${py_ver%%.*}"
    minor="${py_ver#*.}"
    if [ "$major" -lt 3 ] || { [ "$major" -eq 3 ] && [ "$minor" -lt 9 ]; }; then
        error "Python 3.9+ required (found $py_ver)"
        exit 1
    fi
    info "Python $py_ver"
}

check_repo_root() {
    if [ ! -f "$SCRIPT_DIR/pyproject.toml" ] || [ ! -d "$SCRIPT_DIR/src/trcc" ]; then
        error "Run this script from the TRCC Linux repository root."
        exit 1
    fi
}

check_root() {
    if [ "$(id -u)" -ne 0 ]; then
        error "Root required for package install and udev setup."
        echo "  Run: sudo $0"
        exit 1
    fi
}

# ── Distro Detection ────────────────────────────────────────────────────────

detect_distro() {
    if [ ! -f /etc/os-release ]; then
        error "Cannot detect distro: /etc/os-release not found."
        error "Install manually — see doc/INSTALL_GUIDE.md"
        exit 1
    fi

    # shellcheck source=/dev/null
    . /etc/os-release
    DISTRO_ID="${ID:-unknown}"
    DISTRO_ID_LIKE="${ID_LIKE:-}"
    local variant="${VARIANT_ID:-}"

    # 1. Check immutable distros first
    if [ "$DISTRO_ID" = "bazzite" ]; then
        PKG_MANAGER="rpm-ostree"
        IS_IMMUTABLE=true
        USE_VENV=true
        return
    fi
    if [ "$DISTRO_ID" = "fedora" ] && command -v rpm-ostree &>/dev/null; then
        case "$variant" in
            silverblue|kinoite|sway-atomic|budgie-atomic|onyx)
                PKG_MANAGER="rpm-ostree"
                IS_IMMUTABLE=true
                USE_VENV=true
                return
                ;;
        esac
    fi
    if [ "$DISTRO_ID" = "steamos" ]; then
        PKG_MANAGER="pacman-steamos"
        IS_IMMUTABLE=true
        return
    fi

    # 2. Match exact distro ID
    case "$DISTRO_ID" in
        fedora|nobara|rhel|centos|rocky|alma)
            PKG_MANAGER="dnf" ;;
        ubuntu|debian|linuxmint|pop|zorin|elementary|kali|neon|raspbian)
            PKG_MANAGER="apt" ;;
        arch|manjaro|endeavouros|cachyos|garuda|artix|arcolinux)
            PKG_MANAGER="pacman" ;;
        opensuse-tumbleweed|opensuse-leap)
            PKG_MANAGER="zypper" ;;
        void)
            PKG_MANAGER="xbps" ;;
        gentoo|funtoo|calculate)
            PKG_MANAGER="emerge" ;;
        alpine|postmarketos)
            PKG_MANAGER="apk" ;;
        solus)
            PKG_MANAGER="eopkg" ;;
        clear-linux-os)
            PKG_MANAGER="swupd" ;;
        nixos)
            warn "NixOS detected. This script cannot manage declarative packages."
            warn "Follow the NixOS section in doc/INSTALL_GUIDE.md instead."
            exit 0
            ;;
    esac

    # 3. Fall back to ID_LIKE
    if [ -z "$PKG_MANAGER" ]; then
        case "$DISTRO_ID_LIKE" in
            *fedora*)       PKG_MANAGER="dnf" ;;
            *debian*|*ubuntu*) PKG_MANAGER="apt" ;;
            *arch*)         PKG_MANAGER="pacman" ;;
            *suse*)         PKG_MANAGER="zypper" ;;
        esac
    fi

    # 4. Still unknown
    if [ -z "$PKG_MANAGER" ]; then
        error "Unsupported distro: $DISTRO_ID (ID_LIKE: $DISTRO_ID_LIKE)"
        error "Please install manually — see doc/INSTALL_GUIDE.md"
        error "Or open an issue: https://github.com/Lexonight1/thermalright-trcc-linux/issues"
        exit 1
    fi
}

# ── Package Install ─────────────────────────────────────────────────────────

install_system_packages() {
    info "Package manager: $PKG_MANAGER"

    case "$PKG_MANAGER" in
        dnf)
            dnf install -y sg3_utils python3-pyqt6 ffmpeg python3-pip
            ;;
        apt)
            apt update -y
            apt install -y sg3-utils python3-pyqt6 ffmpeg python3-pip python3-venv
            ;;
        pacman)
            pacman -S --noconfirm --needed sg3_utils python-pyqt6 ffmpeg python-pip
            ;;
        zypper)
            zypper install -y sg3_utils python3-qt6 ffmpeg python3-pip
            ;;
        xbps)
            xbps-install -y sg3_utils python3-PyQt6 ffmpeg python3-pip
            ;;
        emerge)
            emerge --ask sg3_utils dev-python/PyQt6 media-video/ffmpeg dev-python/pip
            ;;
        apk)
            apk add sg3_utils py3-pyqt6 ffmpeg py3-pip python3
            ;;
        eopkg)
            eopkg install -y sg3_utils python3-pip ffmpeg
            ;;
        swupd)
            swupd bundle-add python3-basic devpkg-sg3_utils ffmpeg
            ;;
        rpm-ostree)
            if rpm -q sg3_utils &>/dev/null; then
                info "sg3_utils already installed"
            else
                rpm-ostree install sg3_utils
                warn "A reboot is required for sg3_utils to become available."
                warn "After reboot, re-run: sudo $0"
                exit 0
            fi
            USE_VENV=true
            ;;
        pacman-steamos)
            steamos-readonly disable
            trap 'steamos-readonly enable' EXIT
            pacman -S --noconfirm --needed sg3_utils python-pip python-pyqt6 ffmpeg
            steamos-readonly enable
            trap - EXIT
            ;;
        *)
            error "No install handler for: $PKG_MANAGER"
            exit 1
            ;;
    esac

    info "System packages installed."
}

# ── Python Install ──────────────────────────────────────────────────────────

install_python_packages() {
    if [ "$USE_VENV" = true ]; then
        install_python_venv
        return
    fi

    # Try direct pip install first
    info "Installing TRCC via pip..."
    if sudo -u "$REAL_USER" pip install --break-system-packages -e "$SCRIPT_DIR" 2>/dev/null; then
        info "pip install succeeded."
    else
        warn "pip refused direct install — using virtual environment instead."
        USE_VENV=true
        install_python_venv
        return
    fi

    check_trcc_on_path
}

install_python_venv() {
    info "Setting up virtual environment at $VENV_DIR..."

    if [ -d "$VENV_DIR" ]; then
        if ask_yn "Virtual environment already exists at $VENV_DIR. Recreate it?"; then
            rm -rf "$VENV_DIR"
        fi
    fi

    if [ ! -d "$VENV_DIR" ]; then
        sudo -u "$REAL_USER" python3 -m venv "$VENV_DIR"
    fi

    sudo -u "$REAL_USER" "$VENV_DIR/bin/pip" install -e "$SCRIPT_DIR"
    info "Installed in venv: $VENV_DIR"
    info "Launch with: source $VENV_DIR/bin/activate && trcc gui"
}

check_trcc_on_path() {
    if command -v trcc &>/dev/null; then
        info "trcc $(trcc --version 2>/dev/null || echo '') is ready."
        return
    fi

    # Check common pip install locations
    local pip_bin="$REAL_HOME/.local/bin"
    if [ -f "$pip_bin/trcc" ]; then
        warn "'trcc' installed to $pip_bin but it's not on your PATH."
        warn "Add it with:"
        warn "  echo 'export PATH=\"\$PATH:\$HOME/.local/bin\"' >> ~/.bashrc"
        warn "  source ~/.bashrc"
    fi
}

# ── Udev Setup ──────────────────────────────────────────────────────────────

setup_udev() {
    local trcc_cmd=""
    if command -v trcc &>/dev/null; then
        trcc_cmd="trcc"
    elif [ -f "$VENV_DIR/bin/trcc" ]; then
        trcc_cmd="$VENV_DIR/bin/trcc"
    else
        trcc_cmd="python3 -m trcc.cli"
    fi

    info "Running: $trcc_cmd setup-udev"
    if [ "$trcc_cmd" = "python3 -m trcc.cli" ]; then
        PYTHONPATH="$SCRIPT_DIR/src" $trcc_cmd setup-udev
    else
        $trcc_cmd setup-udev
    fi
}

# ── Desktop Shortcut ────────────────────────────────────────────────────────

create_desktop_shortcut() {
    if ask_yn "Create desktop shortcut (app menu entry)?"; then
        local desktop_dir
        desktop_dir="$(dirname "$DESKTOP_FILE")"
        sudo -u "$REAL_USER" mkdir -p "$desktop_dir"

        local exec_cmd="trcc gui"
        if [ "$USE_VENV" = true ]; then
            exec_cmd="bash -c 'source $VENV_DIR/bin/activate && trcc gui'"
        fi

        # Install icon to hicolor theme
        local icon_src="$SCRIPT_DIR/src/trcc/assets/icons/trcc_256x256.png"
        local icon_name="preferences-desktop-display"
        if [ -f "$icon_src" ]; then
            local icon_dir="$REAL_HOME/.local/share/icons/hicolor/256x256/apps"
            sudo -u "$REAL_USER" mkdir -p "$icon_dir"
            sudo -u "$REAL_USER" cp "$icon_src" "$icon_dir/trcc.png"
            for size in 48 64 128; do
                local small_src="$SCRIPT_DIR/src/trcc/assets/icons/trcc_${size}x${size}.png"
                if [ -f "$small_src" ]; then
                    local small_dir="$REAL_HOME/.local/share/icons/hicolor/${size}x${size}/apps"
                    sudo -u "$REAL_USER" mkdir -p "$small_dir"
                    sudo -u "$REAL_USER" cp "$small_src" "$small_dir/trcc.png"
                fi
            done
            gtk-update-icon-cache "$REAL_HOME/.local/share/icons/hicolor" 2>/dev/null || true
            icon_name="trcc"
            info "Installed TRCC icon"
        fi

        sudo -u "$REAL_USER" tee "$DESKTOP_FILE" > /dev/null << EOF
[Desktop Entry]
Name=TRCC Linux
Comment=Thermalright LCD Control Center
Exec=$exec_cmd
Icon=$icon_name
Terminal=false
Type=Application
Categories=Utility;System;
Keywords=thermalright;lcd;cooler;
EOF
        info "Created $DESKTOP_FILE"
    fi
}

# ── Install Orchestrator ────────────────────────────────────────────────────

do_install() {
    echo -e "${BOLD}TRCC Linux Installer v${TRCC_VERSION}${RESET}"
    echo ""

    check_bash_version
    check_repo_root
    check_root

    step "1/5" "Checking Python..."
    check_python

    step "2/5" "Detecting distribution..."
    detect_distro
    info "Detected: $DISTRO_ID (package manager: $PKG_MANAGER)"
    if [ "$IS_IMMUTABLE" = true ]; then
        info "Immutable distro — will use virtual environment."
    fi

    step "3/5" "Installing system packages..."
    install_system_packages

    step "4/5" "Installing TRCC Python package..."
    install_python_packages

    step "5/5" "Setting up device permissions (udev)..."
    setup_udev

    echo ""
    create_desktop_shortcut
    print_success
}

print_success() {
    echo ""
    echo -e "${GREEN}${BOLD}=== TRCC Linux v${TRCC_VERSION} installed ===${RESET}"
    echo ""
    echo "Next steps:"
    echo "  1. Unplug and replug the USB cable (or reboot)"
    if [ "$USE_VENV" = true ]; then
        echo "  2. source $VENV_DIR/bin/activate"
        echo "  3. trcc gui"
    else
        echo "  2. trcc gui"
    fi
    echo ""
    echo "Troubleshooting:"
    echo "  trcc detect       # check if device is found"
    echo "  trcc detect --all # show all devices"
    echo "  trcc test         # color cycle test"
    echo ""
    echo "Full guide: doc/INSTALL_GUIDE.md"
}

# ── Uninstall ───────────────────────────────────────────────────────────────

do_uninstall() {
    echo -e "${BOLD}TRCC Linux Uninstaller${RESET}"
    echo ""

    check_bash_version
    detect_distro

    local removed=0

    # 1. pip uninstall
    info "Removing TRCC Python package..."
    if pip uninstall -y trcc-linux 2>/dev/null; then
        removed=1
    fi
    # Also try as real user
    if sudo -u "$REAL_USER" pip uninstall -y trcc-linux 2>/dev/null; then
        removed=1
    fi
    # Venv
    if [ -f "$VENV_DIR/bin/pip" ]; then
        "$VENV_DIR/bin/pip" uninstall -y trcc-linux 2>/dev/null || true
        rm -rf "$VENV_DIR"
        info "Removed venv: $VENV_DIR"
        removed=1
    fi

    # 2. System files (need root)
    if [ "$(id -u)" -eq 0 ]; then
        for f in "$UDEV_RULES" "$MODPROBE_CONF"; do
            if [ -f "$f" ]; then
                rm -f "$f"
                info "Removed $f"
                removed=1
            fi
        done
        # Reload udev
        if command -v udevadm &>/dev/null; then
            udevadm control --reload-rules 2>/dev/null || true
            udevadm trigger 2>/dev/null || true
        fi
    else
        for f in "$UDEV_RULES" "$MODPROBE_CONF"; do
            if [ -f "$f" ]; then
                warn "Skipped $f (run with sudo to remove)"
            fi
        done
    fi

    # 3. User files
    for dir in "$CONFIG_DIR" "$LEGACY_CONFIG_DIR"; do
        if [ -d "$dir" ]; then
            rm -rf "$dir"
            info "Removed $dir"
            removed=1
        fi
    done
    for f in "$AUTOSTART_FILE" "$DESKTOP_FILE"; do
        if [ -f "$f" ]; then
            rm -f "$f"
            info "Removed $f"
            removed=1
        fi
    done

    # 4. Optionally remove system packages
    if [ "$(id -u)" -eq 0 ] && ask_yn "Remove system packages (sg3_utils, ffmpeg, etc.)? This may affect other programs." "n"; then
        uninstall_system_packages
    fi

    echo ""
    if [ "$removed" -eq 1 ]; then
        echo -e "${GREEN}${BOLD}TRCC Linux has been uninstalled.${RESET}"
    else
        info "Nothing to remove — TRCC is already clean."
    fi
}

uninstall_system_packages() {
    case "$PKG_MANAGER" in
        dnf)
            dnf remove -y sg3_utils python3-pyqt6 ffmpeg || true ;;
        apt)
            apt remove -y sg3-utils python3-pyqt6 ffmpeg || true
            apt autoremove -y || true ;;
        pacman|pacman-steamos)
            if [ "$PKG_MANAGER" = "pacman-steamos" ]; then
                steamos-readonly disable
                trap 'steamos-readonly enable' EXIT
            fi
            pacman -Rs --noconfirm sg3_utils python-pyqt6 ffmpeg || true
            if [ "$PKG_MANAGER" = "pacman-steamos" ]; then
                steamos-readonly enable
                trap - EXIT
            fi
            ;;
        zypper)
            zypper remove -y sg3_utils python3-qt6 ffmpeg || true ;;
        xbps)
            xbps-remove -y sg3_utils python3-PyQt6 ffmpeg || true ;;
        emerge)
            emerge --deselect sg3_utils dev-python/PyQt6 media-video/ffmpeg || true ;;
        apk)
            apk del sg3_utils py3-pyqt6 ffmpeg || true ;;
        eopkg)
            eopkg remove -y sg3_utils ffmpeg || true ;;
        rpm-ostree)
            rpm-ostree uninstall sg3_utils || true
            warn "Reboot required to complete package removal."
            ;;
    esac
}

# ── Help ────────────────────────────────────────────────────────────────────

print_usage() {
    cat << EOF
TRCC Linux Installer v${TRCC_VERSION}

Usage:
  sudo ./install.sh              Install TRCC Linux
  sudo ./install.sh --uninstall  Remove TRCC Linux
  ./install.sh --help            Show this help

The installer auto-detects your Linux distribution and installs
the correct system packages, Python dependencies, and udev rules.

Supported distros:
  Fedora, Ubuntu, Debian, Arch, Manjaro, openSUSE, Void, Gentoo,
  Alpine, Nobara, Solus, Clear Linux, Bazzite, SteamOS, and more.

For NixOS or manual install, see doc/INSTALL_GUIDE.md
EOF
}

# ── Main ────────────────────────────────────────────────────────────────────

main() {
    case "${1:-}" in
        --uninstall)
            do_uninstall
            ;;
        --help|-h)
            print_usage
            ;;
        "")
            do_install
            ;;
        *)
            error "Unknown argument: $1"
            print_usage
            exit 1
            ;;
    esac
}

main "$@"
