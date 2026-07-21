# AUDIT — Device Discovery & Connection (TRCC 2.1.6 C# decompile)

Sources (verbatim, line-cited):
- `~/Downloads/TRCCCAPEN/TRCC_decompiled/TRCC/Form1.cs` (1,734 lines)
- `~/Downloads/TRCCCAPEN/TRCC_decompiled/TRCC/UCDevice.cs` (1,438 lines)

Every claim below quotes the exact source line(s). Where a value lives in a Form
not in scope (`FormCZTV`, `FormLED`, `FormSystemInfo`, external `USBLCD*.exe`), it
is flagged **out-of-scope** — not inferred.

---

## 1. Purpose & lifecycle

### 1.1 The two device transports are architecturally split
TRCC discovers devices through **two entirely separate channels**:

1. **HID channel** — owned by `UCDevice` (`UsbHidDevice` objects `device1..4`).
   In-process enumeration, connect, handshake, data receive.
2. **Shared-memory channel** — owned by `Form1`, fed by **external processes**
   `USBLCD.exe` / `USBLCDNEW.exe` that own the SCSI/SPI LCD wire and write frames
   into named memory-mapped files. `Form1` polls that shared memory and
   synthesizes `FormCZTV` windows from magic bytes.

### 1.2 Startup sequence — `Form1()` ctor
`Form1.cs:272-317`:
```csharp
InitializeComponent();
ClearButtonBouns();
KaijiQidong();                              // autostart registry
CheckDirectoryExist();
InitMemorySize();                           // "shareMemory_Image"    38400000
WriteShareMemory(0, new byte[4], 4);
InitMemorySizeRGB();                        // "shareMemory_ImageRGB" 34560000
WriteShareMemoryRGB(0, new byte[4], 4);
InitializeForm();
formDeviceArray = new ArrayList();
formCZTVArray = new ArrayList();
m_timer = new Timer();
m_timer.Tick += Timer_event;
m_timer.Enabled = true;
m_timer.Interval = 15;
m_timer.Stop();
// ... kills any stray "USBLCD" processes ...
formStart = new FormStart();
// ...
ucDevice1.Scan_UsbHid();                    // kicks off HID enumeration
DelegateUCAbout(32, Language, null);
m_timer.Start();
SystemEvents.PowerModeChanged += new PowerModeChangedEventHandler(OnPowerModeChanged);
```

Shared-memory sizes/names — `Form1.cs:68-90`:
```csharp
public const int shareMemorySize0 = 153600;                 // 320*240*2 = 153600 (one SPI/SCSI frame)
private const int shareMemorySize = 38400000;
private const string shareMemoryName = "shareMemory_Image";
public const int shareMemorySize1 = 691200;                 // 480*480*3 = 691200 (one RGB frame)
private const int shareMemorySizeRGB = 34560000;
private const string shareMemoryNameRGB = "shareMemory_ImageRGB";
```

### 1.3 The 15 ms master timer fans out to three pumps
`Form1.cs:813-818`:
```csharp
private void Timer_event(object sender, EventArgs e)
{
    Timer_Form_event();      // ticks each open FormLED / FormCZTV
    Timer_One_LCD_event();   // (re)launches USBLCD*.exe when resuming from sleep
    Timer_RGB_LCD_event();   // polls shared memory → synthesizes FormCZTV
}
```

### 1.4 Hotplug — Windows `WM_DEVICECHANGE`
`Form1.cs:359-372`:
```csharp
protected override void WndProc(ref Message m)
{
    try
    {
        if (((Message)(ref m)).WParam.ToInt32() == 7)   // DBT_DEVNODES_CHANGED
        {
            ucDevice1.Scan_UsbHid();
        }
    }
    catch { }
    ((Form)this).WndProc(ref m);
}
```
`WParam == 7` is `DBT_DEVNODES_CHANGED`; a USB arrival/removal re-triggers the HID
scan. **This is the only OS hotplug hook; the shared-memory LCD channel has no
hotplug — it is discovered by continuous polling (§4).**

### 1.5 Power / sleep handling
`Form1.cs:319-357` — `OnPowerModeChanged`. `switch (val - 1)`: `case 0` (Suspend)
sets `myPowerMode = true`; `case 2` (Resume) kills `USBLCD` processes, sets
`myPowerMode = false`, calls `ResetAllDevice()`. On resume the LCD helper
processes are re-spawned by `Timer_One_LCD_event` (`Form1.cs:590-654`, gated on
`myPowerMode` + a 128-tick counter).

---

## 2. VID/PID table & wire routing (the core answer)

All four HID devices are constructed in `UCDevice.Timer_event`, `UCDevice.cs:229-272`:
```csharp
device1 = new UsbHidDevice(1046, 32769, hidNameList1, 64);   // VID 0x0416  PID 0x8001  report=64
device2 = new UsbHidDevice(1046, 21250, hidNameList2);       // VID 0x0416  PID 0x5302  report=512(default)
device3 = new UsbHidDevice(1048, 21251, hidNameList3, 64);   // VID 0x0418  PID 0x5303  report=64
device4 = new UsbHidDevice(1048, 21252, hidNameList4);       // VID 0x0418  PID 0x5304  report=512(default)
```

Decimal→hex: 1046=`0x0416`, 1048=`0x0418`, 32769=`0x8001`, 21250=`0x5302`,
21251=`0x5303`, 21252=`0x5304`.

| Slot | VID | PID | report len | list | ID | Device kind | Form opened |
|------|-----|-----|-----------|------|----|-----|-----|
| device1 | 0x0416 | 0x8001 | 64 | hidList1 | 1 | **LED** (RGB / segment) | `FormLED` |
| device2 | 0x0416 | 0x5302 | 512 | hidList2 | 2 | **LCD (HID wire)** | `FormCZTV` (mode 3) |
| device3 | 0x0418 | 0x5303 | 64 | hidList3 | 3 | (routed, no Form body) | none — case empty |
| device4 | 0x0418 | 0x5304 | 512 | hidList4 | 4 | (routed, no Form body) | none — case empty |

The `ID` (1/2/3/4) is the routing key everywhere; the fifth kind, **ID `257`**
(`USB_ID1_1 = 257`, `UCDevice.cs:48`), is the **shared-memory RGB LCD** added by
`RGB_ADD_Device` (`UCDevice.cs:1326-1330`), not a HID device.

**Caveat — ID 3 & 4 are enumerated but inert in these two files.** `Form1`
`DelegateUCDevice` `case 1` only builds a Form for `info==1` (LED) and `info==2`
(LCD); `Form1.cs:1198-1200`:
```csharp
else if ((int)info != 3 && (int)info != 4)
{
}
```
`SendDeviceData3/4` are empty bodies (`UCDevice.cs:1235-1237`, `1277-1279`).

### 2.1 report-length constants
`UCDevice.cs:74-76`:
```csharp
private const int USB_LEN0 = 64;
private const int USB_LEN = 512;
```
device1/device3 use the 64-byte report; device2/device4 default (512-byte path,
see chunking §3.4).

---

## 3. Handshake dispatch per wire

### 3.1 HID scan lifecycle — `UCDevice.Scan_UsbHid` + poll timer
`UCDevice.cs:173-179`:
```csharp
public void Scan_UsbHid(bool bl = true)
{
    scanCount = 0;
    timerStartCount = 0;
    timerStartCountVal = 20;
    timerStart = bl;
}
```
100 ms `System.Timers.Timer` (`UCDevice.cs:161-164`). `Timer_event`
(`UCDevice.cs:181-276`) counts up to `timerStartCountVal`, does up to 3 scan
passes (`scanCount` 1→val=2, 2→val=30, `>=3`→stop & dispose all four devices),
otherwise constructs/`Connect()`s each `deviceN`. After a successful add the poll
value is bumped to keep-alive `300` (`DeviceDataReceived1:960`,
`DeviceDataReceived2:1083`).

### 3.2 LED (device1, ID 1) — connect handshake
`UCDevice.cs:925-939` `DeviceOnConnected1`:
```csharp
byte[] parameters = new byte[20]
{
    218, 219, 220, 221, 0, 0, 0, 0, 0, 0,
    0, 0, 1, 0, 0, 0, 0, 0, 0, 0
};
CommandMessage message = new CommandMessage(parameters);
device1.SendMessage(message);
```
Magic prefix = `0xDA 0xDB 0xDC 0xDD`, byte[12]=`1`.

### 3.3 LED (device1) — response fingerprint
`UCDevice.cs:954-981` `DeviceDataReceived1`. Enqueues `4096`+data. Name/serial:
```csharp
if (data[17] == 16 && data.Length >= 37)
{
    string value = BitConverter.ToString(data, 21).Replace("-", "");   // serial from data[21..]
    arrayList.Add(value);
}
else { /* fall back to sanitized DevicePath */ }
```
The **pm/sub fingerprint** is consumed in `AddhidDeviceList` (§3.6): `receive[6]`
= pm, `receive[5]` = sub.

### 3.4 LCD-HID (device2, ID 2) — connect handshake
`UCDevice.cs:1045-1064` `DeviceOnConnected2`:
```csharp
byte[] parameters = new byte[512];
CommandMessage commandMessage = new CommandMessage(parameters);
for (int i = 0; i < 400; i++) { }          // busy-wait spacer
byte[] parameters2 = new byte[20]
{
    218, 219, 220, 221, 0, 0, 0, 0, 0, 0,
    0, 0, 1, 0, 0, 0, 0, 0, 0, 0
};
CommandMessage message = new CommandMessage(parameters2);
device2.SendMessage(message);
```
Same 20-byte `0xDA DB DC DD … [12]=1` probe as LED, preceded by a 512-byte zero
frame + a 400-iteration spin.

### 3.5 LCD-HID (device2) — response validation & fingerprint
`UCDevice.cs:1079-1125` `DeviceDataReceived2`. **Validated magic offsets differ
from LED — here the prefix is at data[1..4], not data[0..3]:**
```csharp
if (data.Length != 0 && device2 == device
    && data[1] == 218 && data[2] == 219 && data[3] == 220 && data[4] == 221
    && data[13] == 1)                       // == connect/handshake reply
{ ... enqueue 4097 + data ... }
else if (... data[13] != 8 ...) return;     // data[13]==8 = live data frame
```
`data[13]==1` = handshake reply → device add; `data[13]==8` = runtime device
data → routed as event `8193` with `data[9]` set to the list index
(`UCDevice.cs:1111-1123`).

### 3.6 The device-add ACK + pm/sub extraction — `AddhidDeviceList`
`UCDevice.cs:813-853`:
```csharp
byte[] obj = new byte[14]
{
    220, 221, 1, 0, 0, 0, 0, 0, 0, 0,
    0, 1, 0, 0
};
obj[4]  = (byte)ID;
obj[5]  = (byte)(ID >> 8);
obj[10] = (byte)array.Count;
byte[] parameters = obj;
CommandMessage message = new CommandMessage(parameters);
if (ID == 4)
{
    device.SendMessage(message);
    ADDUserButton(ID, 50, 0);
    delegateUcDevice?.Invoke(1, ID);
}
else
{
    ADDUserButton(ID, receive[6], receive[5]);           // pm=receive[6], sub=receive[5]
    delegateUcDevice?.Invoke(1, ID, receive[6], name);   // pm passed to Form
}
```
**Fingerprint bytes located here:** `receive[6]` = **pm** (product-model byte),
`receive[5]` = **sub**. These drive both the sidebar icon (`ADDUserButton`, §5)
and the Form (`FormLEDInit((byte)data,…)` / `FormCZTVInit((byte)data,…)`).

### 3.7 device3 / device4 — no probe on connect
`DeviceOnConnected3` (`UCDevice.cs:1197-1208`) and `DeviceOnConnected4`
(`1239-1250`) send **no** handshake bytes; they only post events `4098`/`4099`.
`DeviceDataReceived3/4` (`1223-1233`, `1265-1275`) forward raw data as `8194`/`8195`.
`AddhidDeviceList` for ID 4 sends the 14-byte ACK (the `ID == 4` branch above).

### 3.8 Where fbl / mode / count / ysl come from
**Only pm (`receive[6]`) and sub (`receive[5]`) are read in these two files.**
`fbl`, `mode`, `count`, `ysl` are **out-of-scope** — they are parsed inside
`FormCZTV.FormCZTVInit(...)` / `FormCZTV.DeviceDataReceived(...)` and
`FormLED.FormLEDInit(...)`, which receive the raw `data[]`. `Form1` routes the raw
byte[] to them via `case 2` (`Form1.cs:1202-1232`):
```csharp
case 2:
    int index3 = ucDevice1.hidList1.Count + ((byte[])data)[9];   // list index = data[9]
    FormCZTV formCZTV = (FormCZTV)arrayList3[1];
    formCZTV.DeviceDataReceived((byte[])data);
```

---

## 4. Shared-memory LCD discovery (SCSI/SPI via USBLCD*.exe)

`Form1.Timer_RGB_LCD_event` (`Form1.cs:656-811`) polls the two MMFs and pattern-
matches magic bytes to synthesize `FormCZTV`. Four distinct signatures:

| Buffer | Magic test (verbatim) | `FormCZTVInit(...)` call | line |
|---|---|---|---|
| RGB | `shareMemoryValRGB[6]==220 && shareMemoryValRGB[2]==72 && ((val[0]==0 && val[7]!=85) \|\| val[0]==1 \|\| (val[0]>=4 && val[0]<=12))` | `FormCZTVInit(72, 2, count, 95, val[4], text, val[1])` | 679, 706 |
| RGB | `shareMemoryValRGB[6]==221 && shareMemoryValRGB[5]==220 && shareMemoryValRGB[7]==220` | `FormCZTVInit(val[0], 10, count, 95, 1, text2)` | 713, 740 |
| RGB | `shareMemoryValRGB[6]==220 && shareMemoryValRGB[2]==54` | `FormCZTVInit(val[1], 3, count, 95, 100, text3, val[0])` | 747, 774 |
| SPI | `shareMemoryVal[153598]==220` | `FormCZTVInit(val[153599], 1, spiCount)` | 787, 805 |

Header metadata read from RGB buffer (`Form1.cs:689`, `706`, `708`):
```csharp
text = Encoding.UTF8.GetString(shareMemoryValRGB, 9, shareMemoryValRGB[8]);   // name @ [9], len @ [8]
formCZTV2.FormCZTVInit(72, 2, formCZTVRgbArrayCount, 95, shareMemoryValRGB[4], text, shareMemoryValRGB[1]);
ucDevice1.RGB_ADD_Device(shareMemoryValRGB[4], shareMemoryValRGB[1]);          // pm=val[4], sub=val[1]
```
So for the shared-memory path the **second `FormCZTVInit` arg is the wire "mode"**:
`1`=SPI, `2`, `3`=HID, `10`. pm/sub for the sidebar come from buffer offsets that
differ per signature (`[4]/[1]`, `[0]/0`, `[1]/[0]`, `[153599]/0`).

**Caveat — magic bytes are `0xDA/0xDB/0xDC/0xDD` = 218/219/220/221.** Header
constants `USB_PACKED_Head=220 (0xDC)`, `USB_PACKED_Head1=221 (0xDD)`
(`UCDevice.cs:50-52`). The 20-byte HID probe additionally leads with `218 219`
(`0xDA 0xDB`).

`RGB_ADD_Device` (`UCDevice.cs:1326-1330`):
```csharp
public void RGB_ADD_Device(byte pm = 50, byte sub = 0)
{
    ADDUserButton(257, pm, sub);
    rgbList.Add(rgbList.Count + 1);
}
```

---

## 5. Device → Form routing table (`Form1.DelegateUCDevice`)

`Form1.cs:1149-1201` `case 1` (device connected):
- `info == 1` → `new FormLED()`, `formLED.FormLEDInit((byte)data, 1, hidList1.Count, name)` (`1156-1171`); delegate `DelegateFormLED`.
- `info == 2` → `new FormCZTV()`, delegate `DelegateFormCZTVHid`, `FormCZTVInit((byte)data, 3, hidList2.Count, 95, 100, name)` (`1176-1196`).
- `info == 3 || 4` → empty (`1198-1200`).

`case 2` (device data received) `Form1.cs:1202-1232` — routes by the same 1/2/3/4
switch, index = `hidList*.Count + data[9|11]`.

`formDeviceArray` layout is **ordered by ID block**: LED block `[0 .. hidList1.Count)`,
then LCD-HID block, then hidList3, then hidList4 (see index math at
`Form1.cs:1096`, `1102`, `1111`, `1116`, `1183`, `1213`, `1221`, `1227`). RGB
shared-memory `FormCZTV`s live in the separate `formCZTVArray`.

### 5.1 pm → product name (sidebar) — the fullest device table
`UCDevice.ADDUserButton` (`UCDevice.cs:317-749`) switches `ID` then `pm` (and often
`sub`) to pick a bitmap. This is the richest VID/PID→model map in the codebase.
Representative (verbatim mapping, `switch (pm)`):

- **ID 1 (LED, 0x0416/0x8001)** `UCDevice.cs:334-424`: pm 1=FROZEN_HORIZON_PRO,
  2=FROZEN_MAGIC_PRO, 3=AX120_DIGITAL, 16=PA120_DIGITAL, 23=RK120_DIGITAL,
  32=AK120_Digital, 48=LF8, 49=LF10, 80=LF12, 96=LF10, 112=LC2, 128=LC1, 129=LF11,
  144=LF15, 160=LF13, 208=CZ1, default=KVMALEDC6.
- **ID 2 (LCD-HID, 0x0416/0x5302)** `UCDevice.cs:426-493`: pm 36=AS120_VISION,
  50/51=FROZEN_WARFRAME, 52/53=BA120_VISION, 54=LC5, 58(sub0=FROZEN_WARFRAME_SE /
  else LM26), 100=FROZEN_WARFRAME_PRO, 101=ELITE_VISION, 128=LM24, default=CZTV.
- **ID 257 (shared-mem RGB LCD)** `UCDevice.cs:505-720`: large pm×sub matrix
  (pm 1 sub≤1=GRAND_VISION, sub48=LM22, sub49=LF14; pm3=CORE_VISION;
  pm4 sub1=HYPER_VISION/sub2=RP130_VISION/sub3=LM16SE; pm5=Mjolnir_VISION;
  pm6 sub1=FROZEN_WARFRAME_Ultra/sub2=FROZEN_VISION_V2; pm7 sub1=Stream_Vision/
  sub2=Mjolnir_VISION_PRO; pm9=LC2JD; pm10 sub5=LF16/sub6=LF18/def=LC3; pm11=LF19;
  pm12=LF167; pm32/50/51/64/65/100/101/128 = further FROZEN/LM/ELITE/LF variants).
- **ID 3, ID 4** `UCDevice.cs:495-504`: both fall through to generic `A1CZTV`.

---

## 6. Reconnect / multi-device / teardown

- **Multi-device**: every kind is a list (`hidList1..4`, `rgbList`,
  `formCZTVArray`); `formDeviceArray` interleaves them by ID block. Buttons in
  `myButtonList` are `Insert`ed at the block offset (`UCDevice.cs:730-747`).
- **Disconnect**: `DeviceOnDisConnectedN` (`UCDevice.cs:941`, `1066`, `1210`, `1252`)
  post events 0/1/2/3 → `DelhidDeviceList` (`UCDevice.cs:794-811`) removes any
  `!IsDeviceConnected` entry, deletes its button, and fires `delegate(0, ID, num)`
  → `Form1.DelegateUCDevice case 0` disposes the Form (`Form1.cs:1076-1147`).
- **Reconnect after 3 empty scans**: `Timer_event` disposes device1..4 and nulls
  them (`UCDevice.cs:201-226`); the next `Scan_UsbHid` (from hotplug or add)
  reconstructs them.
- **Concurrency guards**: `workingcon` / `workingDis` spin-locks serialize
  connect/disconnect (`UCDevice.cs:927`, `943`, `1047`, etc.); `isTimerIN`
  re-entrancy guard on the scan timer (`UCDevice.cs:183-187`).
- **Full reset** on resume/manual: `ResetAllDevice` (`Form1.cs:1510-1555`) tears
  down every Form, clears both MMF buffers, calls `ucDevice1.Remove_RGB_Device()`.

---

## 7. Magic-byte reference (verbatim constants)

`UCDevice.cs:50-68`:
```csharp
public const byte USB_PACKED_Head    = 220;  // 0xDC
public const byte USB_PACKED_Head1   = 221;  // 0xDD
public const byte USB_PACKED_ONOFF   = 0;
public const byte USB_PACKED_STATE   = 1;
public const byte USB_PACKED_GET_STATE = 2;
public const byte USB_PACKED_AUDIO   = 4;
public const byte USB_PACKED_MOTOR   = 5;
public const byte USB_PACKED_FAN     = 6;
public const byte USB_PACKED_LED     = 16;
public const byte USB_PACKED_LCD     = 48;
```
- HID connect probe: `DA DB DC DD 00×7 01 00…` (20 bytes), reply valid when
  `data[13]==1` (LCD) / consumed via `receive[6]=pm,[5]=sub` (LED).
- Device-add ACK: `DC DD 01 00 [ID] [ID>>8] 00×4 [count] 01 00 00` (14 bytes).
- Shutdown/clear sentinel written to RGB MMF: `AA BB CC DD` = `170 187 204 221`
  (`Form1.cs:513`, `1561`).

---

## 8. Confidence & gaps

- **HIGH** confidence on: the 4 VID/PID rows + report lengths, the ID→Form routing,
  the connect handshake byte arrays, the pm=`receive[6]`/sub=`receive[5]`
  fingerprint, the shared-memory magic-byte signatures, hotplug via `WParam==7`.
- **Cannot resolve from these two files (out-of-scope):**
  - `fbl`, `mode`, `count`, `ysl` field parsing — lives in `FormCZTV` / `FormLED`.
  - The `FormCZTVInit` / `FormLEDInit` parameter *semantics* (arg 2 = wire mode is
    inferred from call-site consistency, but the signature is defined elsewhere).
  - The SCSI/SPI wire handshake proper — performed by external `USBLCD.exe` /
    `USBLCDNEW.exe`, not present in this decompile scope.
  - Whether `data[17]==16` (LED serial gate) corresponds to `USB_PACKED_LED=16`
    is likely but not proven within these files.
