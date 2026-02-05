# Porting a .NET Windows Application to Linux with Python + PyQt6

A practical guide distilled from porting Thermalright's TRCC (C# / WinForms / .NET) to native Linux (Python / PyQt6).

## Decompilation

Use **ILSpy** or **dnSpy** to decompile the .NET executable into readable C#. The decompiled output gives you:

- **InitializeComponent()** — every control's exact coordinates, sizes, fonts, colors, and parent hierarchy. This is your layout blueprint.
- **.resx resource files** — embedded images (PNG/BMP), strings, and binary blobs. Extract everything.
- **Delegate/event wiring** — search for `delegate*.Invoke(cmd, ...)` to find command constants that drive the UI logic.
- **Binary data formats** — struct layouts, magic bytes, field offsets. Reimplement parsers by reading the C# struct definitions.

## UI Translation: WinForms to PyQt6

| WinForms | PyQt6 Equivalent |
|----------|-------------------|
| `Form` | `QMainWindow` or `QDialog` |
| `UserControl` | `QWidget` subclass |
| `Panel` | `QWidget` with layout or absolute positioning |
| `PictureBox` | `QLabel` with `setPixmap()` |
| `Button` | `QPushButton` |
| `CheckBox` | `QPushButton` with toggled icon images |
| `TextBox` | `QLineEdit` |
| `ComboBox` | `QComboBox` |
| `FlowLayoutPanel` | `QWidget` + `QFlowLayout` or manual grid |
| `TabControl` | `QStackedWidget` with manual tab buttons |
| `ScrollBar` | Styled `QScrollArea` |
| `Timer` | `QTimer` |
| `BackgroundWorker` | `QThread` or `threading.Thread` |

### Coordinate mapping

WinForms uses absolute pixel positioning via `Location = new Point(x, y)` and `Size = new Size(w, h)` in InitializeComponent(). Translate directly to `widget.setGeometry(x, y, w, h)` in PyQt6. No layout managers needed — absolute positioning matches 1:1.

### Background images

WinForms sets `BackgroundImage` on controls. In PyQt6:

```python
palette = widget.palette()
palette.setBrush(QPalette.ColorRole.Window, QBrush(pixmap))
widget.setPalette(palette)
widget.setAutoFillBackground(True)
```

**Never use `setStyleSheet()` on container widgets** — it overrides QPalette on all descendants, breaking image backgrounds on child widgets. This is the single most common PyQt6 porting pitfall.

### Asset extraction

Extract every embedded resource from the .resx files. Windows apps typically embed PNGs for:
- Button states (normal, hover, active, disabled)
- Panel/tab backgrounds
- Icons, checkboxes, radio buttons
- Localized variants (suffixed by language code)

## Architecture: Don't Port the Pattern, Port the Behavior

.NET WinForms apps are typically tightly coupled — event handlers directly manipulate UI controls, business logic lives in code-behind files, and forms reference each other. Don't replicate this.

**Separate into MVC:**
- **Models** — pure data classes (Python dataclasses)
- **Controllers** — all business logic, zero GUI imports, callback-based notification
- **Views** — PyQt6 widgets that subscribe to controller callbacks

This lets you swap GUI frameworks (PyQt6, Tkinter, GTK) without rewriting logic.

## Platform Replacements

### Hardware access

| Windows | Linux |
|---------|-------|
| Win32 `CreateFile` / `DeviceIoControl` | `/dev/sg*` via `sg_raw` (SCSI generic) |
| `SetupDiGetClassDevs` (USB enumeration) | `lsusb` + `/sys/bus/usb/devices/` sysfs |
| HID device access | `hidapi` or `/dev/hidraw*` |
| COM ports | `/dev/ttyUSB*` or `/dev/ttyACM*` |

### System sensors

| Windows | Linux |
|---------|-------|
| HWiNFO64 shared memory | `/sys/class/hwmon/*/` (temps, fans, voltages) |
| NVIDIA System Management (WMI) | `pynvml` (direct NVML bindings) |
| WMI `Win32_Processor` | `psutil` (CPU, memory, disk, network) |
| Intel Power Gadget | `/sys/class/powercap/intel-rapl:*/energy_uj` |
| Performance Counters | `psutil.disk_io_counters()`, `psutil.net_io_counters()` |

### File paths

| Windows | Linux |
|---------|-------|
| `%AppData%\Company\App\` | `~/.config/app/` (XDG_CONFIG_HOME) |
| `%LocalAppData%\Company\App\` | `~/.local/share/app/` (XDG_DATA_HOME) |
| `Application.StartupPath` | `importlib.resources` or `__file__` relative |
| Registry (`HKCU\Software\...`) | JSON config file |
| Windows Startup folder | Autostart `.desktop` file in `~/.config/autostart/` |

### Media

| Windows | Linux |
|---------|-------|
| GDI+ `Bitmap`, `Graphics` | Pillow (`PIL.Image`) |
| `System.Drawing.Color` | Pillow tuples or PyQt6 `QColor` |
| DirectShow / MediaFoundation | FFmpeg subprocess (`ffprobe` + `ffmpeg`) |
| `System.Media.SoundPlayer` | Not needed (or `pygame.mixer`) |

## Binary Protocol Porting

For USB/SCSI device protocols:

1. **Read the C# send/receive methods** — find the byte arrays being constructed
2. **Identify the command structure** — magic bytes, command IDs, payload layout, checksums
3. **Map data types** — C# `byte[]` → Python `bytes`, `BitConverter` → `struct.pack/unpack`
4. **Test with actual hardware** — protocol docs are rarely complete; the decompiled code is the real spec

Example — C# SCSI write:
```csharp
byte[] cdb = new byte[10];
cdb[0] = 0xEF;  // vendor-specific
cdb[1] = (byte)(cmd >> 24);
// ...
```

Python equivalent:
```python
import struct
cdb = struct.pack('>BBI', 0xEF, cmd >> 24, payload_len)
subprocess.run(['sg_raw', '-s', str(len(data)), device, *hex_cdb], input=data)
```

## Localization

Windows apps often use `.resx` resource files per language. Extract all localized assets (usually image files with language suffixes) and implement a lookup function:

```python
def get_localized(base_name: str, lang: str) -> str:
    """P0背景.png + 'en' → P0背景en.png (if exists, else fallback to base)"""
    if lang:
        localized = base_name.replace('.png', f'{lang}.png')
        if asset_exists(localized):
            return localized
    return base_name
```

## Common Pitfalls

1. **Don't guess coordinates** — always read InitializeComponent(). One pixel off and buttons overlap or hide behind panels.
2. **Watch for off-by-one in tab/panel mapping** — WinForms TabControl indices often don't match the visual button order.
3. **C# delegates ≠ Python signals** — map `delegate.Invoke(cmd, data)` patterns to PyQt6 `pyqtSignal` or controller callbacks.
4. **Thread safety** — WinForms `Invoke`/`BeginInvoke` for UI updates becomes `QMetaObject.invokeMethod` or `pyqtSignal.emit` from worker threads.
5. **Font rendering differs** — Windows ClearType and Linux FreeType render differently. Accept minor text width variations.
6. **Color depth** — if the device expects RGB565, use numpy for fast conversion, not per-pixel Python loops.
7. **Stub what you can't port** — some Windows features (e.g., shared memory IPC with HWiNFO) have no direct equivalent. Build the Linux-native replacement instead of trying to emulate the Windows mechanism.

## Advanced Topics

### Dynamic Scaling

If your app supports multiple resolutions, implement dynamic scaling for fonts and coordinates:

```python
def _get_scale_factor(self):
    """Scale from config resolution to display resolution."""
    cfg_size = min(self._config_resolution)
    disp_size = min(self.width, self.height)
    return disp_size / cfg_size if cfg_size > 0 else 1.0

# In render:
scale = self._get_scale_factor()
x = int(base_x * scale)
y = int(base_y * scale)
font_size = max(8, int(base_font_size * scale))
```

### Screen Capture (Wayland)

X11's `XGetImage` doesn't work on Wayland. Use the XDG Desktop Portal:

```python
# D-Bus portal flow for PipeWire screen capture
bus = dbus.SessionBus()
portal = bus.get_object('org.freedesktop.portal.Desktop', '/org/freedesktop/portal/desktop')
screencast = dbus.Interface(portal, 'org.freedesktop.portal.ScreenCast')
# CreateSession → SelectSources → Start → PipeWire stream
```

### Binary File Padding

Windows apps often pad binary files with magic bytes. When reverse-engineering, look for patterns:

```python
# Example: .tr export has 10240 bytes of 0xDC padding between header and images
f.write(bytes([0xDC] * 10240))
```

### Identify Dead Code

Before porting a feature, check if it's actually used:
- `Visible = false` in InitializeComponent = hidden/disabled control
- Empty delegate handlers = stub code
- Controls with no event wiring = display-only or unfinished

In TRCC, "Animation Linkage" and "Keyboard Linkage" panels were stubs (Visible=false, no logic) — no need to port.

## Project Structure Recommendation

```
src/
├── core/
│   ├── models.py       # Data classes (ThemeInfo, DeviceInfo, etc.)
│   └── controllers.py  # GUI-independent business logic
├── qt_components/      # PyQt6 views (one file per major panel)
├── parsers/            # Binary file format parsers
└── drivers/            # Hardware communication (SCSI, HID, etc.)
```

Keep controllers free of GUI imports — they should work with any frontend.
