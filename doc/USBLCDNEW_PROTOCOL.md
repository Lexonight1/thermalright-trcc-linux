# USBLCDNEW.exe — USB Bulk Protocol Reference

Decompiled from `USBLCDNEW.exe` (.NET 6.0 / C#) via ILSpy.
This binary handles **non-SCSI LCD devices** on Windows via LibUsbDotNet raw USB bulk transfers.

For SCSI devices (`0402:3922`), see `USBLCD_PROTOCOL.md`.

## Overview

```
Windows:  TRCC.exe ──shared memory──> USBLCDNEW.exe ──LibUsbDotNet──> USB Bulk EP01/EP02
Linux:    trcc ──────────────────────────────────────> sg_raw ────────> /dev/sgX (SCSI)
```

USBLCDNEW.exe uses raw USB bulk transfers via LibUsbDotNet/WinUSB — it does NOT use SCSI pass-through. On Linux, the same devices appear as SCSI generic (`/dev/sgX`) because `usb-storage` kernel driver binds first, providing a SCSI translation layer. Both paths deliver the same commands to the device firmware.

## Binary Details

| Property | Value |
|---|---|
| Type | .NET 6.0 (C#), clean decompilation |
| Version | 2.3.0 |
| Size | ~700 lines decompiled |
| USB API | LibUsbDotNet (→ WinUSB/libusb) |
| Source | `/home/ignorant/Downloads/TRCCCAPEN/decompiled/USBLCDNEW.decompiled.cs` |

## Supported Devices

| VID:PID | Decimal | Handler | Protocol | Endpoints |
|---|---|---|---|---|
| `87CD:70DB` | 34733:28891 | `ThreadSendDeviceData` | Magic `0x12345678` | EP01 IN, EP01 OUT |
| `0416:5302` | 1046:21250 | `ThreadSendDeviceDataH` | DA/DB/DC/DD handshake | EP01 IN, EP02 OUT |
| `0416:5406` | 1046:21510 | `ThreadSendDeviceDataALi` | 0xF5 SCSI-like | EP01 IN, EP02 OUT |

Note: The `H` suffix refers to the Nuvoton/HID-capable chipset variant. The `ALi` suffix refers to the ALi Corp chipset (same vendor as SCSI devices but different PID).

## Command Type Constants

```csharp
SSCRM_CMD_TYPE_DEV_INFO   = 1   // Device information query
SSCRM_CMD_TYPE_PICTURE    = 2   // Picture/frame data
SSCRM_CMD_TYPE_LOGO       = 3   // Logo/boot screen
SSCRM_CMD_TYPE_OTA        = 4   // Over-the-air update
SSCRM_CMD_TYPE_UPG_STATE  = 5   // Upgrade state
SSCRM_CMD_TYPE_ROTATE     = 6   // Rotation control
SSCRM_CMD_TYPE_SCR_SET    = 7   // Screen settings
SSCRM_CMD_TYPE_BKL_SET    = 8   // Backlight/brightness
SSCRM_CMD_TYPE_LOGO_STATE = 9   // Logo state
```

These constants are defined in the class but used by TRCC.exe when preparing command packets in shared memory. USBLCDNEW.exe itself just forwards whatever TRCC.exe puts in the shared memory buffer.

## Shared Memory (IPC with TRCC.exe)

### Memory Layout

```
Name:       "shareMemory_ImageRGB"
Total size: 34,560,000 bytes (50 slots × 691,200 bytes/slot)
Slot size:  691,200 bytes
Max devices: 10 (arrayDeviceOnline[10])
```

Each device gets **2 slots** (indexed as `n*2` and `n*2+1`):
- Slot `n*2`: Control bytes (4 bytes) and device info (written back after handshake)
- Slot `n*2+1`: Frame data (up to 691,200 bytes)

### Control Bytes (Slot n*2, offset 0)

| Bytes | Meaning |
|---|---|
| `00 01 01 xx` | Send trigger — device ready, USBLCDNEW reads frame from slot n*2+1 |
| `AA BB CC DD` | Shutdown signal — USBLCDNEW stops all threads |
| `xx xx 00 xx` | Idle — USBLCDNEW polls at 1ms intervals |

After processing a send trigger, USBLCDNEW clears byte[2] to 0.

---

## Protocol 1: Thermalright LCD (`87CD:70DB`)

### USB Configuration

```
Endpoint Write: EP01 OUT
Endpoint Read:  EP01 IN
```

### Handshake

Send 64 bytes:
```
Offset  Value
0-3     12 34 56 78    (magic: 0x12345678)
4-55    00 00 ...      (zeros)
56      01             (command: device info query)
57-63   00 00 ...      (zeros)
```

Read 1024 bytes response. Check `response[24] != 0` for valid device.

### Device Info (Written Back to Shared Memory)

9-byte header + device name:

| Byte | Source | Purpose |
|---|---|---|
| 0 | response[32] | PM (product mode) byte |
| 1 | response[36] | Unknown |
| 2 | 0x48 ('H') | Hardcoded type marker |
| 3 | response[40] | Unknown |
| 4 | response[24] | Device present flag |
| 5 | response[28] | Unknown |
| 6 | 0xDC (220) | Protocol marker |
| 7 | response[20] | Unknown |
| 8 | name length | Device name string length |
| 9+ | name bytes | UTF-8 device name |

### Frame Send

1. Read 691,200 bytes from shared memory slot `n*2+1`
2. Extract data length from bytes[60:63] (LE uint32) + 64 = total transfer size
3. Send via async USB bulk write to EP01 OUT
4. If transfer size is exact multiple of 512, send zero-length packet (USB bulk transfer protocol requirement)
5. Sleep 15ms

The 64-byte offset suggests the frame data has a 64-byte internal header (prepared by TRCC.exe) followed by the actual pixel data.

---

## Protocol 2: Nuvoton HID LCD (`0416:5302`)

### USB Configuration

```
Endpoint Write: EP02 OUT    ← NOTE: different endpoint than 87CD
Endpoint Read:  EP01 IN
```

### Handshake (DA/DB/DC/DD)

Send 512 bytes:
```
Offset  Value
0-3     DA DB DC DD    (magic handshake)
4-11    00 00 ...      (zeros)
12      01             (command: device info)
13-19   00 00 ...      (zeros)
20-511  00 00 ...      (zeros, padding to 512)
```

Read 512 bytes response. Validate:
```
response[0]  == 0xDA
response[1]  == 0xDB
response[2]  == 0xDC
response[3]  == 0xDD    (echo of handshake magic)
response[12] == 0x01    (success)
response[16] == 0x10    (16 = data present flag)
```

**This is the same DA/DB/DC/DD handshake used in our `hid_device.py`.**

### Device Info (Written Back to Shared Memory)

Serial number extracted from response bytes 20-35 as hex string.

9-byte header + serial hex:

| Byte | Source | Purpose |
|---|---|---|
| 0 | response[4] | PM (product mode) byte |
| 1 | response[5] | SUB byte |
| 2 | 0x36 (54) | Hardcoded FBL marker |
| 3 | response[4] | PM (repeated) |
| 4 | response[5] | SUB (repeated) |
| 5 | 0xDC (220) | Protocol marker |
| 6 | 0xDC (220) | Protocol marker |
| 7 | 0xDC (220) | Protocol marker |
| 8 | serial length | Serial hex string length |
| 9+ | serial bytes | UTF-8 hex serial number |

The FBL value 0x36 (54) maps to 360×360 resolution in TRCC.exe's FBL table. But this may not be the actual FBL — it could be a default that gets overridden by the PM/SUB bytes.

### Frame Send

1. Read frame data from shared memory slot `n*2+1`
2. Extract data length from bytes[16:19] (LE uint32) + 20 = total size
3. Round up to next 512-byte boundary: `(size / 512 * 512) + ((size % 512 != 0) ? 512 : 0)`
4. Send via synchronous USB bulk write to EP02 OUT
5. Sleep 1ms

The 20-byte offset suggests the frame data has a 20-byte internal header (the DA/DB/DC/DD protocol header, prepared by TRCC.exe) followed by pixel data. The 512-byte alignment is required for USB bulk transfers.

---

## Protocol 3: ALi Corp LCD (`0416:5406`)

### USB Configuration

```
Endpoint Write: EP02 OUT    ← Same as 0416:5302
Endpoint Read:  EP01 IN
```

### Handshake (SCSI-like 0xF5)

Send 16 + 1024 = 1040 bytes:
```
Header (16 bytes):
Offset  Value
0       F5             (SCSI protocol marker)
1       00             (sub-command: poll)
2       01             (mode flag)
3       00
4-7     BC FF B6 C8    (magic/checksum)
8-11    00 00 00 00
12-15   00 04 00 00    (data size: 0x0400 = 1024)

Payload (1024 bytes):
All zeros
```

Read 1024 bytes response. Check:
```
response[0] == 'e' (0x65) → 320×320 display
response[0] == 'f' (0x66) → possibly 480×480 or other resolution
```

**Same resolution codes as USBLCD.exe** (`'d'`/`'e'` = 320×320), but `'f'` (0x66) is new and not seen in USBLCD.exe.

### Device Info (Written Back to Shared Memory)

Device identifier from response bytes 10-13 as hex string.

9-byte header + identifier hex:

| Byte | Source | Purpose |
|---|---|---|
| 0 | response[0] - 1 | Resolution code ('d' or 'e') |
| 1 | response[10] | ID byte 0 |
| 2 | response[11] | ID byte 1 |
| 3 | response[12] | ID byte 2 |
| 4 | response[13] | ID byte 3 |
| 5 | 0xDC (220) | Protocol marker |
| 6 | 0xDD (221) | Protocol marker (ALi variant) |
| 7 | 0xDC (220) | Protocol marker |
| 8 | ID hex length | Identifier string length |
| 9+ | ID hex bytes | UTF-8 hex identifier |

Note: `response[0] - 1` converts `'e'` (0x65) → `'d'` (0x64) and `'f'` → `'e'`, matching the USBLCD.exe format.

### Frame Send

Send 16 + 204,800 = 204,816 bytes as one USB bulk write:
```
Header (16 bytes):
Offset  Value
0       F5             (SCSI protocol marker)
1       01             (sub-command: write)
2       01             (mode: frame data)
3       00             (chunk index: 0)
4-7     BC FF B6 C8    (magic/checksum — same as poll)
8-11    00 00 00 00
12-15   00 20 03 00    (data size: 0x32000 = 204,800 = 320×320×2)

Payload (204,800 bytes):
RGB565 pixel data
```

After write, reads back 16 bytes (acknowledgment from device).

**Key difference from USBLCD.exe:** The entire frame is sent as ONE bulk transfer (header + all pixel data), NOT chunked into 64 KiB pieces. The SCSI path chunks because SCSI CDB can only describe one transfer at a time, but raw USB bulk has no such limitation.

### Magic Bytes `BC FF B6 C8`

These appear at bytes[4:7] in both poll and frame headers. They may be:
- A constant protocol identifier
- A CRC/checksum of something (but same value for different commands, so unlikely)
- A firmware version marker

They are NOT the CRC32 from USBLCD.exe (which goes at bytes[16:19]).

---

## Protocol Comparison

### Device Detection

| Binary | 87CD:70DB | 0416:5302 | 0416:5406 | 0402:3922 |
|---|---|---|---|---|
| USBLCD.exe | ✗ | ✗ | ✓ (SCSI) | ✓ (SCSI) |
| USBLCDNEW.exe | ✓ (USB) | ✓ (USB) | ✓ (USB) | ✗ |

Note: `0416:5406` is handled by BOTH binaries — USBLCDNEW.exe via raw USB, USBLCD.exe via SCSI pass-through. In practice, only one claims the device (WinUSB takes priority over SCSI).

### Handshake Comparison

| Device | Magic | Send Size | Read Size | Key Response Bytes |
|---|---|---|---|---|
| 87CD:70DB | `12 34 56 78` | 64 | 1024 | [24]=present, [32]=PM |
| 0416:5302 | `DA DB DC DD` | 512 | 512 | [4]=PM, [5]=SUB, [12]=OK, [16]=0x10 |
| 0416:5406 | `F5 00 01 00` | 1040 | 1024 | [0]='e'/'f' (resolution) |

### Frame Send Comparison

| Device | Header Size | Payload | Total | Chunked? | ACK? |
|---|---|---|---|---|---|
| 87CD:70DB | 64 | from bytes[60:63] | varies | No (single) | No |
| 0416:5302 | 20 | from bytes[16:19] | 512-aligned | No (single) | No |
| 0416:5406 | 16 | 204,800 (320×320) | 204,816 | No (single) | Yes (16 bytes) |

### trcc-linux Implementation Mapping

| Feature | USBLCDNEW.exe | trcc-linux | Status |
|---|---|---|---|
| 87CD:70DB via SCSI | N/A (uses USB) | ✓ `scsi_device.py` | Working (SCSI transport) |
| 0416:5302 DA/DB/DC/DD | ✓ | ✓ `hid_device.py` | Matches exactly |
| 0416:5406 via SCSI | N/A (uses USB) | ✓ `scsi_device.py` | Working (SCSI transport) |
| 0416:5406 frame (USB) | ✓ (single bulk) | ✓ (chunked SCSI) | Same data, different transport |
| Device info writeback | ✓ (shared memory) | ✓ (device_detector) | Equivalent |

---

## Architectural Insight

On Windows, there are two separate USB transport paths running simultaneously:
- **USBLCD.exe** → SCSI pass-through → `DeviceIoControl` → commands wrapped in SCSI CDB
- **USBLCDNEW.exe** → LibUsbDotNet/WinUSB → raw USB bulk transfers → direct commands

On Linux, there is only one:
- **trcc** → `sg_raw` → usb-storage SCSI Generic → same commands in SCSI CDB wrapper

The device firmware accepts the same protocol commands regardless of whether they arrive via SCSI CBW (Command Block Wrapper) or raw USB bulk. The Linux usb-storage driver handles the SCSI-to-USB translation transparently.

For HID devices (`0416:5302`, `0416:8001`, etc.), Linux uses PyUSB/HIDAPI directly via `hid_device.py` — matching the USBLCDNEW.exe approach.
