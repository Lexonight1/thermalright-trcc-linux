# NON-ADMIN: force-stop the App, read the panel's poll buffer with panel_log.py, start the GUI again.
# FORCE, not `trcc kill`: a closing App sends a black sleep frame, and bytes 1.. of the poll reply are whatever
# the panel last received -- so a kill always reads back zeros.  Stop-Process leaves the App's last real frame.
# Uses the packed tree (win_vm.py pack) with the dev venv.  -> inbox/panel_log.txt + inbox/panel_poll.bin
$up = "http://10.0.2.2:8765"
$py = "$env:TEMP\trccipc\venv\Scripts\python.exe"
$tree = "C:\trccdev\paneltree"
$out = "C:\trccdev\panel_log.txt"
& {
    "integrity: " + ((whoami /groups | Select-String "Mandatory Level") -replace '\s+', ' ')
    Invoke-WebRequest -UseBasicParsing -Uri "$up/src.zip" -OutFile "C:\trccdev\paneltree.zip"
    if (Test-Path $tree) { Remove-Item -Recurse -Force $tree }
    Expand-Archive -Force "C:\trccdev\paneltree.zip" $tree
    Invoke-WebRequest -UseBasicParsing -Uri "$up/panel_log.py" -OutFile "C:\trccdev\panel_log.py"
    Get-CimInstance Win32_Process -Filter "Name='trcc-gui.exe'" | Where-Object { $_.CommandLine -match ' (gui|daemon)' } |
        ForEach-Object { "== force-stop $($_.ProcessId) $($_.CommandLine)"; Stop-Process -Id $_.ProcessId -Force }
    Start-Sleep 3
    $env:PYTHONPATH = "$tree\src"
    & $py C:\trccdev\panel_log.py 2>&1 | Out-String -Width 400
    Remove-Item env:PYTHONPATH
    Start-Process "C:\Program Files\TRCC\trcc-gui.exe" -ArgumentList "gui", "--resume"
    "== GUI started again"
} *> $out
Invoke-WebRequest -UseBasicParsing -Method Put -InFile $out -Uri "$up/panel_log.txt" | Out-Null
Invoke-WebRequest -UseBasicParsing -Method Put -InFile "C:\trccdev\panel_poll.bin" -Uri "$up/panel_poll.bin" | Out-Null
Get-Content $out -Tail 5
"UPLOADED"
