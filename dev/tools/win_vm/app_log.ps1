# EITHER shell: the App's log (this user's %APPDATA%), rotations included -> inbox/trcc.log, trcc.log.1, ...
Get-ChildItem "$env:APPDATA\trcc" -Filter "trcc.log*" | ForEach-Object {
    Invoke-WebRequest -UseBasicParsing -Method Put -InFile $_.FullName -Uri "http://10.0.2.2:8765/$($_.Name)" | Out-Null
    $_.Name
}
"UPLOADED"
