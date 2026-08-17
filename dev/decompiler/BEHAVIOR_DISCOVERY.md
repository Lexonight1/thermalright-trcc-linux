# BEHAVIOR — per-method grind: Form1.cs + UCDevice.cs

<!-- audit-state: origin=2.0.3.0 addresses=2.1.6.0 known-bad=none -->
> **Audited against TRCC 2.0.3; citations re-anchored to TRCC 2.1.6.**
> 36 method(s) documented here changed in TRCC 2.1.6 and have NOT been re-read: `ADDUserButton`, `AddhidDeviceList`, `CheckDirectoryExist`, `ClearButtonBouns`, `DelegateFormCZTVHid`, `DelegateUCAbout`, `DelegateUCDevice`, `DelhidDeviceList`, `DeviceDataReceived1`, `DeviceDataReceived2`, `DeviceDataReceived4`, `DeviceOnConnected1` (+24 more) — read those entries as TRCC 2.0.3 history.
> [`AUDIT_INDEX.md`](AUDIT_INDEX.md#provenance)
<!-- /audit-state -->

Sources (verbatim, line-cited):
- `~/Downloads/TRCCCAPEN/TRCC_decompiled/TRCC/Form1.cs` (2,200 lines, 48 methods)
- `~/Downloads/TRCCCAPEN/TRCC_decompiled/TRCC/UCDevice.cs` (1,747 lines, 41 methods)

Builds on `AUDIT_DISCOVERY.md` (the two-channel discovery/handshake/VID-PID
model). This file documents **how every method works**, one line each, so the
consolidation into `src/trcc/` can proceed with the whole surface understood.
Every method in `control-flow.json` for these two files is covered below.

Legend: **[GOD]** = oversized multi-responsibility method that must be split on
port; **[COPY-PASTE]** = near-duplicate body that must collapse to one path.

Driving state (Form1): `formDeviceArray` = per-HID-device `[type,int; form]`
pairs ordered LED(1)→CZTV-HID(2)→dev3→dev4; `formCZTVArray` = shared-memory
FormCZTV windows (RGB + SPI external LCDs); `nowFormDevice`/`nowFormDeviceRGB` =
active selection; `myMode` (1=device view, 0=about/no-device); `myPowerMode`
(true=resume-active, drives external `USBLCD*.exe` respawn). `shareMemoryVal`
(153600 = 320×240×2 SPI frame) / `shareMemoryValRGB` (691200 = 480×480×3 RGB
frame) are the poll buffers.

Driving state (UCDevice): `device1..4` = four `UsbHidDevice` handshake channels
(VID/PID + report-len: dev1 1046/32769 len64, dev2 1046/21250 len512, dev3
1048/21251 len64, dev4 1048/21252 len512); `hidList1..4` = connected devices per
channel; `myButtonList` = sidebar buttons; `sendUsbArray` = 10 per-device send
queues; `timerStart`/`scanCount`/`timerStartCountVal` = the scan state machine.

---

## Form1.cs

### Shared-memory primitives (external-process LCD bridge)
- `InitMemorySize` (Form1.cs:132) — `CreateOrOpen("shareMemory_Image", 38400000)`; the 250-slot SPI-frame ring (250×153600). No branches.
- `ReadShareMemory` (Form1.cs:137) — reads one 153600-byte SPI frame from slot `n` (offset `n*153600`) into `shareMemoryVal`; opens/reads/closes a view stream. No branches.
- `WriteShareMemory` (Form1.cs:145) — writes `count` bytes of `b` into SPI slot `n` (offset `n*153600`, default count=153600). No branches.
- `CloseShareMemory` (Form1.cs:153) — `shareMemory.Dispose()`. No branches.
- `InitMemorySizeRGB` (Form1.cs:158) — `CreateOrOpen("shareMemory_ImageRGB", 34560000)`; the 50-slot RGB-frame ring (50×691200). No branches.
- `ReadShareMemoryRGB` (Form1.cs:163) — reads `count` bytes (default 691200) from RGB slot `n` (offset `n*691200`) into `shareMemoryValRGB`. Poll callers pass count=100 to sniff just the header. No branches.
- `WriteShareMemoryRGB` (Form1.cs:171) — writes `count` bytes of `b` into RGB slot `n`. The `{170,187,204,221}` sentinel written here on close/reset tells the external LCD process to sleep. No branches.
- `CloseShareMemoryRGB` (Form1.cs:179) — `shareMemoryRGB.Dispose()`. No branches.

### Startup / autostart / registry
- `ClearButtonBouns` (Form1.cs:168) — zeroes the power button's flat-appearance border color (cosmetic). No branches.
- `KaijiQidong` (Form1.cs:173) — autostart bootstrap; key branches: HKLM Run "TRCC" value `text==null` → write path + toggle ON; else if `Data\boot` file exists → `text==exePath` toggles ON, mismatch → OFF; else (no boot file) → write path, toggle ON, create `Data\boot`.
- `KaijiQidongSet` (Form1.cs:205) — user toggles autostart; key branches: existing value `null` → write path+ON; `bl==true` → write exePath; `bl==false` → write `""` (blank, disables). Called via `DelegateUCAbout` cmd 0.
- `GengGaiRegedit` (Form1.cs:336) — forces HKLM Policies `EnableLUA`; key branch: `!=0` → set to 0 (disables UAC prompts). **Windows-hostile; NOT ported.** Defined but no in-file caller.
- `InitializeForm` (Form1.cs:348) — wires `ucDevice1.delegateUcDevice=DelegateUCDevice` + `ucAbout1.ucAbout=DelegateUCAbout`; builds 6-byte config `{218,219,220,221, readHDD?0:1, timerSec-1}` and pushes to `formSystemInfo.ucSystemInfo1.WriteShareMemory`; starts+shows+hides FormSystemInfo (warm-up); wires `upDateInfo=UpSystemInfo`; adds `ucSystemInfoOptions1` at (190,98). Branch: `isReadHDD` → byte[4].
- `CheckDirectoryExist` (Form1.cs:264) — creates `Data\` if absent. One branch.

### Lifecycle / window
- `Form1` ctor (Form1.cs:272) — the boot sequence: InitializeComponent → ClearButtonBouns → KaijiQidong → CheckDirectoryExist → both shared-mem inits (write 4-byte zero clear) → InitializeForm → new arrays + 15ms `m_timer` (stopped) → **kill stray `USBLCD*` processes** → show/hide FormStart splash → `ucDevice1.Scan_UsbHid()` → `DelegateUCAbout(32,Language)` (language) → `m_timer.Start()` → subscribe `PowerModeChanged`. One try/catch branch.
- `OnPowerModeChanged` (Form1.cs:319) — `switch(mode-1)`: case 0 (Suspend) → `myPowerMode=true`; case 1 (StatusChange) → noop; case 2 (Resume) → kill `USBLCD*` procs, `myPowerMode=false`, `ResetAllDevice()`. This is the wake-from-sleep path (#150).
- `WndProc` (Form1.cs:677) — override; key branch: `WParam.ToInt32()==7` → `ucDevice1.Scan_UsbHid()` (WM_DEVICECHANGE-style hotplug trigger), then base WndProc.
- `Form1_Shown` (Form1.cs:692) — first show: `!kaiji` → set kaiji, show tray icon, **hide window** (start minimized to tray); else just ensure tray icon visible.
- `Form1_Activated` (Form1.cs:706) — ensures tray icon visible; hides `formSystemInfo` if visible. Two branches.
- `mainNotifyIcon_MouseClick` (Form1.cs:718) — tray left-click (`Button==1048576`): toggles window Show/Hide + Activate. Two branches.
- `退出ToolStripMenuItem_Click` (Form1.cs:416) — tray Exit: for each `formCZTVArray` calls `formJP.FormScreenshot_DoubleClick` (stops screencast); loops HID CZTV devices (indexed `hidList1.Count..`) same stop; then Close/Dispose/`Environment.Exit(0)`. try/catch returns early on index miss.
- `buttonPower_Click` (Form1.cs:782) — hides window (minimize-to-tray). No branches.
- `buttonPower_MouseEnter` (Form1.cs:787) / `buttonPower_MouseLeave` (Form1.cs:792) — swap power-button hover image. No branches.
- `Form1_MouseDown` (Form1.cs:797) — drag start (`Button==1048576`): records `_mousePoint = -(X,Y)`; key branch `sender==null` → offset X by −180 (compensates for the 180px sidebar when a child form forwards the event).
- `Form1_MouseMove` (Form1.cs:814) — if dragging, move window to `MousePosition + _mousePoint`. One branch.
- `Form1_MouseUp` (Form1.cs:824) — clears `isMouseDown` on `Button==1048576`; empty inner `IsEmpty` check is dead. Two branches.
- `Form1_FormClosing` (Form1.cs:503) — shutdown: stop+dispose timer; sleep 100 → write RGB sleep sentinel `{170,187,204,221}` → sleep 200; close both shared mems; kill `USBLCD*`; `ucDevice1.UCDeviceClose()` + `formSystemInfo.FormSystemInfoClose()`. Each in its own try/catch. **This is the graceful device-sleep-on-exit path (#143).**

### Master timer pumps — **[GOD]**
- `Timer_event` (Form1.cs:1182) — the 15ms master tick; calls `Timer_Form_event()` + `Timer_One_LCD_event()` + `Timer_RGB_LCD_event()` in sequence. Thin dispatcher, but fans into three god-methods below. **[GOD]** (as the aggregate pump).
- `Timer_Form_event` (Form1.cs:552) — **[GOD]** per-tick form pump: `timerCount++`; every 10 ticks pumps every `hidList1` LED form's `MyTimer_Event()`; then unconditionally pumps every `hidList2` CZTV-HID form's `Timer_event()` (indices `hidList1.Count..`). try/catch `return`/`break` on index race. Key var: `timerCount>=10` gate.
- `Timer_One_LCD_event` (Form1.cs:955) — **[GOD]** external-process keepalive, gated on `myPowerMode`: counts to 128 ticks, then scans running processes for `USBLCD`/`USBLCDNEW`; respawns whichever is missing (hidden window, WorkingDirectory=StartupPath). `!myPowerMode` → reset counter. Key vars: `oneTimerCount>=128`, `flag`/`flag2` (proc present).
- `Timer_RGB_LCD_event` (Form1.cs:656) — **[GOD]** the shared-memory device synthesizer (biggest branch cluster, 13). First pumps existing `formCZTVArray` windows. Gated `!ucDevice1.timerStart` (skip during HID scan). Every 64 ticks: polls RGB slots (`formCZTVRgbArrayCount<10`, `ReadShareMemoryRGB(...,100)`) and matches three magic-byte signatures to birth a FormCZTV: **(a)** `val[6]==220 && val[2]==72 && (val[0] one of {0-not-85,1,4..12})` → `FormCZTVInit(72,2,...,95,val[4],name,val[1])` (protocol 2, 480-class); **(b)** `val[6]==221 && val[5]==220 && val[7]==220` → `FormCZTVInit(val[0],10,...,95,1,name)` (protocol 10); **(c)** `val[6]==220 && val[2]==54` → `FormCZTVInit(val[1],3,...,95,100,name,val[0])` (protocol 3). Each: stops timer, hides chrome, decodes UTF-8 name (`val[9..9+val[8]]`), `ClearMemoryMy`, adds window + `RGB_ADD_Device`, `formCZTVRgbArrayCount++`, restart timer. Then the SPI path (`formCZTVSpiArrayCount<1`, `ReadShareMemory`): `shareMemoryVal[153598]==220` → `FormCZTVInit(val[153599],1,spiCount)` (protocol 1, 320×240 SCSI). The `[6]/[2]/[5]/[7]` header bytes are the device-class discriminators. **[COPY-PASTE]** — the three signature blocks are ~40 identical lines each (stop/hide/decode-name/new-FormCZTV/add/restart) differing only in the `FormCZTVInit(...)` args.

### Delegate callback routers (cmd-dispatch god-switches)
- `DelegateFormLED` (Form1.cs:1189) — LED-form → Form1 callback; `switch(cmd)`: 16 → `ucDevice1.SendDeviceData1(info,data,data1)` (send LED bytes on channel-1); 241/242/243 → forward mouse to `Form1_Mouse*`; 255 → `buttonPower_Click`.
- `ResetFormCZTV` (Form1.cs:1217) — reloads theme on **every other** CZTV window (`!=form`): calls `ReadFileTheme(false)`+`ChangeFileTheme()` across `formCZTVArray` then the HID CZTV forms (`hidList1.Count..`). Enforces one-active-theme. try/catch break on race.
- `DelegateFormCZTV` (Form1.cs:1275) — shared-mem CZTV-form callback; `switch(cmd)`: 0 → `WriteShareMemory` (SPI frame out); 1 → `WriteShareMemoryRGB` (RGB frame out); 128 → `ResetFormCZTV`; 241/242/243 → mouse; 255 → power. This is how the software-rendered LCD frame reaches the external `USBLCD*.exe`.
- `DelegateFormCZTVHid` (Form1.cs:916) — HID CZTV-form callback; `switch(cmd)`: 0/1 (fallthrough) → **de-interleave** `info` (`num%2!=0 → num=(num-1)/2`) then `ucDevice1.SendDeviceData2(num,data,data1)` (the interleaving trick maps the shared even/odd slot index back to the HID device index); 128 → ResetFormCZTV; 241/242/243 mouse; 255 power. **[COPY-PASTE]** — same tail (128/241/242/243/255) as `DelegateFormCZTV`/`DelegateFormLED`; the routers should collapse to one table.

### Device-window show/hide/reindex
- `HideDeviceEvent` (Form1.cs:956) — hides ALL device forms: clamps `nowFormDevice`, loops `formDeviceArray` hiding visible LED(type1)/CZTV(type2) forms (3/4 = noop); clamps `nowFormDeviceRGB`, hides all `formCZTVArray`. Key var: `arrayList[0]` (device type).
- `HideDeviceEventOld` (Form1.cs:1406) — **[COPY-PASTE]** legacy variant: hides only the CURRENT `nowFormDevice`/`nowFormDeviceRGB` form, not all. Superseded by `HideDeviceEvent`; no in-file caller — dead/legacy.
- `ShowDeviceEvent` (Form1.cs:1323) — shows one form; `switch(mode)`: 1 → show `formDeviceArray[count]` (LED or CZTV by type), set `nowFormDevice`; 2 → show `formCZTVArray[count]`, set `nowFormDeviceRGB`.

### The two master routers — **[GOD]**
- `DelegateUCDevice` (Form1.cs:1703) — **[GOD]** UCDevice→Form1, 31 branches, the device-lifecycle hub. `switch(cmd)`: **0 (remove)** — computes flat index from `info` (1=LED base 0; 2=CZTV base `hidList1.Count`; 3/4 further offset), removes control+disposes, **reindexes `myDeviceCount` on all siblings**, promotes device 0 if the active one left, restores chrome when empty; **1 (add)** — hides chrome; `info==1` → new FormLED inserted at `hidList1.Count`, `FormLEDInit((byte)data,1,...,name)`; `info==2` → `ClearMemoryMy` + new FormCZTV at `hidList1.Count+hidList2.Count`, `FormCZTVInit((byte)data,3,...,95,100,name)`, hide about, `HideDeviceEvent`+`ShowDeviceEvent(1,0)`; **2 (data-received)** — `switch((ushort)info)` routes `data` to the right form by index byte (`data[11]` for 1/3/4, `data[9]` for 2 → `formCZTV.DeviceDataReceived(data)`); **240** → myMode=0, hide all + chrome, show about; **241/242/243** mouse; **256** → myMode=1, select device `ShowDeviceEvent(1,info)`; **512** → myMode=0, show no-device chrome; **768** → myMode=0, `ShowDeviceEvent(2,info)` (select shared-mem RGB device). The flat-index arithmetic (`hidList1.Count + hidList2.Count + …`) is repeated ~8×.
- `DelegateUCAbout` (Form1.cs:1947) — **[GOD]** UCAbout→Form1, 22 branches. `switch(cmd)`: **0** → `KaijiQidongSet(info)` (autostart toggle); **16** → rebuild the `{218,219,220,221,readHDD,timerSec-1}` config, push to FormSystemInfo shared mem, **kill HWINFO processes**; **32 (language)** → set `Language`, `switch(Language 0..8)` picks the FormSystemInfo background image + tray "Exit"/"退出" text, then broadcasts `FormCZTVLanguageSet`/`FormLEDLanguageSet` to every device form; **100** → Close/Dispose/Exit(0); **241/242/243** mouse; **255** power. **[COPY-PASTE]** — the config-6-bytes build at case 16 duplicates `InitializeForm`; the 9-way language image switch is a table.
- `UpSystemInfo` (Form1.cs:1741) — FormSystemInfo→Form1 callback; `switch(mode)`: 0 → `switch(val)` 0=spawn `USBLCDNEW.exe`, 16=spawn `USBLCD.exe`+`UpdateMyInfo`+`myPowerMode=true`, 254=`GetMyNameFile`, 255=`UCSystemInfoOptionsTimer`; 1 → `ucSystemInfoOptions1.FormSystemInfoSet(val,name)`; 2 → noop. The external-LCD launcher entry point.

### Buttons / reset / teardown
- `buttonHelp_Click` (Form1.cs:1940) — `Process.Start("LCDHelp.pdf")` in try/catch. No branches.
- `buttonNet60_Click` (Form1.cs:2163) — `Process.Start("dotnet6.exe")` (runtime installer) in try/catch. No branches.
- `ResetAllDevice` (Form1.cs:2174) — full teardown-and-clear: stop timer; remove+dispose every `formCZTVArray` (FormCZTVRemove) and every `formDeviceArray` LED/CZTV form; clear arrays; zero both counts; `ucDevice1.Remove_RGB_Device()`; write 8-byte zero into RGB slots 0..18 and zero the SPI buffer; restart timer. The resume/reset heavy path (called from `OnPowerModeChanged` + `ResetAllDeviceButton`).
- `ResetAllDeviceButton` (Form1.cs:2017) — public reset button: stop timer, sleep 100 → RGB sleep sentinel `{170,187,204,221}` → sleep 200, kill `USBLCD*`, `ResetAllDevice()`, `ucDevice1.Scan_UsbHid()`. One try/catch.
- `Dispose` (Form1.cs:2042) — standard designer dispose (`components`). One branch.
- `InitializeComponent` (Form1.cs:2255) — WinForms designer boilerplate (controls, tray, buttons, sizes, event wiring). No behavior. **Not ported.**

---

## UCDevice.cs

### Ctor / lifecycle / scan
- `UCDevice` ctor (UCDevice.cs:140) — builds all 9 ArrayLists (`hidNameList1..4`, `hidList1..4`, `rgbList`, `myButtonList`, `sendUsbArray` seeded with 10 empty queues), starts a 100ms auto-reset `System.Timers.Timer(Timer_event)`. No branches.
- `UCDeviceClose` (UCDevice.cs:167) — stop+dispose the scan timer. No branches.
- `Scan_UsbHid` (UCDevice.cs:173) — arms the scan state machine: `scanCount=0`, `timerStartCount=0`, `timerStartCountVal=20`, `timerStart=bl` (default true). The hotplug/coldplug entry point (called from ctor, WndProc WParam==7, AddhidDeviceList, ResetAllDeviceButton).
- `Timer_event` (UCDevice.cs:181) — **[GOD]** the HID scan pump (17 branches), reentrancy-guarded by `isTimerIN`. Gated `timerStart`. Counts to `timerStartCountVal`, then `scanCount++`; **scanCount 1** → interval 2 (fast retry), **2** → interval 30, **>=3** → `Scan_UsbHid(false)` (stop) + dispose all four `device1..4`, clear working flags. Else (scanCount 1-2): for each of `device1..4`, **construct if null** with its VID/PID + wire the connect/disconnect/received handlers, **else `.Connect()` if disconnected**. This is the four-channel enumerate/connect driver. **[COPY-PASTE]** — the four `if(deviceN==null){new UsbHidDevice(...); +=handlers} else if(!connected) Connect()` blocks are identical bar VID/PID/handlers.

### Sidebar buttons
- `userButton_Click` (UCDevice.cs:278) — sidebar button click: marks the clicked button active (bitmap2) + others inactive (bitmap1), sets `nowUserButton`; key branch `myType==257` (shared-mem RGB device) → `delegateUcDevice(768, nowUserButton - hidTotal)` (select RGB), else `delegateUcDevice(256, nowUserButton)` (select HID); resets the two footer button images.
- `UserButtonArrange` (UCDevice.cs:340) — relayout: positions each `myButtonList` button at `(25, 160 + i*60)`. No branches.
- `ADDUserButton` (UCDevice.cs:317) — **[GOD]** the device-model→sidebar-image dispatcher (97 branches — the largest method in the file). `switch(ID)` × nested `switch(pm)` × nested `switch(sub)` selects the product image (bitmap1/bitmap2 hover pair) for every Thermalright model: ID 1 = LED coolers (pm 1=Frozen Horizon Pro … 208=CZ1, default=KVMALEDC6); ID 2 = CZTV HID LCDs (pm 36=AS120 Vision … 128=LM24, default=CZTV); ID 3/4 = generic CZTV; **ID 257 = shared-mem RGB LCDs**, the deepest tree (pm 1 sub≤1=Grand Vision / sub 48=LM22 / 49=LF14; pm 4 sub 1=Hyper/2=RP130/3=LM16SE; pm 6/7/10/32/64/65/100/101 each branch on sub). Then inserts the button into `myButtonList` at the type-ordered offset (`hidList1.Count [+hidList2 …]`), highlights if first, `UserButtonArrange`. **This is the master device registry — every VID/PID/pm/sub → product identity mapping lives here.** Port target: a data table, not code.
- `DeleteUserButton` (UCDevice.cs:1040) — removes the sidebar button for `(ID,count)`: resolves flat index by type offset, removes+disposes control, `RemoveAt`, promotes button 0 if the active one left, `UserButtonArrange`. Key branch: `nowUserButton==count` → reset to 0. 8 branches (the 4-way ID offset + guards).

### HID list add/remove
- `DelhidDeviceList` (UCDevice.cs:794) — reverse-iterates `array`; for each disconnected `UsbHidDevice`: `DeleteUserButton(ID,num)`, `delegateUcDevice(0,ID,num)` (tells Form1 to drop the window), `RemoveAt`, dispose. Key branch: `!IsDeviceConnected`.
- `AddhidDeviceList` (UCDevice.cs:1182) — registers a newly-handshaked device: builds a 14-byte `{220,221,1,0,ID,ID>>8,…,array.Count,1,…}` CommandMessage; key branch **ID==4** → send message + `ADDUserButton(4,50,0)` + `delegateUcDevice(1,ID)`, **else** → `ADDUserButton(ID, receive[6], receive[5])` + `delegateUcDevice(1,ID,receive[6],name)` (pm=receive[6], sub=receive[5] are the handshake identity bytes); adds device to list, nulls the matching `deviceN` (so `Timer_event` reconstructs a fresh channel for the next unit), re-arms `Scan_UsbHid`.

### USB event marshalling
- `DoUsbHidEvent` (UCDevice.cs:1224) — **[GOD]** the marshalled USB-event router (13 branches), runs on the UI thread. `switch(arrayList[0])`: **0-3** → `DelhidDeviceList(hidListN, N+1)` + clear `workingDis` (disconnect on channel N); **4096-4099** → `AddhidDeviceList(hidListN, deviceN, N+1, receive, name)` + clear `workingcon` (connect); **8192-8195** → `delegateUcDevice(2, (ushort)N+1, data)` (data-received forward to Form1). The codes encode `(action<<12 | channel)`.
- `UsbHidEvent` (UCDevice.cs:1199) — thread→UI marshaller: `BeginInvoke(DoUsbHidEvent, hid)`. All the device threads funnel through here. No branches.

### Channel 1 (VID 1046 / PID 32769, 64-byte reports — LED coolers)
- `DeviceOnConnected1` (UCDevice.cs:925) — spin-wait `workingcon`, set it, send the 20-byte handshake `{218,219,220,221,0…,1,0…}` on device1. No branches.
- `DeviceOnDisConnected1` (UCDevice.cs:1223) — spin-wait `workingDis`, set it, spawn `UsbHidEvent` thread with code `0`. No branches.
- `DeviceDataReceived1` (UCDevice.cs:954) — guards `device1!=null && len>0`; sets `timerStartCountVal=300` (slow poll once connected); if path matches device1, builds `{4096, data, name}`; key branch **`data[17]==16 && len>=37`** → name from `BitConverter.ToString(data,21)` (device-reported serial), else name from `RemoveIllegalCharacters(DevicePath)`; spawns UsbHidEvent thread.
- `ThreadSendDeviceData1` (UCDevice.cs:983) — chunked sender (64-byte HID reports): guard `isSendUsbThread0[num]`; splits `data[len]` into 64-byte `CommandMessage`s (last partial), `Sleep(30)`, clear flag. try/catch swallow. Key branch: `num2>64` chunk vs tail.
- `SendDeviceData1` (UCDevice.cs:1318) — public: packs `{n,data,len}` and starts a `ThreadSendDeviceData1` thread (Normal priority). No branches.
- `RemoveIllegalCharacters` (UCDevice.cs:1329) — regex-strips everything not in `legalChars` (hex) from a device path → stable id. No branches.

### Channel 2 (VID 1046 / PID 21250, 512-byte reports — CZTV HID LCDs)
- `DeviceOnConnected2` (UCDevice.cs:1045) — spin-wait `workingcon`; allocates a dead 512-byte message + empty 400-iter loop (decompiler residue / timing pad); sends the same 20-byte handshake on device2. No real branches.
- `DeviceOnDisConnected2` (UCDevice.cs:1360) — **[COPY-PASTE]** identical to DisConnected1 but pushes code `1`.
- `DeviceDataReceived2` (UCDevice.cs:1716) — two-signature parser: **connect** — `data[1..4]=={218,219,220,221} && data[13]==1` → `{4097,data,name}` (name via `data[17]==16&&len>=37` serial vs path, same as ch1); **sensor-forward** — `…data[13]==8` → find the device's index in `hidList2`, stamp `data[9]=index`, push `{8193,data}`. Key discriminator: `data[13]` (1=handshake, 8=data).
- `ThreadSendDeviceData2` (UCDevice.cs:1764) — chunked sender (512-byte reports) draining `sendUsbArray[num]` queue: guard `isSendUsbThread[num]`; for each queued `{n,data,len}` splits into 512-byte messages (last partial), pops the queue entry; loops until empty. try/catch logs "Dai: UsbHidDevice myUsbList is Null!". Key branch: `num2>512` chunk vs tail. **This is the 512-chunk LCD frame path referenced by #150 fix.**
- `SendDeviceData2` (UCDevice.cs:1481) — public queued send: only enqueues if `sendUsbArray[n].Count<=2` (backpressure — drops frames when 3 already queued); starts the drain thread (AboveNormal priority) only when it was the first entry. Key branches: queue-depth gate + first-entry gate.

### Channel 3 (VID 1048 / PID 21251, 64-byte) & Channel 4 (VID 1048 / PID 21252, 512-byte) — stubs/minimal
- `DeviceOnConnected3` (UCDevice.cs:1499) — **[COPY-PASTE]** spin-wait, spawn UsbHidEvent thread with `4098` (no handshake bytes sent — connect handled downstream in AddhidDeviceList for ID 3). No branches.
- `DeviceOnDisConnected3` (UCDevice.cs:1512) — **[COPY-PASTE]** disconnect code `2`.
- `DeviceDataReceived3` (UCDevice.cs:1525) — `len>0` → push `{8194,data}`. One branch.
- `SendDeviceData3` (UCDevice.cs:1537) — **empty body** (unimplemented). No branches.
- `DeviceOnConnected4` (UCDevice.cs:1541) — **[COPY-PASTE]** connect code `4099`.
- `DeviceOnDisConnected4` (UCDevice.cs:1554) — **[COPY-PASTE]** disconnect code `3`.
- `DeviceDataReceived4` (UCDevice.cs:1265) — `len>0` → push `{8195,data}`. One branch.
- `SendDeviceData4` (UCDevice.cs:1571) — **empty body** (unimplemented). No branches.
- `SendDeviceData0` (UCDevice.cs:1575) — **empty body** (unimplemented placeholder). No branches.

### Mouse forwarders / footer buttons
- `UCDevice_MouseDown` (UCDevice.cs:1949) / `UCDevice_MouseMove` (UCDevice.cs:1290) / `UCDevice_MouseUp` (UCDevice.cs:1295) — forward to Form1 via `delegateUcDevice(241/242/243, this, e)` for window drag. No branches.
- `buttonSetting_Click` (UCDevice.cs:1964) — footer "About": sets footer images, resets all sidebar buttons to bitmap1, `delegateUcDevice(240)` (show about). No branches.
- `button1_Click` (UCDevice.cs:1977) — footer "Sensor/no-device": sets footer images, resets sidebar buttons, `delegateUcDevice(512)` (show no-device view). No branches.

### Shared-mem RGB device add/remove
- `RGB_ADD_Device` (UCDevice.cs:1620) — `ADDUserButton(257, pm, sub)` + append to `rgbList`. Called by Form1's `Timer_RGB_LCD_event` when a shared-mem LCD is discovered. No branches.
- `Remove_RGB_Device` (UCDevice.cs:1996) — full sidebar+HID purge: reverse-remove/dispose every `myButtonList` button; disconnect+dispose every `hidList1` and `hidList2` device (clear `workingDis`); clear `hidList1/2`, `hidNameList1/2`, `rgbList`. Key branch: null-button guard `return`. Called from `ResetAllDevice`.

### Designer
- `Dispose` (UCDevice.cs:1677) — standard designer dispose. One branch.
- `InitializeComponent` (UCDevice.cs:1686) — WinForms designer boilerplate (two footer buttons, sizes, event wiring). No behavior. **Not ported.**

---

## Consolidation targets (top 5)

1. **`ADDUserButton` (UCDevice.cs:317, 97 branches)** — the master device registry
   (ID×pm×sub → product identity + image). Port to a **data table** (`DeviceInfo`
   rows keyed on `(wire, pm, sub)`), not a nested switch. Already the north-star
   of the decompile-miner pipeline; this is the single richest device-mapping in
   the codebase.
2. **The 6 delegate routers → one Command table** — `DelegateFormLED` /
   `DelegateFormCZTV` / `DelegateFormCZTVHid` / `DelegateUCDevice` /
   `DelegateUCAbout` / `UpSystemInfo` all share the `241/242/243/255` mouse+power
   tail and cmd-switch shape. Collapse to one dispatch (the project's Command bus).
3. **`Timer_RGB_LCD_event` (Form1.cs:656)** — three ~40-line copy-paste
   signature→FormCZTV-birth blocks differing only in `FormCZTVInit(...)` args.
   Extract one `synthesize_shared_mem_device(header) -> DeviceInfo` keyed on the
   `[6]/[2]/[5]/[7]` discriminator bytes (a data table of signatures).
4. **`UCDevice.Timer_event` (181) + the four channel handler sets** — the four
   `deviceN` construct/connect blocks and the near-identical
   `DeviceOnConnected/DisConnected/DataReceived N` handlers collapse to ONE
   parameterized HID channel object (VID/PID/report-len/action-code as data).
   Channels 3/4 have empty senders — the OCP `Device` subclass model.
5. **The three master timer pumps (Form1.cs:552/590/656 via 813)** — split the
   god-pump into per-responsibility timers (form refresh / external-process
   keepalive / shared-mem synth) so each is independently testable, and lift the
   flat-index arithmetic (`hidList1.Count + hidList2.Count + …`, repeated ~10×
   across DelegateUCDevice/DeleteUserButton/ADDUserButton) into one index helper.

## Undetermined (needs out-of-scope sources)
- `FormCZTVInit(pm, protocol, count, [95], [sub/mode], [name], [extra])` overload
  semantics — the `protocol` arg (1=SPI/SCSI, 2/3/10=RGB variants) and the trailing
  args' meaning live in `FormCZTV.cs`, not here. The magic-byte→`FormCZTVInit` arg
  mapping in `Timer_RGB_LCD_event` is transcribed but the arg *roles* are inferred.
- `receive[5]`/`receive[6]` = sub/pm at `AddhidDeviceList` — confirmed as the
  handshake identity bytes by usage, but the full handshake reply layout is in
  `AUDIT_DISCOVERY.md` / the `UsbHidDevice` class (UsbHid lib), not these files.
- `data[13]` sensor opcode `8` (DeviceDataReceived2 forward path) — the payload
  format past the index byte is decoded in `FormCZTV.DeviceDataReceived`, out of scope.
- `GengGaiRegedit` (EnableLUA=0) has no in-file caller — whether anything invokes
  it is undetermined from these two files (likely dead or called from Program.cs).

## Confidence
High for control flow, branch discriminators, byte-array shapes, VID/PID/report
lengths, the shared-memory sizes/offsets, and the copy-paste/god flags — all read
directly line-by-line, every one of the 48+41 methods covered. Medium for the
*semantic role* of `FormCZTVInit` args and sensor-payload bytes (defined in
FormCZTV/FormLED, flagged Undetermined above). Coverage: 48/48 Form1, 41/41
UCDevice — no method sampled or skipped.
