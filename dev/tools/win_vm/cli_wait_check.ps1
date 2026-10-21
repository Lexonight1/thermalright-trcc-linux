# NON-ADMIN: does `trcc display screencast` from the CURRENT tree (payload/src.zip) stay alive waiting for Ctrl-C?
# The installed CLI died 1 s in (signal.pause, missing on Windows).  Casts 10 s, stops it with the installed CLI.
# Run gdi_bench or log_lock_bench first, which extract the tree to C:\trccdev\benchtree.  -> inbox/cli_wait_check.txt
$up = "http://10.0.2.2:8765"
$py = "$env:TEMP\trccipc\venv\Scripts\python.exe"
$exe = "C:\Program Files\TRCC\trcc.exe"
$tree = "C:\trccdev\benchtree\src"
$log = "$env:APPDATA\trcc\trcc.log"
$out = "C:\trccdev\cli_wait_check.txt"
& {
    $start = (Get-Date).ToString("yyyy-MM-ddTHH:mm:ss.fff")
    $env:PYTHONPATH = $tree
    $cli = Start-Process $py -ArgumentList "-m", "trcc", "display", "screencast", "0402:3922", "0", "0", "320", "320" -PassThru -WindowStyle Hidden
    Remove-Item env:PYTHONPATH
    Start-Sleep 10
    "after 10 s the tree's CLI (pid $($cli.Id)) is: $(if ($cli.HasExited) { "GONE, exit $($cli.ExitCode)" } else { 'still waiting' })"
    "stop: " + ((& $exe display stop-screencast 0402:3922 2>&1 | Out-String).Trim())
    if (-not $cli.HasExited) { Stop-Process -Id $cli.Id -Force }
    $hits = @(Get-Content $log -ErrorAction SilentlyContinue | Where-Object {
        $_ -match '^\d{4}-\d\d-\d\dT' -and $_.Substring(0, 23) -ge $start -and $_ -match 'Fatal startup error|signal.*pause|wait_until_interrupted' })
    "log lines since the start naming the wait or a fatal error: $($hits.Count)"
    $hits | Select-Object -First 6
} *> $out
Invoke-WebRequest -UseBasicParsing -Method Put -InFile $out -Uri "$up/cli_wait_check.txt" | Out-Null
Get-Content $out
"UPLOADED"
