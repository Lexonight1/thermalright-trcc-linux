# Supported Devices

## Supported (Tested & Working)

These devices have been tested on real hardware and are confirmed working with TRCC Linux.

### Full LCD Screen (Custom Themes, Images, Videos, Overlays)

| Product | Connection | Screen |
|---------|-----------|--------|
| Frozen Warframe series (SE, PRO, Ultra) | SCSI (0402:3922) | 320x320 |
| Thermalright LCD Display | SCSI (87CD:70DB) | 320x320 |
| GrandVision 360 AIO | SCSI (87AD:70DB) | 320x320 |
| Winbond LCD Display | SCSI (0416:5406) | 320x320 |

---

## Supported (Tester-Reported)

These devices have been reported by testers with varying levels of validation.

### HID LCD Devices

| Product | Connection | Issue | Status |
|---------|-----------|-------|--------|
| Assassin Spirit 120 Vision ARGB | HID (0416:5302) | [#16](https://github.com/Lexonight1/thermalright-trcc-linux/issues/16) | Detected, awaiting v2.0.0 retest |
| Mjolnir Vision 360 | Bulk (87AD:70DB) | [#22](https://github.com/Lexonight1/thermalright-trcc-linux/issues/22) | Same VID:PID as GrandVision, awaiting retest |

### LED + Segment Display (RGB Fan Control, Temperature Readout)

| Product | Connection | Issue | Status |
|---------|-----------|-------|--------|
| Assassin X 120 R Digital | HID (0416:8001) | [#5](https://github.com/Lexonight1/thermalright-trcc-linux/issues/5) | LED RGB working |
| HR10 2280 PRO Digital | HID (0416:8001) | [#1](https://github.com/Lexonight1/thermalright-trcc-linux/issues/1) | Fully supported — 7-segment display, NVMe temp daemon, color wheel |
| Peerless Assassin 120 Digital ARGB White | HID (0416:8001) | [#15](https://github.com/Lexonight1/thermalright-trcc-linux/issues/15) | LED partially working, awaiting retest |
| Phantom Spirit 120 Digital EVO | HID (0416:8001) | [#19](https://github.com/Lexonight1/thermalright-trcc-linux/issues/19) | Detected, awaiting v2.0.0 retest |

---

## Need Testers

These products are recognized by the Windows TRCC app and should work with TRCC Linux. If you own one of these, we'd love your help testing — run `trcc report` and [open an issue](https://github.com/Lexonight1/thermalright-trcc-linux/issues).

### Full LCD Screen Products (Vision Series)

These have a full pixel LCD (240x240 to 1920x462) for custom themes, images, videos, and sensor overlays.

| Product | Chinese Name |
|---------|-------------|
| Frozen Vision V2 | 冰封视界 V2 |
| Core Vision | 核芯视界 |
| Core Matrix VISION | 矩阵视界 |
| Mjolnir Vision | 雷神之锤 |
| Mjolnir Vision PRO | 雷神之锤 PRO |
| Elite Vision | 精英视界 |
| Grand Vision | — |
| Hyper Vision | 终越视界 |
| Stream Vision | 风擎视界 |
| Trofeo VISION | 纵横视界 |
| Wonder Vision | 奇幻视界 |
| Rainbow Vision | 彩虹视界 |
| Peerless Vision | 无双视界 |
| Levita Vision | 悠浮视界 |
| TL-M10 VISION | — |
| TR-A70 Vision | — |
| AS120 VISION | — |
| BA120 VISION | — |
| Assassin Spirit 120 Vision | — |
| Burst Assassin 120 Vision | — |
| Peerless Assassin 120 Vision | — |
| Royal Lord 120 Vision | — |
| Royal Knight 130 Vision | — |
| Phantom Spirit 120 Vision | — |
| Magic Qube | — |

### LED + Segment Display Products (Digital Series)

These have a small digital display showing CPU/GPU temperature plus addressable RGB LED fans.

| Product |
|---------|
| Peerless Assassin 120 Digital |
| Peerless Assassin 140 Digital |
| Frozen Magic Digital |
| Royal Knight 120 Digital |
| Royal Knight 130 Digital |
| Phantom Spirit 120 Digital |
| ~~HR10-2280 PRO Digital~~ (fully supported — see above) |
| MC-3 DIGITAL |

---

## USB Interfaces

All devices connect through one of these USB VID:PIDs:

| VID:PID | Protocol | Display | Products |
|---------|----------|---------|----------|
| 87CD:70DB | SCSI | Full LCD | Older LCD screens |
| 87AD:70DB | SCSI | Full LCD | GrandVision 360 AIO |
| 0402:3922 | SCSI | Full LCD | Frozen Warframe series (SE/PRO/Ultra) |
| 0416:5406 | SCSI | Full LCD | Winbond LCD variant |
| 0416:52E2 | HID | Full LCD | Vision/Warframe (newer HW) |
| 0418:52E3 | HID | Full LCD | ALi Corp LCD variant |
| 0418:52E4 | HID | Full LCD | ALi Corp LCD variant |
| 0416:8001 | HID | LED + segment / Full LCD | Digital series + many Vision products |

The exact product model is identified after a USB handshake. The device responds with PM (product model) and SUB bytes that tell the app which product it is and whether to show the LCD or LED control panel.

## How to Help Test

If you own any of the untested devices above and run Linux:

1. Install: `pip install trcc-linux`
2. Set up udev rules: `sudo trcc setup-udev` (then unplug/replug USB cable)
4. Run detection: `trcc detect --all`
5. Try the GUI: `trcc gui` (HID devices are auto-detected)
6. Report what you see at https://github.com/Lexonight1/thermalright-trcc-linux/issues
