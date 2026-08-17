# C# Oracle Audit — Metrics / Overlay-Element / Clock Subsystem

<!-- audit-state: origin=2.0.3.0 addresses=2.1.6.0 known-bad=none -->
> **Audited against TRCC 2.0.3; citations re-anchored to TRCC 2.1.6.**
> 2 method(s) documented here changed in TRCC 2.1.6 and have NOT been re-read: `FormInitDc`, `SetSystemVal` — read those entries as TRCC 2.0.3 history.
> [`AUDIT_INDEX.md`](AUDIT_INDEX.md#provenance)
<!-- /audit-state -->

Source root: `~/Downloads/TRCCCAPEN/TRCC_decompiled/`

Files audited (verbatim, line-cited):

| File | Lines | Role |
|------|-------|------|
| `TRCC.DCUserControl/UCSystemInfo.cs` | 1–1120 | HWiNFO shared-memory reader + metric parser + sensor picker list |
| `TRCC.DCUserControl/UCXiTongXinXi.cs` | 1–979 | "System Info" overlay config panel (font/size/color/metric-mode/carousel) |
| `TRCC.DCUserControl/UCXiTongXianShiSub.cs` | 1–356 | Per-overlay-element model + live text composition |
| `TRCC.DCUserControl/UCShiJianXianShi.cs` | 1–824 | Clock/date/week config panel (12/24h, date order, toggles) |
| `TRCC/FormSystemInfo.cs` | 1–445 | Sensor-picker dialog: name-priority resolution per metric slot |

Every claim below quotes the exact line(s). No inference is presented as fact.

---

## 1. Purpose & Lifecycle

### 1.1 `UCSystemInfo.cs` — the metric engine
Owns a Windows named memory-mapped file and drives `HWINFO.exe` as the sensor backend.

- Shared-memory constants (`:23-31`) — `shareMemorySize0` = 1048576 (1 MiB) is the length of the `shareMemoryVal` byte array the reader copies the view into; `shareMemorySize` = 2097152 (2 MiB) is the size of the mapping itself, so the staging buffer covers only the first half of the view; `shareMemoryName` = `"shareMemory_SysInfo"` is the Windows named-MMF identifier both TRCC and HWiNFO agree on; `shareMemory` is the `MemoryMappedFile` handle field.
- Create/open the MMF (`:109-112`) — a memory-mapped file named `"shareMemory_SysInfo"`, **2,097,152** bytes (2 MiB), opened if present and created otherwise.
- Lifecycle: `ThreadStart()` (`:248-252`) spawns a background thread on `FormInitDc`.
- `FormInitDc` (`:135-209`) kills any existing `HWINFO` process (`:143-145`), launches `HWINFO.exe` hidden from `Application.StartupPath` (`:153-159`), then polls the MMF up to 60× waiting for the handshake string `HWi32_GetNumberOfDetectedSensors` in the first 100 bytes (`:164-199`), re-launching HWiNFO if the process died. On success it creates a `System.Timers.Timer` with a 1000.0 ms interval, subscribes `timer_event` to its `Elapsed` event, sets `AutoReset` true so it re-arms itself, and starts it — one metric refresh per second for the life of the control (`:200-203`).
- Teardown `ucSystemInfoClose()` (`:988-1009`) stops/disposes the timer, disposes the MMF, and kills every `HWINFO` process.

**Port caveat:** the entire metric source is HWiNFO's JSON blob in a Windows MMF. On Linux this maps to the hwmon/pynvml aggregator; the string-parsing rules below are the *contract* the Linux side must reproduce for display strings/units, not the transport.

### 1.2 `UCXiTongXinXi.cs` — System-Info overlay config panel
UserControl (712×100, `:975`) with a background image `Resources.P01系统信息` (`:944`). Emits all edits through one delegate `delegateUCXinxi(int cmd, object info, object data, object data1)` (`:11-13`). It is a *control surface*, not a renderer — it pushes cmd codes to the parent form. Command codes enumerated in §3.5.

### 1.3 `UCXiTongXianShiSub.cs` — the overlay element (the DTO + live text)
UserControl 60×60 (`:351`) representing ONE draggable on-screen element. Holds all per-element fields (§3.1) and, each tick, composes its three labels' text from the metric engine or the clock (§3.2, §4). This is the C# equivalent of one overlay-element in our DC config.

### 1.4 `UCShiJianXianShi.cs` — Clock/date config panel
UserControl 712×100 (`:821`), background `Resources.P01时间显示` (`:792`). Config surface for the time element: 12/24h (`sjMode`), date order (`nyrMode` 1–4), and three independent on/off toggles (date `isNyr`, hour `isXs`, week `isXq`). Emits via `delegateUCShiJian` (`:11-13`). Cmd codes in §4.4.

### 1.5 `FormSystemInfo.cs` — the sensor picker dialog
Form (490×800, `:443`) hosting one `UCSystemInfo` (`:403`, `:435-438`). Purpose: let the user pick which HWiNFO sensor row feeds a given metric slot, with a **name-priority fallback chain** per metric (§2.4). `GetSystemInfo(int n)` (`:57-251`) returns the canonical sensor *name* for slot `n`; `GetSystemInfoVal` (`:269-294`) returns its current value string; `buttonOK_Click` (`:313-328`) reports the selected row back via `upDateInfo?.Invoke(1, num, text)`.

---

## 2. Metric Sources

### 2.1 Transport: JSON in shared memory
`timer_event` (`:646-960`) reads the MMF (`ReadShareMemory` `:114-120`), ASCII-decodes up to the first NUL (`strlen` `:491-498`, used `:680`), then splits the blob at the first `{` into `head` (drive/SMART table) and `text2` (the JSON sensor array) (`:683-692`).

### 2.2 The six metric "type" scans (the enum, effectively)
`timer_event` scans `text2` once per **HWiNFO sensor type**, each a `do…while(num>0)` loop keyed on a literal `"type":"<t>"` string. Each match walks back to the enclosing `{"` to grab the sensor *name*, then forward to `value`, truncates the value at the decimal point (`text3.Substring(0, text3.IndexOf("."))`), and appends a **unit-suffixed** display string. The six types and their exact unit handling:

| Type literal | Line | Unit logic | Display suffix |
|---|---|---|---|
| `"type":"temperature"` | `:696` | `myWendu==1`→C, `==2`→F: `num5*9/5+32` | `"℃"` (`:720`) / `"℉"` (`:724`); zero-case `"0℃"`/`"32℉"` (`:729-733`) |
| `"type":"fan"` | `:752` | none | `+ "RPM"` (`:773`); zero-case `"0RPM"` (`:777`) |
| `"type":"clock"` | `:789` | none | `+ "MHz"` (`:810`); zero-case `"0MHz"` (`:814`) |
| `"type":"usage"` | `:826` | none | `+ "%"` (`:847`); zero-case `"0%"` (`:851`) |
| `"type":"power"` | `:870` | none | `+ "W"` (`:891`); zero-case `"0W"` (`:895`) |
| `"type":"other"` | `:907` | none | raw int string, no suffix (`:928`); zero-case `"0"` (`:932`) |

Each parsed row is `arrayList2 = [0, name, valueString]` — index 0 is inserted last via `arrayList2.Insert(0, 0)` (e.g. `:735`, `:779`) and is the per-row selected-flag used by `SetSystemInfoVal`/button state.

### 2.3 Temperature unit toggle (`myWendu`)
the `myWendu` unit field, default **1** (`:61`). C→F conversion is integer: `num5 * 9 / 5 + 32` (`:724`). Truncation to int happens BEFORE unit conversion (`:716-717`): the reading string is cut at its decimal point and only then parsed to an int, so `38.9` becomes **38**, not 39..

### 2.4 "core" filtering — per-core rows are dropped
Both temperature and usage scans discard any sensor whose name contains `"core "` (lowercased):
- temp (`:736-739`): `if (((string)arrayList2[1]).ToLower().Contains("core ")) { arrayList2.Clear(); }`
- usage (`:854-857`): `if (((string)arrayList2[1]).Contains("core "))` — **note: usage check is case-sensitive, temp is `.ToLower()`.**

### 2.5 Named-scalar reads (`SetSystemVal`) — CPU/GPU power + memory timings
`SetSystemVal(strjs, str, types, ref float sysVal, n)` (`:534-559`) finds `str`, parses the following `{...}` as JSON, and if its `"type"` contains `types`, stores `value` truncated-at-`.` times `n`. Direct-field reads at end of `timer_event`:
- CPU power (`:941`): `SetSystemVal(text2, "\"CPU Package Power\"", "power", ref CpuPower);`
- GPU power fallback chain (`:942-945`): tries `"Total Graphics Power (TGP)"` → `"GPU Power"` → `"GPU ASIC Power"`; if **all fail, the GPU-power field is assigned the CPU-power value** (deliberate fallback).
- Memory scalars (`:946-956`), all defaulting to `100000f` (`:71-91`), all typed `"other"` except the temperature and clock rows. Each is one `SetSystemVal` call naming the HWiNFO sensor string to search for, the `"type"` substring the matched object must carry, and the destination field:

| HWiNFO sensor name | required `"type"` | Destination field |
|---|---|---|
| `"SPD Hub Temperature"` | `temperature` | `MemTemperature` |
| `"Memory Clock"` | `clock` | `MemClock` |
| `"Physical Memory Used"` | `other` | `MemUsed` |
| `"Memory Clock Ratio"` | `other` | `MemRatio` |
| `"Tcas"`, `"Trcd"`, `"Trp"`, `"Tras"`, `"Trc"`, `"Trfc"` | `other` | one `Mem*` timing field each |
| `"Physical Memory Load"` | `other` | `MemLoad` |

### 2.6 Disk (`GetHarddiskInfo`, `:561-644`)
Parsed from `head` (not the JSON). Builds `HardDiskInfo` rows once (drive name via `"drive: "` `:566`, SMART id via `"s.m.a.r.t.: "` `:584`). Each tick reads per drive: `"Total Activity"`(usage) `:625`, `"Read Rate"`(other) `:628`, `"Write Rate"`(other) `:631`, `"Drive Temperature"`(temperature) `:637`. Sentinel default `10000f`/`100000` for unread values (`:624`, `:574-577`).

### 2.7 FormSystemInfo name-priority chains (the metric→sensor-name map)
`GetSystemInfo(int n)` (`:57-251`) — the canonical sensor-name priority per slot. This IS the metric-key map a port must reproduce:

| Slot `n` | Metric | Priority order (first match wins), lines |
|---|---|---|
| 1 | CPU temp | name=="CPU" AND value has ℃/℉ (`:88-91`) → "CPU Package" (`:95`) → "CPU Die (average)" (`:102`) → "CPU (Tctl/Tdie)" (`:109`) |
| 2 | CPU usage | "Total CPU Usage" (`:120`) |
| 3 | CPU clock | "P-core 0 Clock" (`:131`) → contains "Core 0 Clock" (`:138`) → "Ring/LLC Clock" (`:145`) |
| 4 | GPU temp | "GPU Temperature" (`:156`) |
| 5 | GPU usage | "GPU Utilization" (`:167`) → "GPU Core Load" (`:174`) → "GPU D3D Usage" (`:181`) |
| 6 | GPU clock | "GPU Clock" (`:192`) → "GPU Shader Clock" (`:199`) |
| 7 | GPU power | "Total Graphics Power (TGP)" (`:210`) → "GPU Power" (`:217`) → "GPU ASIC Power" (`:224`) |
| 8 | GPU fan | "GPU Fan1" (`:235`) → "GPU Fan" (`:242`) |

### 2.8 List rebuild vs refresh
`SetSystemInfoVal` (`:296-451`) builds the label rows on first data AND pre-creates **30 spare hidden rows** (`:393-450`) so later ticks only mutate text. `ResetSystemInfoVal` (`:244-294`) is the per-tick fast path: sets name/value text, shows/hides rows, resizes panel. `DoSystemInfoUI` (`:500-526`) gates first-build vs refresh via `first` (`:502-507`) and drives a 3-tick `myDCount` countdown that fires `upDateUCInfo?.Invoke(0/16/254/255)` (`:506`,`:516`,`:520`,`:524`).

---

## 3. Overlay Element Model (`UCXiTongXianShiSub.cs`)

### 3.1 Per-element fields (`:17-37`)
Ten public fields, all initialised inline at declaration — this is the whole persisted state of one overlay element:

| Field | Type | Default | Meaning |
|---|---|---|---|
| `myMode` | int | 0 | element kind: 0=data, 1=time, 2=week, 3=date, 4=text |
| `myModeSub` | int | 0 | sub-format within the kind (time 12/24h, date order) |
| `myX` | int | 100 | position X |
| `myY` | int | 100 | position Y |
| `myMainCount` | int | 0 | metric category: 0=CPU, 1=GPU, 2=MEM, 3=HDD, 4=NET, 5=FAN; 10000=FAN sentinel |
| `mySubCount` | int | 1 | which of the 4 sub-metrics within the category (1..4) |
| `myColor` | Color | RGB (255, 255, 255) — white | element text colour |
| `myFont` | Font | family `微软雅黑`, size 36f, style 0 (Regular), unit 3 (Point), GDI charset 134 | element font |
| `isSelect` | bool | false | whether this element is the selected one in the editor |
| `myText` | string | `""` (empty) | the literal string drawn in text mode (`myMode` 4) |

`GraphicsUnit)3` = **Point** (System.Drawing.GraphicsUnit.Point). Default overlay font is Microsoft YaHei, **36 Point**. (Contrast: the config-panel labels use 10.5f Point, `:307`,`:319`,`:331`.)

### 3.2 Three-label composition (`label1`=name, `label2`=numeric, `label3`=unit)
Init layout (`:301-340`): label1 at (2,1), label2 at (2,21), label3 at (2,41), all 56×18, all default font `微软雅黑 10.5pt` Point, all default color `(50,197,255)`. `myMode` selects which are shown (`InitUCXiTongXianShiSub` `:55-117`): mode 0 shows all three; modes 1/2/3/4 **hide label1 & label3** and show only label2 (`:93-114`).

Per-tick text build `UCXiTongXianShiSubTimer(string val, string dw)` (`:188-272`), for `myMode==0` (`:199-225`) pulls from `Form1.ucSystemInfoOptions1.UCSystemInfoOptionsOneList[myMainCount]` and splits by `mySubCount` (1..4). **The number/unit split is a regex:**
- `label2` numeric text (`:206`) — a `Regex.Replace` over the source label's text with the pattern `[^\d]` and an empty replacement string: every non-digit character is deleted, so only the digits survive.
- `label3` unit text (`:207`) — the same source text with the digit string just computed removed from it by a plain string replace; whatever is left over is taken to be the unit.
So the composed HWiNFO string like `"55℃"` is re-split: label2=`"55"`, label3=`"℃"`. label1 = the metric's own name textbox.

### 3.3 The FAN sentinel `myMainCount == 10000`
`UCXiTongXianShiSubTimer` short-circuits BEFORE the mode switch (`:190-196`) — when `myMainCount` equals 10000 it writes the literal string `"FAN"` into label1, the caller-supplied `val` argument into label2 and the caller-supplied `dw` argument into label3, then returns immediately, so the `myMode` switch below is never reached for a fan element.
FAN elements do NOT read the options list; the caller passes `val`/`dw` directly. Also in `InitUCXiTongXianShiSub` both `myMainCount==5` and `==10000` map to `myColorFan` (`:78-83`).

### 3.4 Category → default label1 color (`InitUCXiTongXianShiSub` mode 0, `:61-87`)
A switch on `myMainCount` picks which shared colour field label1 is painted with:

| `myMainCount` | label1 colour field |
|---|---|
| 0 (CPU) | `myColorCpu` |
| 1 (GPU) | `myColorGpu` |
| 2 (MEM) | `myColorMem` |
| 3 (HDD) | `myColorHdd` |
| 4 (NET) | `myColorNet` |
| 5 (FAN) | `myColorFan` |
| 10000 (FAN sentinel) | `myColorFan` |
| anything else (default arm) | `myColorZDY` (custom) |

label2/label3 always take `myColor` (`:88-89`). These colors come from `UCSystemInfoOptionsOne` static fields (not in the audited files).

### 3.5 Config-panel command map (`UCXiTongXinXi.cs`, `delegateUCXinxi`)
| cmd | Trigger | Payload | Line |
|---|---|---|---|
| 0 | on/off toggle | `buttonOn` | `:136` |
| 1 | metric mode M1–M6 select | `mIndex` (1–6) | `:175`,`183`,`189`,`195`,`203`,`210` |
| 2 | sub-metric multi-select / carousel | `(idx, isLunbo?-1:mMode, state)` | `:283`,`298`,…`:358`; carousel `:396`,`:404` |
| 3 | carousel timer secs | textBox text (numeric, max 2 chars, default "2") | `:419`, box `:812-816` |
| 4 | number-font picker | — | `:425` |
| 5 | text-font picker | — | `:435` |
| 6 | number color picker | — | `:430` |
| 7 | text color picker | — | `:440` |
| 8 | unit-display toggle (`buttonDWXS`) | — | `:459` |

`mMode` (`:281` etc.) = count of selected sub-metrics; `isLunbo` (carousel mode) forces payload `-1` (`:283`). Size labels default text `"9"`, font labels default `"微软雅黑"` (`:816-888`). **cmd 8 `buttonDWXS` is the per-element unit-visibility switch** — the C# analog of "draw unit vs bare number" (relevant to the 0xDC flag-template unit divergence memo).

---

## 4. Clock (`UCXiTongXianShiSub` runtime) + config (`UCShiJianXianShi`)

### 4.1 Time format tokens (`myMode==1`, `:227-241`)
A switch on `myModeSub` formats the current time into `myText`, three arms:

| `myModeSub` | Format string | Result |
|---|---|---|
| 0 | `"HH:mm"` | 24-hour, zero-padded hour, no seconds |
| 1 | `"hh:mm tt"` with `CultureInfo.InvariantCulture` | 12-hour, zero-padded hour, AM/PM designator |
| 2 | `"HH:mm"` | 24-hour — a duplicate of arm 0 |

`myModeSub` selects. 12h uses `"hh:mm tt"` with **InvariantCulture** → AM/PM literal "AM"/"PM". label2 gets the whole string (`:240`).

### 4.2 Week format (`myMode==2`, `:242-247`)
A nested conditional on the static `Form1.Language` picks one seven-entry string array of weekday names, then indexes it:

- Language `1` → the Chinese names `星期日`, `星期一` … `星期六`.
- Language `2` → the English abbreviations `SUN`, `MON` … `SAT`.
- Any other language value → the same `SUN` … `SAT` abbreviations, so the two non-Chinese branches are identical arrays.
- The index is `DateTime.Now.DayOfWeek` rendered with the `"d"` format specifier and converted back to an int, and the array entry at that index becomes `myText`.

`DayOfWeek.ToString("d")` gives 0=Sunday..6=Saturday. Language 0/2 → English abbreviations; 1 → Chinese.

### 4.3 Date format tokens (`myMode==3`, `:249-267`)
A switch on `myModeSub` formats today's date into `myText`; the 0 arm falls through into the 1 arm, so both yield the same string:

| `myModeSub` | Format string | Result |
|---|---|---|
| 0, 1 | `"yyyy/MM/dd"` | four-digit year first, slash-separated |
| 2 | `"dd/MM/yyyy"` | day first, four-digit year last |
| 3 | `"MM/dd"` | month then day, year omitted |
| 4 | `"dd/MM"` | day then month, year omitted |

`myModeSub` selects (0 and 1 both → yyyy/MM/dd). Text-mode `myMode==4` just echoes `myText` (`:268-270`).

### 4.4 Clock config panel (`UCShiJianXianShi.cs`) — the source of `myModeSub`
- `sjMode` = 12/24h: `1`→12H image (`:188`), `2`→24H image (`:193`); toggled by buttons/`buttonXiaoshi` (`:323-335`); emits cmd 2 (`:202`,`:209`,`:334`).
- `nyrMode` = date order 1–4 (`ButtonNYR_Set` `:116-150`): 1=YMD (`PYMD`), 2=DMY (`PDMY`), 3=MD (`PMD`), 4=DM (`PDM`); `buttonRiQi` cycles 1→4→1 (`:337-346`); emits cmd 1.
- Three independent visibility toggles, each its own bool + cmd:
  - `isNyr` date on/off → cmd 3 (`:236`)
  - `isXs` hour on/off → cmd 4 (`:263`)
  - `isXq` week on/off → cmd 9 (`:310`)
- Font/color pickers: cmd 5 date-font (`:268`), 6 date-color (`:273`), 7 hour-font (`:278`), 8 hour-color (`:283`), 10 week-font (`:315`), 11 week-color (`:320`).
- Size/font display labels all default `"9"` / `"微软雅黑"`, font `9f` Point (`:577-761`).

**Mapping note:** panel `nyrMode` (1=YMD,2=DMY,3=MD,4=DM) does NOT equal element `myModeSub` (0/1=yyyy/MM/dd,2=dd/MM/yyyy,3=MM/dd,4=dd/MM). The parent form translates between them (translation code not in these files) — a port must not assume the panel index is the element index.

### 4.5 AM/PM sizing
No explicit AM/PM font-size manipulation exists in these files. `myMode==1 case 1` emits the full `"hh:mm tt"` string into a single label2 (`:234`,`:240`) at the element's `myFont`. Any "AM/PM reads bigger" behavior (per the font-size memo) is NOT in this file — it is a downstream render effect, not authored here. **Do not attribute AM/PM sizing to this control.**

---

## 5. Caveats a naive port misses

1. **GPU-power falls back to CPU-power** when all three GPU-power names miss (`:944` — the GPU-power field is assigned the CPU-power value) — a port that returns 0/None diverges from the C#.
2. **FAN sentinel `myMainCount == 10000`** bypasses the options list entirely and takes caller-supplied `val`/`dw`, forcing label1=`"FAN"` (`:190-196`). `5` and `10000` are distinct code paths that only share a color.
3. **Number/unit split is regex, not semantic** (`:206-207`): label2 = `[^\d]`-stripped digits, label3 = the leftover. A value like `"1.2GHz"` would split as label2=`"12"`, label3=`".GHz"` — but values are pre-truncated at `.` upstream (`:716`,`:846`) so fractions never reach here. Port must truncate at the decimal BEFORE the digit/unit split.
4. **`"core "` filter differs in case** between temp (`.ToLower().Contains("core ")` `:736`) and usage (`Contains("core ")` `:854`). Copying one to both changes which rows survive.
5. **Font unit is Point, not pixel** (`GraphicsUnit)3` everywhere: element default `36f` Point `:31`; panel labels `9f`/`10.5f` Point). `AutoScaleMode=(AutoScaleMode)3` = DPI (`:341`,`:942`). Point→pixel is DPI-dependent (96-DPI baseline per project convention) — never treat the number as pixels.
6. **Temperature C→F is integer math on a pre-truncated int** (`num5*9/5+32`, `:724`) — floating conversion or rounding will drift by up to 1°F from the device.
7. **Values are always truncated at the decimal** (`text3.Substring(0, text3.IndexOf("."))`) in every scan and in `SetSystemVal` (`:552`) — the device shows integers only; a port emitting `55.4` is wrong.
8. **Sentinel defaults leak if unread**: memory scalars default `100000f` (`:71-91`), disk `10000f`/`100000` (`:574-577`,`:624`). These are "no reading" markers, not real values — a port must treat them as absent, not display them.
9. **Date panel index ≠ element sub-format index** (§4.4) — `nyrMode` and `myModeSub` are different enumerations translated by the parent form.
10. **12h clock uses InvariantCulture** (`:234`) so AM/PM is always Latin "AM"/"PM" regardless of `Form1.Language`; week names DO switch on `Form1.Language` (`:244`). Localization is inconsistent by design.
11. **`SetSystemVal` multiplier `n`** (`:534`,`:553`) scales the parsed value (default 1) — unused in the audited calls but part of the contract if a metric needs unit scaling.
