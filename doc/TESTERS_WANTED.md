# Testers Wanted

TRCC Linux supports many Thermalright devices, but I only own SCSI-based coolers (`87CD:70DB`). Most HID and LED devices have been implemented from reverse-engineering the Windows TRCC 2.1.2 source — they need real hardware validation.

If you have a device listed below, testing takes about 2 minutes. Your help directly improves support for everyone on Linux.

## How to Test

1. Install TRCC Linux (see [README](../README.md#native-packages-recommended) for your distro)
2. Run this one command and copy the output:
```bash
trcc report
```

3. [Open an issue](https://github.com/Lexonight1/thermalright-trcc-linux/issues/new) and paste the output
4. Optionally try `trcc gui` and report what works / what doesn't

Even a "nothing happened" report is useful — the `hid-debug` output tells us exactly where the protocol breaks.

## Confirmed Working

These devices have been tested on real Linux hardware by contributors:

| Device | USB ID | Protocol | Tested By |
|--------|--------|----------|-----------|
| FROZEN HORIZON PRO | `87CD:70DB` | SCSI | Developer |
| FROZEN MAGIC PRO | `87CD:70DB` | SCSI | Developer |
| FROZEN VISION V2 | `87CD:70DB` | SCSI | Developer |
| LC1, LC2, LC3, LC5 | `0416:5406` | SCSI | Developer |
| FROZEN WARFRAME, WARFRAME SE | `0402:3922` | SCSI | Developer |
| GrandVision 360 AIO | `87AD:70DB` | Bulk | [bipobuilt](https://github.com/bipobuilt) |
| Mjolnir Vision 360 | `87AD:70DB` | Bulk | [Pikarz](https://github.com/Pikarz) |
| Trofeo Vision LCD | `0416:5302` | HID Type 2 | [PantherX12max](https://github.com/PantherX12max), [N8ghtz](https://github.com/N8ghtz) |
| Assassin Spirit 120 Vision ARGB | `0416:5302` | HID Type 2 | [michael-spinelli](https://github.com/michael-spinelli) |
| Frozen Warframe SE | `0402:3922` | SCSI | [apj202-ops](https://github.com/apj202-ops) |
| FROZEN WARFRAME (SCSI) | `0402:3922` | SCSI | [gizbo](https://github.com/gizbo) |
| Wonder Vision Pro 360 | `87AD:70DB` | Bulk | [Civilgrain](https://github.com/Civilgrain) |
| HR10 2280 PRO Digital | `0416:8001` | HID LED | [Lcstyle](https://github.com/Lcstyle) |
| AX120 Digital (LED) | `0416:8001` | HID LED | [shadowepaxeor-glitch](https://github.com/shadowepaxeor-glitch), [hexskrew](https://github.com/hexskrew) |
| Assassin X 120R Digital ARGB (LED) | `0416:8001` | HID LED | [hexskrew](https://github.com/hexskrew) |
| Peerless Assassin 120 Digital ARGB White (LED) | `0416:8001` | HID LED | [Xentrino](https://github.com/Xentrino) |
| Phantom Spirit 120 Digital EVO (LED) | `0416:8001` | HID LED | [javisaman](https://github.com/javisaman), [Rizzzolo](https://github.com/Rizzzolo) |
| Trofeo Vision 9.16 LCD | `0416:5408` | LY (Bulk) | [Mr-Renegade](https://github.com/Mr-Renegade) |
| Frozen Warframe Pro | `87AD:70DB` | Bulk | [loosethoughts19-hash](https://github.com/loosethoughts19-hash) |
| Elite Vision 360 ARGB Black | `0402:3922` | SCSI | [tensaiteki](https://github.com/tensaiteki) |
| GrandVision 360 AIO | `87AD:70DB` | Bulk | [Reborn627](https://github.com/Reborn627) |
| Peerless Assassin 120 Digital ARGB (LED) | `0416:8001` | HID LED | [Pewful2021](https://github.com/Pewful2021) |
| Frozen Warframe 240 (HID) | `0416:5302` | HID Type 2 | [riodevelop](https://github.com/riodevelop), [wobbegongus](https://github.com/wobbegongus) |
| HID Type 2 (generic) | `0416:5302` | HID Type 2 | [mog199](https://github.com/mog199) |
| PA120 DIGITAL LCD | `0416:8040` | HID | [lallemandgianni-boop](https://github.com/lallemandgianni-boop) |
| Stream Vision | `87AD:70DB` | Bulk | [Me-shok](https://github.com/Me-shok) |
| Wonder Vision 360 UB ARGB | `87AD:70DB` | Bulk | [Alb3e3](https://github.com/Alb3e3) (Windows) |

## Need Testers — Custom Overlay Masks (v8.3.8)

New feature: upload your own PNG mask overlay and position it with X/Y controls. Custom masks are saved to `~/.trcc-user/` (survives uninstall and data re-download).

**What to test:**
1. Open GUI → Theme Settings → Mask panel → click Upload icon
2. Select a PNG image → crop dialog → confirm
3. Mask should appear in the mask browser with a thumbnail
4. Adjust X/Y position using the +/- buttons
5. Toggle visibility with the eye icon
6. Right-click a custom mask → Delete should remove it

If anything is off (positioning, cropping, display), [open an issue](https://github.com/Lexonight1/thermalright-trcc-linux/issues/new) with your screen resolution and device model.

## Need Testers — HID LCD Devices

These use USB HID to display images on the LCD. Code is complete, but no one has tested with real hardware.

| Device | USB ID | Protocol | Priority |
|--------|--------|----------|----------|
| BA120 VISION | `0416:5302` | HID Type 2 | High |
| FROZEN WARFRAME SE (HID variant) | `0416:5302` | HID Type 2 | Medium |
| FROZEN WARFRAME PRO | `0416:5302` | HID Type 2 | Medium |
| ELITE VISION (HID variant) | `0416:5302` | HID Type 2 | Medium — SCSI variant confirmed by tensaiteki |
| LC5 (HID variant) | `0416:5302` | HID Type 2 | Medium |
| TARAN ARMS | `0418:5303` | HID Type 3 | High — only Type 3 device |
| TARAN ARMS | `0418:5304` | HID Type 3 | High — only Type 3 device |

## Need Testers — LED RGB Devices

These use PID `0416:8001` for RGB LED ring control. The device model is auto-detected via a HID handshake PM byte. Many models share this PID.

**Standard LED devices (FormLED in Windows):**

| Device | PM Byte | LED Style | Status |
|--------|---------|-----------|--------|
| RK120 DIGITAL | 23 | Style 2 | Untested |
| AK120 DIGITAL | 32 | Style 3 (64 LEDs, 10 segments) | Untested |
| FROZEN HORIZON PRO (LED) | 1 | Style 1 | Untested |
| FROZEN MAGIC PRO (LED) | 2 | Style 1 | Untested |
| LF8 | 48 | Style 5 (93 LEDs, 23 segments) | Untested |
| LF10 | 96 | Style 7 (116 LEDs, 12 segments) | Untested |
| LF11 | 129 | Style 10 (38 LEDs, 17 segments) | Untested |
| LF12 | 80 | Style 6 (124 LEDs, 72 segments) | Untested |
| LF13 | 160 | Style 12 (62 LEDs, 62 segments) | Untested |
| LF15 | 144 | Style 11 (93 LEDs, 72 segments) | Untested |
| LC1 | 128 | Style 4 (31 LEDs, 14 segments) | Untested |
| LC2 | 112 | Style 9 (61 LEDs, 31 segments) | Untested |
| CZ1 | 208 | Style 8 (18 LEDs, 13 segments) | Untested |

**Vision-series RGB devices (case 257 in Windows — needs PM mapping work):**

These devices also connect via `0416:8001` but use different PM byte mappings that overlap with the standard LED devices. Supporting these requires protocol investigation.

| Device | PM (case 257) | Status |
|--------|---------------|--------|
| Mjolnir VISION | 5 | Not yet mapped |
| Mjolnir VISION PRO | 7 (sub=2) | Not yet mapped |
| GRAND VISION | 1 | Not yet mapped — PM overlaps with FROZEN HORIZON PRO |
| Stream Vision | 7 (sub=1) | Not yet mapped |
| FROZEN WARFRAME Ultra | 6 (sub=1) | Not yet mapped |
| FROZEN VISION V2 (RGB) | 6 (sub=2) | Not yet mapped |
| RP130 VISION | 2 | Not yet mapped — PM overlaps with FROZEN MAGIC PRO |
| LM16SE | 2 (sub=3) | Not yet mapped |
| LM22 | 1 (sub=48) | Not yet mapped |
| LM24 | 2 (sub=128) | Not yet mapped |
| LM26 | — | Not yet mapped |
| LM27 | 1 (sub=49) | Not yet mapped |

> If you have any of these Vision-series devices, a `trcc system hid-debug` dump would be extremely valuable — it will help us figure out how Windows distinguishes these from the standard LED devices.

## All TRCC-Compatible Models (from Thermalright's download page)

The following models are listed on [Thermalright's official download page](https://www.thermalright.com/support/download/) as compatible with TRCC software. They all use the same USB controllers we already support — we just need testers to confirm which USB ID and protocol each one uses.

**Models not yet tested on Linux (USB ID unknown — need `lsusb` output):**

| Device | Likely Protocol | Status |
|--------|----------------|--------|
| Burst Assassin 120 Vision | HID LCD or LED | Need tester |
| Core Matrix VISION | HID LCD or LED | Need tester |
| Core Vision | HID LCD or LED | Need tester |
| Hyper Vision | HID LCD or LED | Need tester |
| Levita Vision | HID LCD or LED | Need tester |
| MC-3 DIGITAL | HID LCD or LED | Need tester |
| Peerless Assassin 140 Digital | HID LCD or LED | Need tester |
| Peerless Vision | HID LCD or LED | Need tester |
| Phantom Spirit 120 Digital/Vision | HID LCD or LED | Need tester |
| Rainbow Vision | HID LCD or LED | Need tester |
| Royal Knight 130 Digital/Vision | HID LCD or LED | Need tester |
| Royal Lord 120 Vision | HID LCD or LED | Need tester |
| TL-M10 VISION | HID LCD or LED | Need tester |
| TR-A70 Vision | HID LCD or LED | Need tester |
| Wonder Vision | HID LCD or LED | Need tester |

> A single `lsusb` line from any of these devices tells us exactly which protocol it uses and whether it should work out of the box.

## Not Sure If Your Device Is Listed?

Run `lsusb` and look for `0416`, `0418`, `87cd`, or `0402` in the output. If you see a match, [open an issue](https://github.com/Lexonight1/thermalright-trcc-linux/issues/new) — even if your exact model isn't listed above, it likely shares the same USB controller.
