# NON-ADMIN: 2b -- the App dies (Stop-Process -Force, a crash, not `trcc kill`) while a GUI is still building its window.
# The GUI must find or start a new App and keep running -- not hang 6-80 s (measured 10-19), not die.  ONE cycle: it
# stops the App that drives the panel once.  `trcc kill` first, or an open GUI absorbs the launch (SingleInstance).
# -> inbox/gui_app_crash.txt.  Leaves TRCC running on the App the GUI restarted.
$up = "http://10.0.2.2:8765"
$exe = "C:\Program Files\TRCC\trcc.exe"
$gui = "C:\Program Files\TRCC\trcc-gui.exe"
$log = "$env:APPDATA\trcc\trcc.log"
$out = "C:\trccdev\gui_app_crash.txt"
function Procs($kind) { @(Get-CimInstance Win32_Process -Filter "Name='trcc-gui.exe'" | Where-Object { $_.CommandLine -like "* $kind*" } | ForEach-Object ProcessId) }
function Stamp { (Get-Date).ToString("yyyy-MM-ddTHH:mm:ss.fff") }
# Log lines at or after $since -- the file is shared by every TRCC process, and older runs wrote the same words.
function Since($since, $pattern) { @(Get-Content $log -ErrorAction SilentlyContinue | Where-Object { $_.Length -ge 23 -and $_.Substring(0, 23) -ge $since -and $_ -match $pattern }) }
& {
    "integrity: " + ((whoami /groups | Select-String "Mandatory Level") -replace '\s+', ' ')
    "kill: " + ((& $exe kill 2>&1 | Out-String).Trim() -split "`n")[-1]
    Start-Sleep 3
    "before: app [$((Procs 'daemon') -join ',')] gui [$((Procs 'gui') -join ',')]"
    $start = Stamp
    $p = Start-Process $gui -ArgumentList "gui", "--resume" -PassThru
    $building = $null
    foreach ($i in 1..240) {
        if (($hit = Since $start "GuiUI.run: building the window")) { $building = $hit[0]; break }
        Start-Sleep -Milliseconds 250
    }
    if (-not $building) { "NO 'building the window' within 60 s -- nothing killed"; return }
    $app = Procs 'daemon'
    $killed = Stamp
    if (-not $app) { "building at $($building.Substring(11, 12)) but NO App process yet -- nothing killed"; return }
    $app | ForEach-Object { Stop-Process -Id $_ -Force }
    "gui pid $($p.Id); window build began $($building.Substring(11, 12)); App [$($app -join ',')] force-killed $($killed.Substring(11, 12))"
    $deadline = (Get-Date).AddSeconds(90)
    while ((Get-Date) -lt $deadline -and -not (Since $killed "init delivered")) { Start-Sleep 1 }
    Start-Sleep 5
    "after: app [$((Procs 'daemon') -join ',')] gui [$((Procs 'gui') -join ',')] (gui $($p.Id) $(if ($p.HasExited) { "EXITED $($p.ExitCode)" } else { "running" }))"
    "-- what the log says after the kill --"
    Since $killed "went away unexpectedly|stopped unexpectedly|ensure_daemon|daemon: listening|reap_after|init delivered|RestoreDeviceState: key|LoadTheme: Theme|Traceback|CRITICAL" | Select-Object -First 25
    $crash = Get-WinEvent -FilterHashtable @{LogName = "Application"; Id = 1000; StartTime = [datetime]$start} -ErrorAction SilentlyContinue
    "Application Error 1000 since the start: $(@($crash).Count)"
} *> $out
Invoke-WebRequest -UseBasicParsing -Method Put -InFile $out -Uri "$up/gui_app_crash.txt" | Out-Null
Get-Content $out
"UPLOADED"
