# NON-ADMIN: does `trcc kill` stop a BUSY App?  N cycles per CLI: start the installed App, load Theme1 (it renders
# every 2 s), kill, then look for its process.  "installed" = C:\Program Files\TRCC\trcc.exe; "tree" = payload/src.zip
# (win_vm.py pack) run from source with the dev venv.  -> inbox/kill_cycle.txt.  Leaves the App stopped.
$up = "http://10.0.2.2:8765"
$cycles = if ($env:TRCC_KILL_CYCLES) { [int]$env:TRCC_KILL_CYCLES } else { 8 }
$exe = "C:\Program Files\TRCC\trcc.exe"
$gui = "C:\Program Files\TRCC\trcc-gui.exe"
$py = "$env:TEMP\trccipc\venv\Scripts\python.exe"
$tree = "C:\trccdev\killtest"
$theme = "$env:APPDATA\trcc\data\theme320320\Theme1"
$out = "C:\trccdev\kill_cycle.txt"
function AppPids { @(Get-CimInstance Win32_Process -Filter "Name='trcc-gui.exe'" | Where-Object { $_.CommandLine -like '* daemon*' } | ForEach-Object ProcessId) }
& {
    "integrity: " + ((whoami /groups | Select-String "Mandatory Level") -replace '\s+', ' ')
    Invoke-WebRequest -UseBasicParsing -Uri "$up/src.zip" -OutFile "C:\trccdev\killtest.zip"
    if (Test-Path $tree) { Remove-Item -Recurse -Force $tree }
    Expand-Archive -Force "C:\trccdev\killtest.zip" $tree
    foreach ($cli in "installed", "tree") {
        $lies = 0
        foreach ($i in 1..$cycles) {
            foreach ($p in AppPids) { Stop-Process -Id $p -Force }
            Start-Process $gui -ArgumentList "daemon"
            Start-Sleep 6
            & $exe display load-theme 0402:3922 $theme 2>&1 | Out-Null
            Start-Sleep (Get-Random -Minimum 2 -Maximum 5)
            $before = (AppPids) -join ","
            if ($cli -eq "installed") { $said = (& $exe kill 2>&1 | Out-String).Trim() }
            else { $env:PYTHONPATH = "$tree\src"; $said = (& $py -m trcc kill 2>&1 | Out-String).Trim(); Remove-Item env:PYTHONPATH }
            Start-Sleep 3
            $after = (AppPids) -join ","
            $lie = ($said -match "stopped") -and $after
            if ($lie) { $lies++ }
            "{0} #{1}: App [{2}] -> said '{3}' -> still running [{4}]{5}" -f $cli, $i, $before, ($said -split "`n")[-1], $after, $(if ($lie) { "  <-- LIE" } else { "" })
        }
        "== $cli : $lies of $cycles kills said stopped while the App ran"
    }
    foreach ($p in AppPids) { Stop-Process -Id $p -Force }
} *> $out
Invoke-WebRequest -UseBasicParsing -Method Put -InFile $out -Uri "$up/kill_cycle.txt" | Out-Null
Get-Content $out -Tail 3
"UPLOADED"
