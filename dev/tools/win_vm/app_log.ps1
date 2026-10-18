# EITHER shell: the App's log (this user's %APPDATA%) -> inbox/trcc.log
Invoke-WebRequest -UseBasicParsing -Method Put -InFile "$env:APPDATA\trcc\trcc.log" -Uri "http://10.0.2.2:8765/trcc.log" | Out-Null
"UPLOADED"
