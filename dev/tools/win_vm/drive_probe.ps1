# EITHER shell (run it NON-ADMIN to prove no rights are needed): which
# \\.\PhysicalDriveN is the panel, found through SetupAPI -> inbox/drive_probe.txt
$py = "$env:TEMP\trccipc\venv\Scripts\python.exe"
& {
    "integrity: " + ((whoami /groups | Select-String "Mandatory Level") -replace '\s+', ' ')
    & $py -c "import time, trcc.adapters.system._windows_drives as d; s = time.perf_counter(); r = d.find_physical_drive(0x0402, 0x3922); print('0402:3922 ->', r, f'in {1000*(time.perf_counter()-s):.0f} ms'); print('87cd:70db ->', d.find_physical_drive(0x87CD, 0x70DB))" 2>&1 | Out-String
} *> C:\trccdev\drive_probe.txt
Invoke-WebRequest -UseBasicParsing -Method Put -InFile C:\trccdev\drive_probe.txt -Uri "http://10.0.2.2:8765/drive_probe.txt" | Out-Null
Get-Content C:\trccdev\drive_probe.txt
