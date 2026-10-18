# NON-ADMIN: wake with Theme1 (2 s redraws), hold 10 s, then ONE red frame.
$py = "$env:TEMP\trccipc\venv\Scripts\python.exe"
& $py -m trcc display load-theme 0402:3922 "$env:APPDATA\trcc\data\theme320320\Theme1" 2>&1 | Out-String
"THEME AT " + (Get-Date).ToString("HH:mm:ss")
Start-Sleep 10
& $py -m trcc display color 0402:3922 ff0000 2>&1 | Out-String
"RED AT " + (Get-Date).ToString("HH:mm:ss")
