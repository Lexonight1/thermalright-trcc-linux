# ADMIN, while the service holds the panel (run `theme` in the NON-ADMIN shell first):
# is a second, elevated owner refused?  -> inbox/claim_probe.txt
$py = "$env:TEMP\trccipc\venv\Scripts\python.exe"
Invoke-WebRequest -UseBasicParsing -Uri "http://10.0.2.2:8765/claim_probe.py" -OutFile C:\trccdev\claim_probe.py
& $py C:\trccdev\claim_probe.py *> C:\trccdev\claim_probe.txt
Invoke-WebRequest -UseBasicParsing -Method Put -InFile C:\trccdev\claim_probe.txt -Uri "http://10.0.2.2:8765/claim_probe.txt" | Out-Null
Get-Content C:\trccdev\claim_probe.txt
