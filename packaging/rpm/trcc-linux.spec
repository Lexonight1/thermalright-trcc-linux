%global pypi_name trcc-linux
%global pkg_name trcc_linux
%global srcname trcc-linux

Name:           trcc-linux
Version:        9.2.6
Release:        1%{?dist}
Summary:        Thermalright LCD/LED Control Center for Linux

License:        GPL-3.0-or-later
URL:            https://github.com/Lexonight1/thermalright-trcc-linux
Source0:        %{pypi_source %{srcname}}

BuildArch:      noarch
BuildRequires:  python3-devel
BuildRequires:  python3-hatchling
BuildRequires:  python3-pip
%if 0%{?fedora}
BuildRequires:  checkpolicy
%endif

Requires:       python3-pyside6 >= 6.5.0
Requires:       python3-numpy >= 1.24.0
Requires:       python3-psutil >= 5.9.0
Requires:       python3-pyusb >= 1.2.0
Requires:       python3-click >= 7.0
Requires:       python3-typer >= 0.9.0
Requires:       python3-fastapi >= 0.100
Requires:       python3-uvicorn >= 0.20
Requires:       python3-prompt-toolkit >= 3.0.0
Requires:       portaudio
Requires:       sg3_utils

# libusb is pulled in by python3-pyusb, but be explicit
%if 0%{?fedora}
Requires:       libusb1
%endif
%if 0%{?suse_version}
Requires:       libusb-1_0-0
%endif

# Optional deps
Recommends:     p7zip
Recommends:     ffmpeg
Recommends:     python3-pynvml
Recommends:     python3-dbus
Recommends:     python3-gobject
Recommends:     python3-hidapi

%description
Linux implementation of the Thermalright LCD Control Center (TRCC).
Controls LCD displays and LED segment displays on Thermalright CPU coolers
and AIO liquid coolers. Supports SCSI, HID, Bulk, and LY USB protocols.

Features:
- GUI (PySide6) with full Windows TRCC feature parity
- CLI (Typer) with 50+ commands
- REST API (FastAPI) with 49 endpoints
- Theme management (local + cloud)
- Video playback on LCD
- LED RGB effects and segment display control
- Overlay text/clock/sensor elements

%prep
%autosetup -n %{srcname}-%{version}

%build
%pyproject_wheel
%if 0%{?fedora}
# Build the SELinux policy module from source — loaded in %post via semodule.
checkmodule -M -m -o trcc_usb.mod src/trcc/data/trcc_usb.te
semodule_package -o trcc_usb.pp -m trcc_usb.mod
%endif

%install
%pyproject_install
%pyproject_save_files trcc

# System files
install -Dm644 packaging/udev/99-trcc-lcd.rules \
    %{buildroot}%{_udevrulesdir}/99-trcc-lcd.rules
install -Dm644 packaging/modprobe/trcc-lcd.conf \
    %{buildroot}%{_modprobedir}/trcc-lcd.conf
install -Dm644 packaging/modprobe/trcc-sg.conf \
    %{buildroot}%{_modulesloaddir}/trcc-sg.conf
install -Dm644 src/trcc/assets/trcc-linux.desktop \
    %{buildroot}%{_datadir}/applications/trcc-linux.desktop
for size in 256 128 64 48 32 24 16; do
    install -Dm644 "src/trcc/assets/icons/trcc_${size}x${size}.png" \
        "%{buildroot}%{_datadir}/icons/hicolor/${size}x${size}/apps/trcc.png"
done
install -Dm644 src/trcc/assets/com.github.lexonight1.trcc.policy \
    %{buildroot}%{_datadir}/polkit-1/actions/com.github.lexonight1.trcc.policy
install -Dm644 src/trcc/assets/trcc-quirk-fix.service \
    %{buildroot}%{_unitdir}/trcc-quirk-fix.service

# SELinux policy module (Fedora only) — built in %build, loaded in %post
%if 0%{?fedora}
install -Dm644 trcc_usb.pp \
    %{buildroot}%{_datadir}/selinux/packages/trcc_usb/trcc_usb.pp
%endif

%post
udevadm control --reload-rules 2>/dev/null || :
udevadm trigger 2>/dev/null || :
modprobe sg 2>/dev/null || :
%if 0%{?fedora}
if command -v semodule >/dev/null 2>&1 && selinuxenabled 2>/dev/null; then
    semodule -i %{_datadir}/selinux/packages/trcc_usb/trcc_usb.pp 2>/dev/null || :
fi
%endif
%systemd_post trcc-quirk-fix.service

%postun
udevadm control --reload-rules 2>/dev/null || :
%if 0%{?fedora}
if [ $1 -eq 0 ] && command -v semodule >/dev/null 2>&1 && selinuxenabled 2>/dev/null; then
    semodule -r trcc_usb 2>/dev/null || :
fi
%endif
%systemd_postun trcc-quirk-fix.service

%files -f %{pyproject_files}
%license LICENSE
%doc README.md
%{_bindir}/trcc
%{_bindir}/trcc-gui
%{_bindir}/trcc-lcd
%{_udevrulesdir}/99-trcc-lcd.rules
%{_modprobedir}/trcc-lcd.conf
%{_modulesloaddir}/trcc-sg.conf
%{_datadir}/applications/trcc-linux.desktop
%{_datadir}/icons/hicolor/*/apps/trcc.png
%{_datadir}/polkit-1/actions/com.github.lexonight1.trcc.policy
%{_unitdir}/trcc-quirk-fix.service
%if 0%{?fedora}
%{_datadir}/selinux/packages/trcc_usb/trcc_usb.pp
%endif

%changelog
* Sat Mar 28 2026 TRCC Linux Contributors <noreply@github.com> - 9.2.6-1
- ensure_all() now truly idempotent — always ensures all archives on startup
- Non-square devices get both orientations extracted so rotation works immediately
- See https://github.com/Lexonight1/thermalright-trcc-linux/releases

* Sat Mar 28 2026 TRCC Linux Contributors <noreply@github.com> - 9.2.5-1
- Fix cloud mask download for portrait/rotated devices (issue #95)
- Expand CLOUD_MASK_URLS to all supported resolutions including billboard devices
- ensure_all() now extracts web+mask data for both orientations on non-square devices
- See https://github.com/Lexonight1/thermalright-trcc-linux/releases

* Mon Mar 17 2026 TRCC Linux Contributors <noreply@github.com> - 8.8.1-1
- Fix CLI video/test/screencast renderer initialization
- Fix PIL-to-QImage conversion in encode_for_device
- Show DRM GPU sensors in sensor picker for multi-GPU systems
- 4-platform support (Linux, Windows, macOS, FreeBSD)
- 5269 tests, 49 API endpoints
- See https://github.com/Lexonight1/thermalright-trcc-linux/releases

* Fri Mar 07 2026 TRCC Linux Contributors <noreply@github.com> - 8.1.3-1
- Strict DI across all services — full hexagonal SOLID purity
- CPU optimization: 48%% to 6%% with MP4 playback
- DeviceProfile table replaces scattered encoding logic
- Hexagonal test restructuring (9 directories mirroring src/)
- 4021 tests, 50 CLI commands, 43 API endpoints
- See https://github.com/Lexonight1/thermalright-trcc-linux/releases

* Sat Mar 01 2026 TRCC Linux Contributors <noreply@github.com> - 6.5.2-1
- IPC daemon: GUI-as-server for single-device-owner safety
- Fix video background black screen on custom theme reload
- CodeQL security fix (URL substring sanitization)
- Test suite: 4440 tests, 76%% coverage
- See https://github.com/Lexonight1/thermalright-trcc-linux/releases

* Fri Feb 28 2026 TRCC Linux Contributors <noreply@github.com> - 6.3.3-1
- Single-instance window raise via SIGUSR1
- PM-based device button image resolution
- See https://github.com/Lexonight1/thermalright-trcc-linux/releases
