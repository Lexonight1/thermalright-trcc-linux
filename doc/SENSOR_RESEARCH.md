# Cross-Platform Sensor & Device Research — Fail-Proof Reference

**Status:** in-progress research. **No code based on this until every section has citations and every claim has a "Failure modes" line.** Goal is to make sure the canonical-pattern lesson (`feedback_canonical_pattern_first.md`) holds *before* any source is written, not after.

This doc is the standing reference for what each supported OS can actually expose, by what API, with what permissions, and what's known to fail. When in doubt, cite this doc and link the upstream source.

## Method

For each OS × metric domain pair, three things must be true before "green":

1. **Canonical API named** — the documented, vendor- or upstream-blessed path. Not a workaround.
2. **Permissions explicit** — none / user / sudo / admin / kernel driver — and CVE status if applicable.
3. **Failure modes documented** — what hardware doesn't expose it, what returns garbage, what changed in recent OS versions.

Each claim has ≥2 citations (one canonical doc, one shipping consumer).

## Scope

OSes covered: Linux, Windows, macOS (Intel + Apple Silicon), FreeBSD, OpenBSD.

Metric domains:
- CPU temperature
- GPU temperature (per vendor: NVIDIA, AMD, Intel)
- Fan + voltage
- Power consumption (CPU package + GPU + system)
- USB device discovery & handshake

Plus cross-cutting:
- Python ecosystem maintenance (pyusb, hidapi, libusb, pynvml)
- Driver signing / OS sandboxing changes that affect us
- Known CVEs / banned drivers / upcoming deprecations

## Verdict legend

- ✅ **Green** — canonical API, predictable permissions, no upcoming deprecation, citation confidence high
- ⚠️ **Yellow** — works but with a caveat (per-vendor SDK, hardware-dependent, requires opt-in install)
- ❌ **Red** — no clean path exists today; would require a code change we *can't* ship (e.g. unsigned kernel driver)

---

## Linux

### CPU temperature
**hwmon subsystem** is canonical. `coretemp` driver for Intel, `k10temp`/`zenpower3` for AMD Ryzen/EPYC. Read via `/sys/class/hwmon/hwmonN/tempN_input` (millidegrees Celsius).

- [hwmon sysfs interface (kernel.org)](https://docs.kernel.org/hwmon/sysfs-interface.html)
- [lm_sensors ArchWiki](https://wiki.archlinux.org/title/Lm_sensors)
- [HWMON Subsystem (DeepWiki)](https://deepwiki.com/linux-doc/linux/5.1-hwmon-subsystem)

We already use this via [linux_sensors.py](src/trcc/adapters/system/linux_sensors.py).

### GPU temperature
- **NVIDIA:** `pynvml` (NVIDIA Management Library). Stable, vendor-supported.
- **AMD:** hwmon via `amdgpu` driver — `/sys/class/drm/cardN/device/hwmon/hwmonN/temp1_input`
- **Intel:** hwmon via `i915` driver (Iris/Arc) — same pattern

### Fan + voltage
hwmon `fanN_input` (RPM) + `inN_input` (voltage). Driver-dependent — `nct6779`, `it87`, `lm75` and friends cover most consumer motherboards.

### Power consumption
**RAPL** via `/sys/class/powercap/intel-rapl:N/energy_uj`.

🚨 **Permission gotcha (Linux 5.10+):** RAPL files restricted to root since kernel 5.10 (security: [CVE-2020-8694, CVE-2020-8695, INTEL-SA-00389](https://www.intel.com/content/www/us/en/developer/articles/technical/software-security-guidance/advisory-guidance/running-average-power-limit-energy-reporting.html)). Was world-readable before; broke many monitoring tools (e.g. [Prometheus node_exporter #2090](https://github.com/prometheus/node_exporter/issues/2090)).

**Workaround we already use:** `trcc system setup-rapl` chmods `+r` (or installs a udev rule) so the user's group can read. Lasts across reboots if the udev rule is in place.

### USB device discovery
- udev rules (we install via `trcc system setup`) for `/dev/sg*` (SCSI) and `/dev/hidraw*` (HID)
- `pyusb` + `libusb-1.0` for raw USB
- sysfs walk via `/sys/class/scsi_generic/sgN/device` for VID/PID

### Safety / future-proofing
- hwmon API stable since Linux 2.6 — no deprecation risk
- RAPL permissions: kernel 5.10 lockdown is *the* baseline now; udev rule fix is canonical (used by Scaphandre, codecarbon, and us)
- `pyusb` actively maintained ([repo health 2026](https://snyk.io/advisor/python/pyusb))

---

---

## Windows

### CPU temperature

Windows has **no native userland API** for CPU temperature. Three real paths:

| Path | Permission | Coverage | Source/Citation |
|---|---|---|---|
| `MSAcpi_ThermalZoneTemperature` (`root\wmi`) | None | ⚠️ Returns motherboard temp on most consumer hardware; many systems return nothing. Used to work better on Win 7; degraded on Win 10/11. | [Tom's Hardware forum](https://forums.tomshardware.com/threads/windows-defender-removes-vulnerabledriver-winnt-winring0.3892498/), prior research |
| **HWiNFO64 SHM** via `Global\HWiNFO_SENS_SM2` | None (read-only MMF) | ✅ Full coverage when HWiNFO is running. **12-hour limit on Free version**; Pro removes it. ARM64/non-Pro is also limited. | [HWiNFO Licensing 2026](https://www.hwinfo.com/licenses/), [SHM forum thread](https://www.hwinfo.com/forum/threads/shared-memory-support.18/) |
| **LibreHardwareMonitor** via `root\LibreHardwareMonitor` WMI namespace | LHM spawns + uses **WinRing0** kernel driver | ✅ Full coverage. ⚠️ **WinRing0 is being banned** — see safety section below | [LHM GitHub](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor), [Rainmeter LHM plugin](https://github.com/mallardduck/Rainmeter-LibreHardwareMonitor) |

**Failure modes:** MSAcpi returns blanks or motherboard-only temp on >70% of consumer hardware ([WindowsForum thread](https://windowsforum.com/threads/how-to-check-cpu-temperature-on-windows-11-4-reliable-methods.382828/)). HWiNFO SHM goes static after 12h on Free version. LHM's WinRing0 may be quarantined by Windows Defender as of March 2025.

### GPU temperature

| Vendor | Canonical API | Permission | Status |
|---|---|---|---|
| **NVIDIA** | NVML (`nvidia-ml-py` / `pynvml`) | None | ✅ Stable, vendor-supported, cross-platform. We already use it. |
| **AMD** | **ADLX SDK** (`AMD Device Library eXtra`) | None for monitoring; admin for tuning | ✅ **Canonical 2026** — has [Python samples](https://gpuopen.com/manuals/adlx/adlx-page_sample_py/) and [`ADLXPybind`](https://github.com/sfinktah/ADLXPybind). Requires Adrenalin **25.3.1+**. Replaces older ADL Display Library. |
| **Intel** | **oneAPI Level Zero `sysman` API** | None | ✅ Available on Windows; ships with [Intel Compute Runtime](https://github.com/intel/compute-runtime). [pti-gpu/system_management/LevelZero](https://github.com/intel/pti-gpu/blob/master/chapters/system_management/LevelZero.md) is the canonical doc. Python bindings via `pyze` or direct ctypes. |

**Failure modes:** ADLX requires recent Adrenalin install — older driver users won't see it. Intel L0 requires recent Iris/Arc driver stack. NVML works on every GeForce/Quadro/Tesla but not on Optimus laptops where the dGPU is asleep.

### Fan + voltage

Same triangle as CPU temp:
- **HWiNFO SHM** — full fan/voltage tree
- **LHM** via WMI — full fan/voltage tree
- **No native** — Windows doesn't expose fans/voltages outside motherboard SMBus

### Power consumption

- **CPU package power:** HWiNFO / LHM (via MSR reads — both bundled WinRing0)
- **GPU power:** NVML (NVIDIA), ADLX (AMD), L0 sysman (Intel)
- **System power:** none — only laptop battery via `Win32_Battery` WMI class

### USB device discovery

Three paths, all production-grade:

| Path | Pros | Cons | Citation |
|---|---|---|---|
| **SetupAPI** (`SetupDiGetClassDevs` + `SetupDiEnumDeviceInterfaces`) | Direct, fast | Verbose ctypes plumbing | [MSDN SetupAPI](https://learn.microsoft.com/en-us/windows/win32/api/setupapi/) |
| **WMI** `Win32_PnPEntity` | Simple Python via `wmi` package | Slower, depends on WMI service | [Medium: Automating Device Manager via Python WMI](https://snotna.medium.com/automating-windows-device-manager-with-python-and-wmi-883e1171e8cd) |
| **hidapi** (for HID devices only) | Cross-platform Python binding | HID-only | [hidapi PyPI](https://pypi.org/project/hid/) |

We currently use SetupAPI ([windows/detector.py](src/trcc/adapters/device/windows/detector.py)). Working canonically.

### Safety / future-proofing

🚨 **WinRing0 ban — April 2026 enforcement**

Microsoft Defender began flagging WinRing0 as `VulnerableDriver:WinNT/Winring0` in March 2025 ([Microsoft Support article](https://support.microsoft.com/en-us/windows/microsoft-defender-antivirus-alert-vulnerabledriver-winnt-winring0-eb057830-d77b-41a2-9a34-015a5d203c42)). The April 2026 Windows security update introduces stronger protections against known vulnerable kernel drivers ([Microsoft 2026 update note](https://support.microsoft.com/en-us/topic/april-2026-windows-security-updates-introduce-protections-to-known-vulnerable-kernel-drivers-1f8aaf7c-d4ac-4e02-be1d-b63c1b1aa9d0)).

Affected apps include: HWiNFO (when used with WinRing0 alternatives), LibreHardwareMonitor, OpenHardwareMonitor, MSI Afterburner, EVGA Precision X1, OpenRGB, FanCtrl, CapFrameX, ZenTimings, SteelSeries Engine, OmenMon ([Neowin Windows 11/10 flagging WinRing0](https://www.neowin.net/news/windows-1110-is-flagging-winring0-on-your-pc-monitoring-fan-control-apps-heres-why/)). **TRCC's bundled LHM is in this list.**

Microsoft is also blocking cross-signed kernel drivers and enforcing WHCP-only signing starting April 2026 ([WindowsNews 2026 kernel security overhaul](https://windowsnews.ai/article/windows-april-2026-kernel-security-overhaul-microsoft-blocks-cross-signed-roots-enforces-whcp.407924)). New kernel drivers must be submitted to Microsoft Hardware Developer Center.

**Mitigation already exists — PawnIO:**

LHM **v0.9.6+** ships a [PawnIO](https://poorlydocumented.com/2025/09/replacing-winring0-in-fan-control-with-pawnio/)-based build instead of WinRing0. **PawnIO is signed by Microsoft *and* the driver's author** — it executes Pawn bytecode in ring-0 with safety checks, so it's not on Defender's hit list. FanControl migrated to PawnIO in V238 ([DeepWiki: Driver Evolution and Anti-Virus Issues](https://deepwiki.com/Rem0o/FanControl.Releases/5.4-driver-evolution-and-anti-virus-issues)); OpenRGB has a merge request open ([OpenRGB MR !2833](https://gitlab.com/CalcProgrammer1/OpenRGB/-/merge_requests/2833)).

**Implication for TRCC:** the LHM bundle is *not* dead — we just need to be on **LHM ≥ 0.9.6 with the PawnIO build**. Our [_lhm_subprocess.py](src/trcc/adapters/system/windows/sources/lhm.py) already mentions "Install LHM v0.9.6 + PawnIO manually" in its warning string; the bundling step needs to actually pull the PawnIO build. Known limitation: PawnIO is blocked by FACEIT anti-cheat (per [PawnIO project notes](https://www.file.net/process/pawnio.sys.html)) — non-issue for TRCC's user base.

**Long-term** (still recommended): add the **vendor-SDK chain** (ADLX + NVML + L0) for GPU + **HWiNFO SHM tier** for CPU. The existing strategy chain we just shipped already supports this — `ADLXSource`, `IntelL0Source` would each be one new file under [windows/sources/](src/trcc/adapters/system/windows/sources/) following the same `@WindowsSensorSource.register('key')` pattern.

Sources:
- [Microsoft April 2026 vulnerable driver protections](https://support.microsoft.com/en-us/topic/april-2026-windows-security-updates-introduce-protections-to-known-vulnerable-kernel-drivers-1f8aaf7c-d4ac-4e02-be1d-b63c1b1aa9d0)
- [PCWorld — Windows Defender flags WinRing0](https://www.pcworld.com/article/2912435/if-windows-defender-flags-winring0-on-your-gaming-pc-pay-attention.html)
- [HWiNFO Licenses 2026](https://www.hwinfo.com/licenses/)
- [AMD GPUOpen ADLX](https://gpuopen.com/adlx/)
- [Intel Level Zero sysman](https://github.com/intel/pti-gpu/blob/master/chapters/system_management/LevelZero.md)
- [WHQL April 2026 enforcement](https://windowsnews.ai/article/windows-april-2026-kernel-security-overhaul-microsoft-blocks-cross-signed-roots-enforces-whcp.407924)

---

## macOS — Intel

### CPU temperature
**SMC keys** — `TC0P` (proximity), `TC0D` (die), `TC0E`, `TC1C`-`TC3C` (per-core). Read via `IOServiceMatching("AppleSMC")` + `IOConnectCallStructMethod`. Stable for years; we already use this in [macos/sensors.py](src/trcc/adapters/system/macos/sensors.py).

### GPU temperature
**SMC keys** — `TG0P`, `TG0D` for discrete; iGPU varies. Same SMC interface.

### Fan + voltage
**SMC keys** — `F0Ac`, `F1Ac` (fan actual RPM), `VC0C` (CPU voltage). [iSMC](https://github.com/dkorunic/iSMC), [smctemp](https://github.com/narugit/smctemp), Macs Fan Control all use this exact set.

### Power consumption
**`powermetrics`** — requires **sudo** ([powermetrics docs](https://firefox-source-docs.mozilla.org/performance/powermetrics.html)). Or **IOReport** (no sudo, see Apple Silicon section).

### USB device discovery
**IOKit** via `IOServiceMatching("IOUSBDevice")` + `IOServiceGetMatchingServices`. Same on Intel and Apple Silicon. We use ctypes to `CDLL('IOKit')`. Wraps cleanly via [PyObjC](https://pyobjc.readthedocs.io/) too.

### Safety / future-proofing
- **Notarization:** does NOT require sandboxing. Hardened runtime is enough ([Apple notarization doc](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution)). We can still read SMC + IOReport from a notarized non-sandboxed Python app.
- **macOS 26 SDK:** PyObjC actively maintained against current SDK ([PyObjC changelog](https://pyobjc.readthedocs.io/en/latest/changelog.html)) — both ctypes and PyObjC paths viable.

---

## macOS — Apple Silicon (M1 → M5+)

### CPU temperature
**Two paths, both currently necessary:**

1. **SMC keys** — work for *some* readings, but **key names change per chip generation** (`Tp01/Tp05/Tp09` on M1, different on M3+) ([ThermalForge](https://github.com/ProducerGuy/ThermalForge), [iSMC](https://github.com/dkorunic/iSMC)). No universal table — must auto-discover.
2. **HID sensor hub** — Apple Silicon exposes temp/voltage/current/power through a HID sensor hub (per [iSMC docs](https://github.com/dkorunic/iSMC)). This is what `iSMC` and `mac_temp_sensor` use for AS coverage.

**We already use both** in [macos/sensors.py](src/trcc/adapters/system/macos/sensors.py) and [macos/hid_sensors.py](src/trcc/adapters/system/macos/hid_sensors.py). Coverage is *partial* because Apple changes key names per generation — some readings work on M1 but not M4, etc.

### GPU temperature & power (both require IOReport)
**IOReport API** (`IOReportCreateSubscription` + `IOReportCopyChannelsWithID` + `IOReportCreateSamples`) is the canonical Apple Silicon path used by [mactop](https://github.com/metaspartan/mactop), [macpow](https://github.com/k06a/macpow), [asitop](https://github.com/tlkh/asitop), [macgtop](https://github.com/Acelogic/macgtop), [macmon](https://www.x-cmd.com/install/macmon/).

**Key advantages:**
- **No sudo required** — IOReport reads without root ([macmon: no-root CPU/GPU/ANE monitor](https://www.x-cmd.com/install/macmon/))
- Exposes **CPU + GPU + ANE + DRAM power**, all temps Apple chooses to publish
- Works across M1 → M5+ (channels named, not key-coded — version-resilient)

**Caveat:** IOReport is a **private framework**. No public Apple developer docs ([Alex DeLorenzo: reverse-engineering CoreDisplay/IOReport](https://alexdelorenzo.dev/programming/2018/08/16/reverse_engineering_private_apple_apis)). Reference implementations: [test-ioreport](https://github.com/freedomtan/test-ioreport), [OSXPrivateSDK IOReport.h](https://github.com/samdmarshall/OSXPrivateSDK/blob/master/PrivateSDK10.10.sparse.sdk/usr/local/include/IOReport.h). Apple has not changed it in years (stable in practice across Big Sur → Sequoia).

**ctypes vs PyObjC:** PyObjC handles IOReport's CoreFoundation types more transparently (per [PyObjC framework wrappers](https://pyobjc.readthedocs.io/en/latest/notes/framework-wrappers.html)). ctypes works but requires manual CFRelease bookkeeping. **Recommendation:** start with ctypes (matches our IOKit pattern); PyObjC is a swap-out option if memory bookkeeping bites.

### Fan + voltage
**SMC keys still work** for fans on Apple Silicon (e.g. `F0Ac`). Same as Intel.

### Power consumption
**IOReport** (no sudo) — preferred. **`powermetrics`** (sudo) — fallback.

### USB device discovery
Same as Intel: IOKit `IOUSBDevice` matching.

### Safety / future-proofing
- **IOReport is private but stable** — used by every shipping Apple Silicon monitor (mactop, macpow, asitop, macmon). Apple has not deprecated it across major macOS versions.
- **Notarization:** non-sandboxed notarized app can call private framework symbols via `dlopen`. We need hardened runtime + Developer ID signature + notarization for distribution outside Mac App Store.
- **No Mac App Store path** for IOReport-using apps — App Store sandbox blocks private framework access.

Sources for macOS:
- [ThermalForge — open-source Apple Silicon fan control with SMC discovery](https://github.com/ProducerGuy/ThermalForge)
- [iSMC — Apple SMC CLI with M1-M5 HID sensor hub support](https://github.com/dkorunic/iSMC)
- [mactop — Apple Silicon Monitor Top, IOReport-based](https://github.com/metaspartan/mactop)
- [macpow — Real-time power tree TUI for Apple Silicon](https://github.com/k06a/macpow)
- [macmon — Apple Silicon power stats without sudo](https://www.x-cmd.com/install/macmon/)
- [asitop — Python performance monitoring CLI for Apple Silicon](https://github.com/tlkh/asitop)
- [test-ioreport — IOReport API reference impl](https://github.com/freedomtan/test-ioreport)
- [OSXPrivateSDK IOReport.h — IOReport C interface](https://github.com/samdmarshall/OSXPrivateSDK/blob/master/PrivateSDK10.10.sparse.sdk/usr/local/include/IOReport.h)
- [macOS-hardware-stats KnownSMCKeys.md](https://github.com/tigattack/macOS-hardware-stats/blob/main/KnownSMCKeys.md)
- [PyObjC framework wrappers](https://pyobjc.readthedocs.io/en/latest/notes/framework-wrappers.html)
- [Apple notarization docs](https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution)

---

## FreeBSD

### CPU temperature
- **Intel:** `coretemp` kernel module — `kldload coretemp` (or `coretemp_load="YES"` in `/boot/loader.conf`). Reads `dev.cpu.N.temperature` via sysctl.
- **AMD:** `amdtemp` kernel module. **Zen 5 support added to `amdtemp(4)` + `amdsmn(4)` in 15.0-CURRENT, merged to stable/14.** Older 14.2-RELEASE needs patches.
- ⚠️ **Some AMD EPYC don't report** even with `amdtemp` loaded — known limitation.

Sources:
- [FreshPorts amdtemp man page](https://man.freebsd.org/cgi/man.cgi?query=amdtemp&manpath=FreeBSD+9.1-RELEASE)
- [FreeBSD forum: Zen 5 amdtemp](https://forums.freebsd.org/threads/getting-cpu-temperature-on-amd-ryzen-zen-5-cpus.97086/)
- [coretemp man page](https://man.freebsd.org/cgi/man.cgi?coretemp=)

### GPU temperature
🚨 **No native path for AMD/Intel GPUs on FreeBSD.**
- "There is no sysctl for AMD GPU's. The amdgpu driver under FreeBSD cannot monitor GPU temps, not natively, not via sysctl, not via acpi" ([FreeBSD forum](https://forums.freebsd.org/threads/amdgpu-is-there-a-way-to-read-amd-gpus-temperatures-and-or-power-usage.76245/))
- `radeontop` doesn't show temps; patches exist but never accepted upstream
- The drm-kmod port lags Linux; hwmon hooks for amdgpu/i915 not exposed

**NVIDIA:** `nvidia-driver` port works (`pkg install nvidia-driver`) but missing **Vulkan + CUDA**. NVML availability uncertain — typically present with the driver but Python `nvidia-ml-py` may fail to load the library on FreeBSD.

### Fan + voltage
**`hw.sensors` framework** (ported from OpenBSD via 2007 GSoC) — exposes via sysctl. Coverage **limited** on consumer FreeBSD: `bsdhwmon` is server-board-focused (Supermicro, IPMI). Consumer Nuvoton chips like `nct6779` typically NOT covered ([bsdhwmon README](https://github.com/koitsu/bsdhwmon/blob/master/README.md), [Vermaden FreeBSD sensors](https://vermaden.wordpress.com/2022/02/15/sensors-information-on-freebsd/)).

### Power consumption
RAPL on FreeBSD: extremely limited compared to Linux. No clean userspace API.

### USB device discovery
- **`usbconfig(8)`** CLI — `usbconfig list` for enumeration
- **libusb20(3)** native FreeBSD library; libusb 0.1.12 compatible
- **pyusb works** with libusb backend (per [PyUSB site](https://pyusb.github.io/pyusb/))
- We use this in [adapters/device/bsd/detector.py](src/trcc/adapters/device/bsd/detector.py)

### Safety / future-proofing
- `coretemp`/`amdtemp` are stable kernel modules; Zen 5 support landed 14-stable
- AMD GPU temp gap unlikely to close near-term (drm-kmod ports tail upstream Linux by 1-2 years)
- NVIDIA driver maintained at recent versions but always lags Linux

---

---

## OpenBSD

### CPU temperature
**`cpu(4)` driver** reads MSR directly for modern Intel/AMD — `sysctl hw.sensors.cpu0.temp0`. Available out of the box, no module loading.

⚠️ "Due to the way thermal information is reported on Intel processors, the temperature may be off by ±15°C" ([cpu(4) man page](https://man.openbsd.org/cpu)).

### GPU temperature
- **NVIDIA:** ❌ **Zero support.** OpenBSD philosophically refuses binary blobs; NVIDIA refuses to provide source ([OpenBSD nv(4) man page](https://man.openbsd.org/nv.4)). Reverse-engineered `nv` Xorg driver provides 2D only, no telemetry.
- **AMD:** No GPU temp via `hw.sensors`. `radeon` driver is open-source and well-supported for display, but doesn't expose hwmon-style sensors.
- **Intel:** Same — `inteldrm` for display only, no telemetry.

### Fan + voltage
**`hw.sensors` framework — wide coverage** via Super I/O drivers:
- `lm(4)` — generic LM78/79 + many compatible chips (consumer motherboards)
- `it(4)` — ITE IT87xx series
- `aibs(4)` — ASUS ATK0110 ACPI sensors (good consumer ASUS coverage)
- `viasio`, `nsclpcsio`, `fins`, `schsio` — various other chips

`sysctl hw.sensors.lm0.fan1=1607 RPM`, `sysctl hw.sensors.aibs0.temp0=...` etc. ([sensorsd tutorial](https://calomel.org/sensorsd_config.html), [hw.sensors paper](https://www.openbsd.org/papers/asiabsdcon2009-sensors-paper.pdf))

**OpenBSD's hw.sensors is widely considered better than FreeBSD's port** for consumer hardware coverage.

### Power consumption
Limited. `apm(4)`/`acpibtn(4)` for battery; no comprehensive RAPL equivalent.

### USB device discovery
- **`usbdevs(1)`** CLI, **libusb** in ports (`pkg_add libusb1`)
- pyusb works
- `pledge`/`unveil` may restrict — `pledge("rpath", NULL)` is enough for sysfs USB walking

### Safety / future-proofing
- `pledge`/`unveil` add a security layer — Python `ctypes` can call them ([nullprogram: pledge from Python](https://nullprogram.com/blog/2021/09/15/)). Sensor reads need the `"ps"` promise (sysctl ps subset).
- OpenBSD 7.7 → 7.9: stricter pledge enforcement ([OpenBSD 7.9 errata](https://discoverbsd.com/p/afb2c91622)) — code that opens device nodes without proper pledge will fail.
- NVIDIA gap is structural and unlikely to ever close on OpenBSD.

---

---

## Cross-cutting Python ecosystem

### pyusb — maintenance status, alternatives

- ✅ **Healthy as of 2026.** Last update **Feb 12, 2026** ([Snyk health report](https://snyk.io/advisor/python/pyusb)).
- Python ≥ 3.9 + ctypes + libusb-1.x backend works on Linux/Windows/macOS/FreeBSD/OpenBSD
- Caveat: low PR activity in past month — feature-complete library, not an abandoned one

**Alternative:** `libusb-package` (pyocd/libusb-package) bundles libusb-1.0 DLL for Windows — convenience wrapper. Not a replacement for pyusb itself.

### hidapi — health, Python binding maturity

- **`hidapi` PyPI** ([trezor/cython-hidapi](https://github.com/trezor/cython-hidapi)) — **actively maintained** by Trezor team. **v0.15.0** with Python 3.14 wheels for Windows + Linux + musllinux + macOS.
- Linux: uses hidraw API by default; libusb available via build flag
- ✅ Cross-platform: Windows, Linux, FreeBSD, macOS

### libusb-1.x — platform parity

✅ **Linux, macOS, Windows (Vista+), Android, OpenBSD, NetBSD, Haiku, Solaris** ([libusb.info](https://libusb.info/))

FreeBSD compatible via libusb20(3) ABI shim. The single most cross-portable USB library in the ecosystem.

### pynvml / nvidia-ml-py — current vendor support

- ✅ **Official NVIDIA package**: [`nvidia-ml-py` on PyPI](https://pypi.org/project/nvidia-ml-py/) (copyright 2011-2025 NVIDIA)
- Wraps NVML C library; supports modern features (GPU fabric health, etc.)
- Cross-platform: anywhere CUDA / NVIDIA driver runs (Linux, Windows, FreeBSD with nvidia-driver port)
- Don't use `nvidia-ml-py3` (deprecated) or `pynvml` from `gpuopenanalytics` (community fork — official is better)

### Driver signing / kernel-level access

| OS | Userland sensor read possible? | Kernel driver required? | Driver signing change 2026? |
|---|---|---|---|
| Linux | ✅ Mostly (hwmon, RAPL, sysfs) | Only for low-level (CPU MSR via msr-tools) | No |
| Windows | ❌ Almost nothing native (MSAcpi unreliable) | Yes — WinRing0 (banned) → PawnIO (signed) → vendor SDKs | **WHCP enforcement April 2026** — only Microsoft-signed drivers accepted |
| macOS | ⚠️ SMC/IOReport are private but no driver needed | No (private framework via dlopen) | No (notarization since 2019) |
| FreeBSD | ✅ via kernel modules (`coretemp`, `amdtemp`, `hw.sensors`) | Modules already kernel-signed via base | No |
| OpenBSD | ✅ via built-in drivers | Kernel-built, no separate signing | No (philosophy: source-only, no signed blobs) |

### WinRing0 → PawnIO migration tracker

| App | Status | Source |
|---|---|---|
| FanControl | ✅ Migrated v238+ | [DeepWiki](https://deepwiki.com/Rem0o/FanControl.Releases/5.4-driver-evolution-and-anti-virus-issues) |
| LibreHardwareMonitor | ✅ v0.9.6+ | [Poorly Documented blog](https://poorlydocumented.com/2025/09/replacing-winring0-in-fan-control-with-pawnio/) |
| HWiNFO | ✅ Has its own (separate) driver, signed | n/a |
| OpenRGB | ⚠️ Merge request open | [OpenRGB MR !2833](https://gitlab.com/CalcProgrammer1/OpenRGB/-/merge_requests/2833) |
| MSI Afterburner | ⚠️ Pending | [Guru3D forum](https://forums.guru3d.com/threads/will-ab-4-6-7-migrate-over-to-pawnio-now-that-ms-is-actively-treating-winring0-as-a-trojan.458413/) |
| OmenMon, EVGA Precision, ZenTimings, SteelSeries, CapFrameX | ⚠️ Various states | [Neowin coverage](https://www.neowin.net/news/windows-1110-is-flagging-winring0-on-your-pc-monitoring-fan-control-apps-heres-why/) |

**Implication for TRCC:** ship LHM ≥ 0.9.6 (PawnIO build). Update [_lhm_subprocess.py](src/trcc/adapters/system/windows/sources/lhm.py)'s warning copy now reflects the v0.9.6+PawnIO pinning correctly.

---

---

## Final verdict matrix

| OS | CPU temp | GPU NVIDIA | GPU AMD | GPU Intel | Fan/voltage | Power | USB discovery | Verdict |
|---|---|---|---|---|---|---|---|---|
| **Linux** | ✅ hwmon | ✅ pynvml | ✅ amdgpu hwmon | ✅ i915 hwmon | ✅ hwmon | ⚠️ RAPL needs udev | ✅ udev + pyusb | **Green** (only RAPL needs the udev rule we already install) |
| **Windows** | ⚠️ HWiNFO/LHM/MSAcpi | ✅ pynvml | ⚠️ → ADLX (canonical 2026) | ⚠️ → L0 sysman | ⚠️ HWiNFO/LHM | ⚠️ HWiNFO/LHM/NVML/ADLX | ✅ SetupAPI | **Yellow now → Green if we add ADLX + L0 + ship LHM-PawnIO build** |
| **macOS Intel** | ✅ SMC | ✅ pynvml (rare) | ⚠️ SMC for some keys | n/a | ✅ SMC | ⚠️ powermetrics (sudo) | ✅ IOKit | **Yellow** (power needs sudo unless IOReport added) |
| **macOS Apple Silicon** | ⚠️ SMC + HID, key gen-variability | n/a | n/a | ⚠️ → IOReport | ✅ SMC | ⚠️ → IOReport (no sudo) | ✅ IOKit | **Yellow now → Green if we add IOReport source** |
| **FreeBSD** | ✅ coretemp/amdtemp | ✅ pynvml (rare on BSD) | ❌ no path | ❌ no path | ⚠️ hw.sensors (consumer-limited) | ❌ no path | ✅ usbconfig + libusb | **Yellow → ecosystem-blocked for AMD/Intel GPU + system power** |
| **OpenBSD** | ✅ cpu(4) (±15°C variance) | ❌ none | ❌ no telemetry | ❌ no telemetry | ✅ hw.sensors (excellent consumer coverage via aibs/lm/it) | ❌ no path | ✅ usbdevs + libusb | **Yellow → ecosystem-blocked for GPU + system power; CPU is great** |

Legend: ✅ green = canonical, no caveats. ⚠️ yellow = works with caveats or could be improved with code we *can* write. ❌ red = no clean path; ecosystem-blocked, not on us.

---

## Recommended next code changes

Each entry below has an **upgradable-to-green path** in the verdict matrix above — i.e. we have evidence the code can be written and will work. **No speculation.**

### Highest leverage — Windows (3 changes)

| # | Change | Closes which yellow? | Effort | Risk |
|---|---|---|---|---|
| 1 | **Pin LHM bundle to v0.9.6+ (PawnIO build)** in [installer / dist scripts](src/trcc/install/) | WinRing0 ban (Apr 2026 enforcement) | XS — bundling change, no code | None — verified by FanControl V238 + LHM upstream |
| 2 | **Add `ADLXSource` (priority 15)** under [windows/sources/](src/trcc/adapters/system/windows/sources/) | AMD GPU temp/usage/power on Windows without LHM | M — ~250 LOC ctypes around ADLX SDK; Python sample exists | Low — vendor-supported SDK, [Python samples](https://gpuopen.com/manuals/adlx/adlx-page_sample_py/) |
| 3 | **Add `IntelL0Source` (priority 17)** under same dir | Intel iGPU + Arc temp on Windows | M — ~200 LOC ctypes around Level Zero `zes_*` (sysman); ships with [Intel Compute Runtime](https://github.com/intel/compute-runtime) | Low — vendor-supported, used by Phoronix benchmarks |

After these three: Windows verdict moves to **Green** for every column.

### Medium leverage — macOS (1 change)

| # | Change | Closes which yellow? | Effort | Risk |
|---|---|---|---|---|
| 4 | **Add `IOReportSource`** as a new strategy in [macos/sensors.py](src/trcc/adapters/system/macos/sensors.py) — same pattern as the Windows chain | Apple Silicon GPU temp/power, removes powermetrics-sudo requirement | L — ~300 LOC ctypes around private IOReport framework; [test-ioreport](https://github.com/freedomtan/test-ioreport) is the reference impl | Medium — private framework, but stable in practice for 5+ years per mactop/macpow/macmon shipping evidence |

After this: macOS verdict moves to **Green** on Apple Silicon.

### Low leverage — BSD (no code changes possible)

The BSD red entries (AMD/Intel GPU temp on FreeBSD, all GPU on OpenBSD) are **ecosystem-blocked**. No amount of code on our side closes them. The honest path:
- Document the gap in README + per-distro install docs
- Surface "GPU temp not available on FreeBSD/OpenBSD — see vendor driver status" in `trcc doctor` output
- Don't pretend we have data we don't

### Linux (no code changes — already green)

The only yellow is RAPL permission, and we already install a udev rule via `trcc system setup-rapl`.

### Cross-cutting — pin recommendations

After the above, the recommended pyproject extras would be:

| Extra | Adds | Use case |
|---|---|---|
| `[windows]` | pywin32, wmi (already exists) | (Windows base) |
| `[windows-vendor]` (NEW) | ADLX wheels (not yet on PyPI — would need build) + Intel `pyze` (community) | Power users without LHM running |
| `[hid]` | hidapi (already exists) | All OSes for HID protocol devices |
| `[nvidia]` | nvidia-ml-py (already exists) | All OSes with NVIDIA GPU |
| `[macos-iorport]` (NEW) | (just version-pin if we vendor the IOReport ctypes wrapper) | Apple Silicon best path |

---

## Summary — what changes after this research

**Verdict updates from earlier guesses:**

1. **Windows is *more* fixable than I thought.** PawnIO + ADLX + L0 are all canonical, vendor-supported paths. The "WinRing0 ban kills monitoring on Windows" framing was wrong — there's a clean migration path that the rest of the ecosystem (FanControl, LHM, etc.) is already taking.

2. **macOS Apple Silicon yellow is fixable** — IOReport is private but stable; every shipping AS monitoring tool uses it; reference implementations exist.

3. **BSD red is *not* fixable in our code.** The graphics stack on FreeBSD/OpenBSD genuinely doesn't expose what we'd read for AMD/Intel GPU temp. This is a multi-year ecosystem gap.

4. **Linux is solid as-is.** Only RAPL permission needs the udev rule (which we install).

5. **Cross-platform Python deps are healthy.** pyusb, hidapi, nvidia-ml-py all actively maintained as of Feb 2026.

The roadmap to "all-green except BSD" is **4 new sensor source files** (ADLX, IntelL0, LHM-PawnIO bundling, IOReport), zero architecture changes, zero new dependencies on the trickle-down chain.

