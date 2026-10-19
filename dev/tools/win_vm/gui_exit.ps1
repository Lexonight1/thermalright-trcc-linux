# NON-ADMIN: does trcc-gui crash when it exits?  N rounds: start `trcc-gui.exe gui --resume` (as sign-in does; it starts
# the App), wait, `trcc kill` (the GUI must close with the App), then look for the GUI's process and for an Application
# Error 1000 newer than the round.  -> inbox/gui_exit.txt.  Leaves TRCC stopped.
$up = "http://10.0.2.2:8765"
$rounds = if ($env:TRCC_GUI_ROUNDS) { [int]$env:TRCC_GUI_ROUNDS } else { 3 }
# The GUI takes >12 s to start on the VM; a kill before then is the startup case, not the steady state.
$wait = if ($env:TRCC_GUI_WAIT) { [int]$env:TRCC_GUI_WAIT } else { 45 }
$exe = "C:\Program Files\TRCC\trcc.exe"
$gui = "C:\Program Files\TRCC\trcc-gui.exe"
$out = "C:\trccdev\gui_exit.txt"
function Trcc { @(Get-CimInstance Win32_Process -Filter "Name='trcc-gui.exe'" | Where-Object { $_.CommandLine -and $_.CommandLine -notlike '* service*' }) }
& {
    "integrity: " + ((whoami /groups | Select-String "Mandatory Level") -replace '\s+', ' ')
    foreach ($i in 1..$rounds) {
        Trcc | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
        $start = Get-Date
        $p = Start-Process $gui -ArgumentList "gui", "--resume" -PassThru
        Start-Sleep $wait
        $said = (& $exe kill 2>&1 | Out-String).Trim()
        Start-Sleep 15
        $left = (Trcc | ForEach-Object { "$($_.ProcessId) $($_.CommandLine)" }) -join "; "
        $crash = Get-WinEvent -FilterHashtable @{LogName = "Application"; Id = 1000; StartTime = $start} -ErrorAction SilentlyContinue |
            ForEach-Object { ($_.Message -split "`n" | Select-String "Faulting (module name|process id)|Exception code") -join " | " }
        "round {0}: gui pid {1} exit code {2} | kill said '{3}' | left: [{4}] | crash: {5}" -f $i, $p.Id, $(if ($p.HasExited) { $p.ExitCode } else { "running" }), ($said -split "`n")[-1], $left, $(if ($crash) { $crash -join " || " } else { "none" })
    }
} *> $out
Invoke-WebRequest -UseBasicParsing -Method Put -InFile $out -Uri "$up/gui_exit.txt" | Out-Null
Get-Content $out -Tail 3
"UPLOADED"
