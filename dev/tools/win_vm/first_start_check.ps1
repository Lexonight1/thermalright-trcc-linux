# NON-ADMIN (as a user starts TRCC): the first start after an install must leave exactly ONE App.
# 2026-10-20: the App took ~9 s against a 10 s wait; the GUI fell back to an App of its own and the spawned one
# came up beside it -- two owners of one panel.  Fixed in fc5d2401: the GUI waits on the process it spawned (up to
# 60 s), and stops it before any fallback.  Run right after first_start_install.ps1.  Drives the panel.
# -> inbox/first_start_check.txt.  Leaves TRCC running.
$up = "http://10.0.2.2:8765"
$exe = "C:\Program Files\TRCC\trcc.exe"
$gui = "C:\Program Files\TRCC\trcc-gui.exe"
$log = "$env:APPDATA\trcc\trcc.log"
$out = "C:\trccdev\first_start_check.txt"
function Procs($kind) { @(Get-CimInstance Win32_Process -Filter "Name='trcc-gui.exe'" | Where-Object { $_.CommandLine -like "* $kind*" } | ForEach-Object ProcessId) }
function Stamp { (Get-Date).ToString("yyyy-MM-ddTHH:mm:ss.fff") }
# Timestamped lines at or after $since only: the file is shared by every TRCC process and older runs.
function Since($since, $pattern) { @(Get-Content $log -ErrorAction SilentlyContinue | Where-Object { $_ -match '^\d{4}-\d\d-\d\dT' -and $_.Substring(0, 23) -ge $since -and $_ -match $pattern }) }
& {
    Set-Location C:\trccdev
    "integrity: " + ((whoami /groups | Select-String "Mandatory Level") -replace '\s+', ' ')
    "version: " + ((& $exe --version 2>&1 | Out-String).Trim())
    "before: app [$((Procs 'daemon') -join ',')] gui [$((Procs 'gui') -join ',')]"
    $start = Stamp
    $p = Start-Process $gui -ArgumentList "gui" -PassThru
    $verdict = 'the App answered|exited with code|did not answer|falling back'
    foreach ($i in 1..300) { if (Since $start $verdict) { break }; Start-Sleep -Milliseconds 250 }
    Start-Sleep 15        # long enough for a late second App to show itself
    "-- the start, from the log (GUI pid $($p.Id)) --"
    Since $start 'Spawning the shared App|_wait_for_the_spawn|falling back|shared App failed|init delivered' |
        ForEach-Object { $_.Substring(11) }
    $apps = Procs 'daemon'
    "apps: [$($apps -join ',')]   gui: [$((Procs 'gui') -join ',')]"
    $owners = @(Since $start 'init delivered' | ForEach-Object { if ($_ -match '\[(\d+)\]') { $Matches[1] } } | Sort-Object -Unique)
    "processes that initialised the panel: [$($owners -join ',')]"
    if ($apps.Count -eq 1 -and -not (Since $start 'falling back') -and $owners.Count -le 1) { "VERDICT: ONE App" }
    else { "VERDICT: NOT ONE App -- read the lines above" }
    Set-Location C:\trccdev
} *> $out
Invoke-WebRequest -UseBasicParsing -Method Put -InFile $out -Uri "$up/first_start_check.txt" | Out-Null
Get-Content $out
"UPLOADED"
