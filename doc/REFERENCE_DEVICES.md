# Supported Devices

## Confirmed Working

These devices have been tested on real hardware and confirmed working with TRCC Linux.

### Full LCD Screen (Custom Themes, Images, Videos, Overlays)

| Product | Connection | Screen | Tested By |
|---------|-----------|--------|-----------|
| FROZEN HORIZON PRO | SCSI (87CD:70DB) | 320x320 | Developer |
| FROZEN MAGIC PRO | SCSI (87CD:70DB) | 320x320 | Developer |
| FROZEN VISION V2 | SCSI (87CD:70DB) | 320x320 | Developer |
| FROZEN WARFRAME | SCSI (0402:3922) | 320x320 | Developer |
| FROZEN WARFRAME 240 | SCSI (0402:3922) | 320x240 | [gizbo](https://github.com/gizbo) |
| FROZEN WARFRAME SE | HID (0416:5302) | 320x240 | [apj202-ops](https://github.com/apj202-ops) |
| FROZEN WARFRAME 360 | HID (0416:5302) | — | [Edoardo-Rossi-EOS](https://github.com/Edoardo-Rossi-EOS), [edoargo1996](https://github.com/edoargo1996), [stephendesmond1-cmd](https://github.com/stephendesmond1-cmd) |
| LC1, LC2, LC3, LC5 | SCSI (0416:5406) | 320x320 | Developer |
| GrandVision 360 AIO | Bulk (87AD:70DB) | 480x480 | [bipobuilt](https://github.com/bipobuilt), [cadeon](https://github.com/cadeon) |
| Mjolnir Vision 360 | Bulk (87AD:70DB) | 480x480 | [Pikarz](https://github.com/Pikarz) |
| Wonder Vision Pro 360 | Bulk (87AD:70DB) | — | [Civilgrain](https://github.com/Civilgrain) |
| Trofeo Vision LCD | HID (0416:5302) | 1280x480 | [PantherX12max](https://github.com/PantherX12max), [N8ghtz](https://github.com/N8ghtz) |
| Assassin Spirit 120 Vision ARGB | HID (0416:5302) | 240x240 | [michael-spinelli](https://github.com/michael-spinelli), [acioannina-wq](https://github.com/acioannina-wq) |
| Elite Vision 360 ARGB Black | SCSI (0402:3922) | — | [tensaiteki](https://github.com/tensaiteki) |
| Frozen Warframe Pro | Bulk (87AD:70DB) | — | [loosethoughts19-hash](https://github.com/loosethoughts19-hash) |
| Trofeo Vision 9.16 LCD | LY (0416:5408) | — | [Mr-Renegade](https://github.com/Mr-Renegade) |

### LED + Segment Display (RGB Fan Control, Temperature Readout)

| Product | Connection | Tested By |
|---------|-----------|-----------|
| AX120 Digital | HID (0416:8001) | [shadowepaxeor-glitch](https://github.com/shadowepaxeor-glitch), [hexskrew](https://github.com/hexskrew) |
| Assassin X 120R Digital ARGB | HID (0416:8001) | [hexskrew](https://github.com/hexskrew) |
| Peerless Assassin 120 Digital ARGB White | HID (0416:8001) | [Xentrino](https://github.com/Xentrino) |
| Phantom Spirit 120 Digital EVO | HID (0416:8001) | [javisaman](https://github.com/javisaman), [Rizzzolo](https://github.com/Rizzzolo) |
| HR10 2280 PRO Digital | HID (0416:8001) | [Lcstyle](https://github.com/Lcstyle) |
| Magic Qube | HID (0416:8001) | [jphilipb](https://github.com/jphilipb) |

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
| Mjolnir Vision PRO | 雷神之锤 PRO |
| Elite Vision | 精英视界 |
| Hyper Vision | 终越视界 |
| Stream Vision | 风擎视界 |
| Rainbow Vision | 彩虹视界 |
| Peerless Vision | 无双视界 |
| Levita Vision | 悠浮视界 |
| TL-M10 VISION | — |
| TR-A70 Vision | — |
| AS120 VISION | — |
| BA120 VISION | — |
| Burst Assassin 120 Vision | — |
| Peerless Assassin 120 Vision | — |
| Royal Lord 120 Vision | — |
| Royal Knight 130 Vision | — |
| Phantom Spirit 120 Vision / EVO | 幻灵 120 Vision |
| Phantom Spirit 120 Vision EVO | Bulk (87AD:70DB, PM 4 SUB 4 A1LF10V) | [alan7383](https://github.com/alan7383) |

### LED + Segment Display Products (Digital Series)

These have a small digital display showing CPU/GPU temperature plus addressable RGB LED fans.

| Product |
|---------|
| Peerless Assassin 140 Digital |
| Frozen Magic Digital |
| Royal Knight 120 Digital |
| Royal Knight 130 Digital |
| MC-3 DIGITAL |

---

## USB Interfaces

All devices connect through one of these USB VID:PIDs:

| VID:PID | Protocol | Display | Products |
|---------|----------|---------|----------|
| 87CD:70DB | SCSI | Full LCD | Older LCD screens |
| 87AD:70DB | Bulk | Full LCD | GrandVision 360 AIO, Mjolnir Vision 360 |
| 0402:3922 | SCSI | Full LCD | Frozen Warframe series (360/SE/PRO/Ultra) |
| 0416:5406 | SCSI | Full LCD | Winbond LCD variant |
| 0416:5302 | HID Type 2 | Full LCD | Vision/Warframe (newer HW) |
| 0418:5303 | HID Type 3 | Full LCD | TARAN ARMS |
| 0418:5304 | HID Type 3 | Full LCD | TARAN ARMS |
| 0416:5408 | LY Bulk | Full LCD | Trofeo Vision 9.16 LCD |
| 0416:5409 | LY1 Bulk | Full LCD | |
| 0416:8001 | HID | LED + segment / Full LCD | Digital series + many Vision products |

The exact product model is identified after a USB handshake. The device responds with PM (product model) and SUB bytes that tell the app which product it is and whether to show the LCD or LED control panel.

## How to Help Test

If you own any of the untested devices above and run Linux:

1. Install: `pip install trcc-linux`
2. Run the setup wizard: `trcc system setup` (checks deps, installs udev rules, desktop entry)
3. Unplug/replug USB cable
4. Run detection: `trcc detect`
5. Try the GUI: `trcc gui`
6. Report what you see at https://github.com/Lexonight1/thermalright-trcc-linux/issues
