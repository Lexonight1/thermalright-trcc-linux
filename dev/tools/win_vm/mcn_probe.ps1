# ADMIN: why Windows refuses IOCTL_STORAGE_MCN_CONTROL on the panel's drive (mcn_probe.py) -> inbox/mcn_probe.txt
# Sends the panel no SCSI command; safe while the service holds it.
$py = "$env:TEMP\trccipc\venv\Scripts\python.exe"
Invoke-WebRequest -UseBasicParsing -Uri "http://10.0.2.2:8765/mcn_probe.py" -OutFile C:\trccdev\mcn_probe.py
& $py C:\trccdev\mcn_probe.py *> C:\trccdev\mcn_probe.txt
Invoke-WebRequest -UseBasicParsing -Method Put -InFile C:\trccdev\mcn_probe.txt -Uri "http://10.0.2.2:8765/mcn_probe.txt" | Out-Null
Get-Content C:\trccdev\mcn_probe.txt
