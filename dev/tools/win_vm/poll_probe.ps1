# ADMIN, one-off: does a 'failed' (121) DIRECT poll still deliver the reply?
$py = "$env:TEMP\trccipc\venv\Scripts\python.exe"
Invoke-WebRequest -UseBasicParsing -Uri "http://10.0.2.2:8765/poll_probe.py" -OutFile C:\trccdev\poll_probe.py
& $py C:\trccdev\poll_probe.py *> C:\trccdev\poll_probe.txt
Invoke-WebRequest -UseBasicParsing -Method Put -InFile C:\trccdev\poll_probe.txt -Uri "http://10.0.2.2:8765/poll_probe.txt" | Out-Null
Get-Content C:\trccdev\poll_probe.txt
