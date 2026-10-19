# ADMIN: what Windows answers when a SCSI disk vanishes mid-stream (unplug it
# from the host while this runs -- see gone_probe.py) -> inbox/gone_probe.txt
$py = "$env:TEMP\trccipc\venv\Scripts\python.exe"
Invoke-WebRequest -UseBasicParsing -Uri "http://10.0.2.2:8765/gone_probe.py" -OutFile C:\trccdev\gone_probe.py
& $py C:\trccdev\gone_probe.py $(if ($env:TRCC_GONE) { $env:TRCC_GONE -split ' ' } else { '46f4', '0001', '60' }) *> C:\trccdev\gone_probe.txt
Invoke-WebRequest -UseBasicParsing -Method Put -InFile C:\trccdev\gone_probe.txt -Uri "http://10.0.2.2:8765/gone_probe.txt" | Out-Null
Get-Content C:\trccdev\gone_probe.txt
