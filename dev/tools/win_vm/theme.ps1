# NON-ADMIN: load Theme1 (clock -> the App redraws every 2 s) through the running App.
$py = "$env:TEMP\trccipc\venv\Scripts\python.exe"
"integrity: " + ((whoami /groups | Select-String "Mandatory Level") -replace '\s+', ' ')
& $py -m trcc display load-theme 0402:3922 "$env:APPDATA\trcc\data\theme320320\Theme1" 2>&1 | Out-String
"THEME LOADED AT " + (Get-Date).ToString("HH:mm:ss")
