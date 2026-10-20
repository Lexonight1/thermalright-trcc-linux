# NON-ADMIN: run pipe_security.py with the dev venv against the built tree; upload the result.
Invoke-WebRequest -UseBasicParsing -Uri "http://10.0.2.2:8765/pipe_security.py" -OutFile "$env:TEMP\pipe_security.py"
$env:PYTHONPATH = "C:\trccdev\build\src"
& "$env:TEMP\trccipc\venv\Scripts\python.exe" "$env:TEMP\pipe_security.py" *> "$env:TEMP\pipe_security.txt"
Get-Content "$env:TEMP\pipe_security.txt"
Invoke-WebRequest -UseBasicParsing -Method Put -InFile "$env:TEMP\pipe_security.txt" -Uri "http://10.0.2.2:8765/pipe_security.txt" | Out-Null
"UPLOADED"
