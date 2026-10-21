# NON-ADMIN: one App at -vvv (per-frame lines), a 20 s cast, then the App back as sign-in starts it.
# Restarts the App on the panel TWICE (into -vvv, back to normal).  -> inbox/cast_breakdown.log (the -vvv window only)
$up = "http://10.0.2.2:8765"
$exe = "C:\Program Files\TRCC\trcc.exe"
$gui = "C:\Program Files\TRCC\trcc-gui.exe"
$log = "$env:APPDATA\trcc\trcc.log"
$out = "C:\trccdev\cast_breakdown.log"
function AppPids { @(Get-CimInstance Win32_Process -Filter "Name='trcc-gui.exe'" | Where-Object { $_.CommandLine -and $_.CommandLine -notlike '* service*' } | ForEach-Object ProcessId) }  # not TRCCService (SYSTEM: its CommandLine is hidden from a normal shell)
& $exe kill 2>&1 | Out-Null
Start-Sleep 3
AppPids | ForEach-Object { Stop-Process -Id $_ -Force }
$start = (Get-Date).ToString("yyyy-MM-ddTHH:mm:ss.fff")
$app = Start-Process $gui -ArgumentList "-vvv", "daemon" -PassThru
Start-Sleep 20
& $exe display screencast-region 0402:3922 0 0 320 320 2>&1 | Out-Null
$cast = Start-Process $exe -ArgumentList "display", "screencast", "0402:3922" -PassThru -WindowStyle Hidden
Start-Sleep 20
& $exe display stop-screencast 0402:3922 2>&1 | Out-Null
$end = (Get-Date).ToString("yyyy-MM-ddTHH:mm:ss.fff")
Start-Sleep 2
if (-not $cast.HasExited) { Stop-Process -Id $cast.Id -Force }
& $exe kill 2>&1 | Out-Null
Start-Sleep 3
Get-ChildItem "$env:APPDATA\trcc\trcc.log*" -Exclude *.lock | Get-Content -ErrorAction SilentlyContinue |
    Where-Object { $_ -match '^\d{4}-\d\d-\d\dT' -and $_.Substring(0, 23) -ge $start -and $_.Substring(0, 23) -le $end } |
    Sort-Object | Set-Content $out -Encoding utf8
Invoke-WebRequest -UseBasicParsing -Method Put -InFile $out -Uri "$up/cast_breakdown.log" | Out-Null
$v = (Get-ItemProperty HKCU:\Software\Microsoft\Windows\CurrentVersion\Run).TRCCNext
Invoke-Expression "& $v"
"$((Get-Item $out).Length) bytes uploaded; TRCC restarted as at sign-in"
