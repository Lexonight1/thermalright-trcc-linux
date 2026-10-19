# NON-ADMIN (proves no rights are needed): every USB disk is found by the
# socket libusb names it -- the twins check (#287) -> inbox/drive_probe.txt
# Twins on the VM: two identical QEMU USB disks (46f4:0001), hot-plugged live
# from the host -- see drive_probe.py.
$py = "$env:TEMP\trccipc\venv\Scripts\python.exe"
Invoke-WebRequest -UseBasicParsing -Uri "http://10.0.2.2:8765/drive_probe.py" -OutFile C:\trccdev\drive_probe.py
& {
    "integrity: " + ((whoami /groups | Select-String "Mandatory Level") -replace '\s+', ' ')
    & $py -m trcc kill 2>&1 | Out-String
    Start-Sleep 3
    & $py C:\trccdev\drive_probe.py 0402 3922 46f4 0001 2>&1 | Out-String -Width 250
} *> C:\trccdev\drive_probe.txt
Invoke-WebRequest -UseBasicParsing -Method Put -InFile C:\trccdev\drive_probe.txt -Uri "http://10.0.2.2:8765/drive_probe.txt" | Out-Null
Get-Content C:\trccdev\drive_probe.txt
