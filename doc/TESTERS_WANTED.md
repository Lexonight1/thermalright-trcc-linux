# Testers Wanted

TRCC Linux supports many Thermalright devices, but I only own SCSI-based coolers (`87CD:70DB`). Most HID and LED devices have been implemented from reverse-engineering the Windows TRCC 2.0.3 source — they need real hardware validation.

If you have a device listed below, testing takes about 2 minutes. Your help directly improves support for everyone on Linux.

## How to Test

1. Install TRCC Linux:
```bash
pip install trcc-linux
sudo trcc setup-udev
```
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
| HR10 2280 PRO Digital | `0416:8001` | HID LED | [Lcstyle](https://github.com/Lcstyle) |
| AX120 Digital (LED) | `0416:8001` | HID LED | [shadowepaxeor-glitch](https://github.com/shadowepaxeor-glitch) |
| Peerless Assassin 120 Digital ARGB White (LED) | `0416:8001` | HID LED | [Xentrino](https://github.com/Xentrino) |

## Need Testers — HID LCD Devices

These use USB HID to display images on the LCD. Code is complete, but no one has tested with real hardware.

| Device | USB ID | Protocol | Priority |
|--------|--------|----------|----------|
| Trofeo Vision LCD | `0416:5302` | HID Type 2 | High — user reported detection issue (#1) |
| AS120 VISION | `0416:5302` | HID Type 2 | High |
| BA120 VISION | `0416:5302` | HID Type 2 | High |
| FROZEN WARFRAME (HID variant) | `0416:5302` | HID Type 2 | Medium |
| FROZEN WARFRAME SE (HID variant) | `0416:5302` | HID Type 2 | Medium |
| FROZEN WARFRAME PRO | `0416:5302` | HID Type 2 | Medium |
| ELITE VISION | `0416:5302` | HID Type 2 | Medium |
| LC5 (HID variant) | `0416:5302` | HID Type 2 | Medium |
| TARAN ARMS | `0418:5303` | HID Type 3 | High — only Type 3 device |
| TARAN ARMS | `0418:5304` | HID Type 3 | High — only Type 3 device |

## Need Testers — LED RGB Devices

These use PID `0416:8001` for RGB LED ring control. The device model is auto-detected via a HID handshake PM byte. Many models share this PID.

**Standard LED devices (FormLED in Windows):**

| Device | PM Byte | LED Style | Status |
|--------|---------|-----------|--------|
| AX120 DIGITAL | 3 | Style 1 (30 LEDs, 10 segments) | Confirmed working |
| PA120 DIGITAL | 16 | Style 2 (84 LEDs, 18 segments) | LED detected, untested style |
| Peerless Assassin 120 DIGITAL | — | — | LED detected, handshake failed |
| RK120 DIGITAL | 23 | Style 2 | Untested |
| AK120 DIGITAL | 32 | Style 3 (64 LEDs, 10 segments) | Untested |
| FROZEN HORIZON PRO (LED) | 1 | Style 1 | Untested |
| FROZEN MAGIC PRO (LED) | 2 | Style 1 | Untested |
| HR10 2280 PRO Digital | 128 | Style 4 (31 LEDs, 14 segments) | Confirmed working |
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

> If you have any of these Vision-series devices, a `trcc hid-debug` dump would be extremely valuable — it will help us figure out how Windows distinguishes these from the standard LED devices.

## All TRCC-Compatible Models (from Thermalright's download page)

The following models are listed on [Thermalright's official download page](https://www.thermalright.com/support/download/) as compatible with TRCC software. They all use the same USB controllers we already support — we just need testers to confirm which USB ID and protocol each one uses.

**Models not yet tested on Linux (USB ID unknown — need `lsusb` output):**

| Device | Likely Protocol | Status |
|--------|----------------|--------|
| Assassin Spirit 120 Vision | HID LCD or LED | Need tester |
| Burst Assassin 120 Vision | HID LCD or LED | Need tester |
| Core Matrix VISION | HID LCD or LED | Need tester |
| Core Vision | HID LCD or LED | Need tester |
| Hyper Vision | HID LCD or LED | Need tester |
| Levita Vision | HID LCD or LED | Need tester |
| Magic Qube | HID LCD or LED | Need tester |
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

**Separate software (TR-VISION HOME):**

| Device | Status |
|--------|--------|
| Frozen Vision (original) | Different software — may use different protocol |
| Frozen Guardian | Different software — may use different protocol |

> A single `lsusb` line from any of these devices tells us exactly which protocol it uses and whether it should work out of the box.

## Not Sure If Your Device Is Listed?

Run `lsusb` and look for `0416`, `0418`, `87cd`, or `0402` in the output. If you see a match, [open an issue](https://github.com/Lexonight1/thermalright-trcc-linux/issues/new) — even if your exact model isn't listed above, it likely shares the same USB controller.
