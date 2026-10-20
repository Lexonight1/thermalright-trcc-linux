# NON-ADMIN (an elevated CLI builds its own App): cast the top-left 320x320 of the desktop to the panel for
# TRCC_CAST_S seconds (default 60), then stop it -- run between usbtrace_start and usbtrace_stop (ADMIN) to count
# what reached the panel.  -> inbox/screencast_check.txt (start/stop times + the App log's verdict lines)
$up = "http://10.0.2.2:8765"
$exe = "C:\Program Files\TRCC\trcc.exe"
$secs = if ($env:TRCC_CAST_S) { [int]$env:TRCC_CAST_S } else { 60 }
$log = "$env:APPDATA\trcc\trcc.log"
$out = "C:\trccdev\screencast_check.txt"
& {
    "integrity: " + ((whoami /groups | Select-String "Mandatory Level") -replace '\s+', ' ')
    $start = (Get-Date).ToString("yyyy-MM-ddTHH:mm:ss.fff")
    "CAST  $start"
    $cast = Start-Process $exe -ArgumentList "display", "screencast", "0402:3922", "0", "0", "320", "320" -PassThru -WindowStyle Hidden
    Start-Sleep $secs
    "STOP  $(Get-Date -Format o): " + ((& $exe display stop-screencast 0402:3922 2>&1 | Out-String).Trim())
    Start-Sleep 3
    if (-not $cast.HasExited) { Stop-Process -Id $cast.Id -Force; "the cast CLI did not exit on its own -- stopped" }
    "-- the App's verdict, timestamped lines since the start --"
    Get-Content $log -ErrorAction SilentlyContinue |
        Where-Object { $_ -match '^\d{4}-\d\d-\d\dT' -and $_.Substring(0, 23) -ge $start -and
                       $_ -match 'GdiScreenCapture|blank pixmap|captured nothing|capturing again|StartScreencast ok|StopScreencast ok|BitBlt|GetDIBits' } |
        Select-Object -First 12
} *> $out
Invoke-WebRequest -UseBasicParsing -Method Put -InFile $out -Uri "$up/screencast_check.txt" | Out-Null
Get-Content $out
"UPLOADED"
