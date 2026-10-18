# NON-ADMIN: one red frame through the running App; print when it was sent.
$py = "$env:TEMP\trccipc\venv\Scripts\python.exe"
"integrity: " + ((whoami /groups | Select-String "Mandatory Level") -replace '\s+', ' ')
& $py -m trcc display color 0402:3922 ff0000 2>&1 | Out-String
"RED SENT AT " + (Get-Date).ToString("HH:mm:ss")
