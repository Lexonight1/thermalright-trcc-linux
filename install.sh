#!/usr/bin/env bash
# TRCC Linux — Auto-install / uninstall script
# Bootstraps pip, installs trcc-linux, delegates to `trcc setup` for deps.
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

# Resolve real user when running under sudo
REAL_USER="${SUDO_USER:-$USER}"
REAL_HOME="$(getent passwd "$REAL_USER" | cut -d: -f6)"
DESKTOP_FILE="$REAL_HOME/.local/share/applications/trcc.desktop"
AUTOSTART_FILE="$REAL_HOME/.config/autostart/trcc.desktop"
CONFIG_DIR="$REAL_HOME/.trcc"
USER_CONTENT_DIR="$REAL_HOME/.trcc-user"
LEGACY_CONFIG_DIR="$REAL_HOME/.config/trcc"
VENV_DIR="$REAL_HOME/trcc-env"

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
        error "Root required for system setup."
        echo "  Run: sudo $0"
        exit 1
    fi
}

# ── Immutable Distro Detection ─────────────────────────────────────────────
# Only needed to decide pip vs venv. All dep install is handled by `trcc setup`.

detect_immutable() {
    if [ ! -f /etc/os-release ]; then
        return
    fi

    # shellcheck source=/dev/null
    . /etc/os-release
    local distro_id="${ID:-unknown}"
    local variant="${VARIANT_ID:-}"

    if [ "$distro_id" = "bazzite" ]; then
        USE_VENV=true
        return
    fi
    if [ "$distro_id" = "fedora" ] && command -v rpm-ostree &>/dev/null; then
        case "$variant" in
            silverblue|kinoite|sway-atomic|budgie-atomic|onyx)
                USE_VENV=true
                return
                ;;
        esac
    fi
    if [ "$distro_id" = "steamos" ]; then
        USE_VENV=true
        return
    fi
    if [ "$distro_id" = "nixos" ]; then
        warn "NixOS detected. This script cannot manage declarative packages."
        warn "Follow the NixOS section in doc/GUIDE_INSTALL.md instead."
        exit 0
    fi
}

# ── Ensure pip ─────────────────────────────────────────────────────────────

ensure_pip() {
    if python3 -m pip --version &>/dev/null; then
        info "pip available"
        return
    fi

    warn "pip not found — attempting to install..."

    # Try ensurepip first (works on most distros)
    if python3 -m ensurepip --default-pip 2>/dev/null; then
        info "pip installed via ensurepip"
        return
    fi

    # Fall back to package manager
    if command -v dnf &>/dev/null; then
        dnf install -y python3-pip
    elif command -v apt &>/dev/null; then
        apt update -y && apt install -y python3-pip python3-venv
    elif command -v pacman &>/dev/null; then
        pacman -S --noconfirm --needed python-pip
    elif command -v zypper &>/dev/null; then
        zypper install -y python3-pip
    elif command -v xbps-install &>/dev/null; then
        xbps-install -y python3-pip
    elif command -v apk &>/dev/null; then
        apk add py3-pip
    elif command -v eopkg &>/dev/null; then
        eopkg install -y python3-pip
    else
        error "Cannot install pip automatically. Install python3-pip manually."
        exit 1
    fi

    if ! python3 -m pip --version &>/dev/null; then
        error "pip still not available after install attempt."
        exit 1
    fi
    info "pip installed"
}

# ── Python Install ──────────────────────────────────────────────────────────

install_trcc() {
    if [ "$USE_VENV" = true ]; then
        install_trcc_venv
        return
    fi

    info "Installing TRCC via pip..."
    if sudo -u "$REAL_USER" pip install --break-system-packages -e "$SCRIPT_DIR" 2>/dev/null; then
        info "pip install succeeded."
    else
        warn "pip refused direct install — using virtual environment instead."
        USE_VENV=true
        install_trcc_venv
        return
    fi

    check_trcc_on_path
}

install_trcc_venv() {
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
}

check_trcc_on_path() {
    if command -v trcc &>/dev/null; then
        info "trcc $(trcc --version 2>/dev/null || echo '') is ready."
        return
    fi

    local pip_bin="$REAL_HOME/.local/bin"
    if [ -f "$pip_bin/trcc" ]; then
        warn "'trcc' installed to $pip_bin but it's not on your PATH."
        warn "Add it with:"
        warn "  echo 'export PATH=\"\$PATH:\$HOME/.local/bin\"' >> ~/.bashrc"
        warn "  source ~/.bashrc"
    fi
}

# ── Resolve trcc command ───────────────────────────────────────────────────

find_trcc_cmd() {
    if command -v trcc &>/dev/null; then
        echo "trcc"
    elif [ -f "$VENV_DIR/bin/trcc" ]; then
        echo "$VENV_DIR/bin/trcc"
    elif [ -f "$REAL_HOME/.local/bin/trcc" ]; then
        echo "$REAL_HOME/.local/bin/trcc"
    else
        echo "PYTHONPATH=$SCRIPT_DIR/src python3 -m trcc.cli"
    fi
}

# ── Install Orchestrator ──────────────────────────────────────────────────

do_install() {
    echo -e "${BOLD}TRCC Linux Installer v${TRCC_VERSION}${RESET}"
    echo ""

    check_bash_version
    check_repo_root
    check_root

    step "1/3" "Checking Python & pip..."
    check_python
    detect_immutable
    if [ "$USE_VENV" = true ]; then
        info "Immutable distro — will use virtual environment."
    fi
    ensure_pip

    step "2/3" "Installing TRCC Python package..."
    install_trcc

    step "3/3" "Running setup wizard (deps, udev, desktop entry)..."
    local trcc_cmd
    trcc_cmd="$(find_trcc_cmd)"
    info "Running: $trcc_cmd setup --yes"
    # trcc setup handles: system deps, GPU drivers, udev, SELinux, desktop entry
    # IMPORTANT: run as the real user so the user's site-packages/venv are visible.
    # (If run as root, ~/.local installs won't be importable and `trcc` will crash.)
    #
    # Use env PATH so ~/.local/bin is visible even if the user's shell rc wasn't loaded.
    if [[ "$trcc_cmd" == PYTHONPATH=* ]]; then
        sudo -u "$REAL_USER" -H bash -lc "PATH=\"$REAL_HOME/.local/bin:$VENV_DIR/bin:\$PATH\" $trcc_cmd setup --yes"
    else
        sudo -u "$REAL_USER" -H env PATH="$REAL_HOME/.local/bin:$VENV_DIR/bin:$PATH" "$trcc_cmd" setup --yes
    fi

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
    echo "Full guide: doc/GUIDE_INSTALL.md"
}

# ── Uninstall ──────────────────────────────────────────────────────────────

do_uninstall() {
    echo -e "${BOLD}TRCC Linux Uninstaller${RESET}"
    echo ""

    check_bash_version

    local removed=0

    # 1. pip uninstall
    info "Removing TRCC Python package..."
    if pip uninstall -y trcc-linux 2>/dev/null; then
        removed=1
    fi
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
    for dir in "$CONFIG_DIR" "$USER_CONTENT_DIR" "$LEGACY_CONFIG_DIR"; do
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

    echo ""
    if [ "$removed" -eq 1 ]; then
        echo -e "${GREEN}${BOLD}TRCC Linux has been uninstalled.${RESET}"
    else
        info "Nothing to remove — TRCC is already clean."
    fi
}

# ── Help ─────────────────────────────────────────────────────────────────────

print_usage() {
    cat << EOF
TRCC Linux Installer v${TRCC_VERSION}

Usage:
  sudo ./install.sh              Install TRCC Linux
  sudo ./install.sh --uninstall  Remove TRCC Linux
  ./install.sh --help            Show this help

The installer bootstraps pip, installs trcc-linux, then delegates to
'trcc setup' for system dependencies, udev rules, and desktop integration.

Supported distros:
  Fedora, Ubuntu, Debian, Arch, Manjaro, openSUSE, Void, Gentoo,
  Alpine, Nobara, Solus, Clear Linux, Bazzite, SteamOS, and more.

For NixOS or manual install, see doc/GUIDE_INSTALL.md
EOF
}

# ── Main ─────────────────────────────────────────────────────────────────────

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
