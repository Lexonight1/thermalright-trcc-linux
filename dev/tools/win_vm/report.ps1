# NON-ADMIN: stop the old App, then trcc report through the rebuilt service.
$up = "http://10.0.2.2:8765"
$py = "$env:TEMP\trccipc\venv\Scripts\python.exe"
$out = "C:\trccdev\report.txt"
& {
    "integrity: " + ((whoami /groups | Select-String "Mandatory Level") -replace '\s+', ' ')
    & $py -m trcc kill 2>&1 | Out-String
    Start-Sleep 3
    & $py -m trcc report 2>&1 | Out-String -Width 250
} *> $out
Invoke-WebRequest -UseBasicParsing -Method Put -InFile $out -Uri "$up/report.txt" | Out-Null
"UPLOADED"
