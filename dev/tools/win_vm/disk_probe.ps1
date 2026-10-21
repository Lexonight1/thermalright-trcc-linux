# NON-ADMIN: what the Windows drive-temperature reader (W3) sees, from the CURRENT tree.  -> inbox/disk_probe.txt
# Every drive is asked ONLY its bus type (StorageDeviceProperty, answered from the class driver's cached
# descriptor).  The temperature is asked only through discover_windows_disks, which never asks a USB drive --
# the LCD panel is one.  Also says whether this shell is elevated, since the point is that it needs no admin.
$up = "http://10.0.2.2:8765"
$py = "$env:TEMP\trccipc\venv\Scripts\python.exe"
$tree = "C:\trccdev\disktree-$(Get-Date -Format yyyyMMddHHmmss)"
$out = "C:\trccdev\disk_probe.txt"
& {
    Set-Location C:\trccdev
    "integrity: " + ((whoami /groups | Select-String "Mandatory Level") -replace '\s+', ' ')
    Invoke-WebRequest -UseBasicParsing -Uri "$up/src.zip" -OutFile "$tree.zip"
    Expand-Archive -Force "$tree.zip" $tree
    $env:PYTHONPATH = "$tree\src"
    & $py -c @"
import logging
logging.basicConfig(level=logging.INFO, format='%(name)s: %(message)s')
from trcc.adapters.sensors import _storage_query as sq
drives = sq._Drives()
for n in range(8):
    data = drives.query(n, sq._PROPERTY_DEVICE)
    print('PhysicalDrive%d' % n, 'absent' if data is None else sq.describe(data))
disks = sq.discover_windows_disks()
for d in disks:
    print('READ', d.key, d.name, d.temp())
print('drives with a temperature:', len(disks))
"@
    Set-Location C:\trccdev
} *> $out
Invoke-WebRequest -UseBasicParsing -Method Put -InFile $out -Uri "$up/disk_probe.txt" | Out-Null
Get-Content $out
"UPLOADED"
