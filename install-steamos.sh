#!/usr/bin/env bash
# TRCC Linux on SteamOS 3 -- installed in your home folder, so SteamOS
# updates keep it.
#
# A SteamOS update replaces the whole system with Valve's new image, so
# anything pacman installed is gone afterwards.  Your home folder is not part
# of that image.  This puts TRCC in a Python venv there and lets
# `trcc system setup` add the device rules (kept through updates by SteamOS's
# keep list), the menu entry and autostart.  SteamOS already ships what TRCC
# needs beside it: Python, 7-Zip, ffmpeg, PortAudio, libusb.
#
# Usage, as your own user in Konsole (Desktop Mode):
#   curl -fsSL https://raw.githubusercontent.com/Lexonight1/thermalright-trcc-linux/main/install-steamos.sh | bash
# Run it again to update.

if [ -z "${BASH_VERSION:-}" ]; then
    exec bash "$0" "$@"
fi
set -euo pipefail

VENV="$HOME/.local/share/trcc-linux"
BIN="$HOME/.local/bin"
GUIDE="https://github.com/Lexonight1/thermalright-trcc-linux/blob/main/doc/GUIDE_INSTALL.md"

info() { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mWARNING:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -ne 0 ] || die "Run this as your own user, not with sudo -- it asks for the password itself when it needs it."
grep -qx 'ID=steamos' /etc/os-release 2>/dev/null \
    || die "This script is for SteamOS 3.  For other systems see $GUIDE"
python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' \
    || die "TRCC needs Python 3.10 or newer; this SteamOS has $(python3 --version 2>&1)."

if [ -x "$VENV/bin/python" ]; then
    info "Updating TRCC in $VENV"
else
    info "Creating a Python environment in $VENV"
    python3 -m venv "$VENV"
fi
"$VENV/bin/python" -m pip install --upgrade --quiet trcc-linux
info "Installed $("$VENV/bin/trcc" --version 2>/dev/null || echo trcc-linux)"

mkdir -p "$BIN"
ln -sf "$VENV/bin/trcc" "$BIN/trcc"

command -v 7z >/dev/null || command -v 7zz >/dev/null \
    || warn "7-Zip is not on this SteamOS -- the theme browser stays empty without it."
command -v ffmpeg >/dev/null \
    || warn "ffmpeg is not on this SteamOS -- videos will not play without it."

info "Setting up device access, the menu entry and autostart."
info "This asks for your password.  Never set one?  Press Ctrl-C, run 'passwd', then run this again."
"$VENV/bin/trcc" system setup \
    || warn "Setup reported a problem above.  TRCC is installed; run '$BIN/trcc system setup' again after fixing it."

info "Done.  Open TRCC from the application menu."
case ":$PATH:" in
    *":$BIN:"*) info "In a terminal: trcc gui" ;;
    *)          info "In a terminal: $BIN/trcc gui" ;;
esac
