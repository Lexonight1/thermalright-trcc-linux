# NON-ADMIN: a shared App (spawned by the CLI) connects the panel and draws red through the service.
$up = "http://10.0.2.2:8765"
$py = "$env:TEMP\trccipc\venv\Scripts\python.exe"
$out = "C:\trccdev\connect.txt"
$logmark = (Get-Date).ToString("yyyy-MM-ddTHH:mm:ss")
& {
    "log from $logmark"
    "== integrity"; whoami /groups | Select-String "Mandatory Level"
    foreach ($a in @("daemon-status"), @("device", "connect", "0402:3922"), @("display", "color", "0402:3922", "ff0000"), @("daemon-status")) {
        "== trcc $($a -join ' ')"
        $s = Get-Date; & $py -m trcc @a 2>&1 | Out-String
        "   ($([int]((Get-Date) - $s).TotalMilliseconds) ms, exit $LASTEXITCODE)"
    }
    "== python processes"
    Get-CimInstance Win32_Process -Filter "Name like 'python%' or Name like 'trcc%'" | Format-Table ProcessId, ParentProcessId, Name, CommandLine -AutoSize -Wrap | Out-String -Width 250
} *> $out
Invoke-WebRequest -UseBasicParsing -Method Put -InFile $out -Uri "$up/connect.txt" | Out-Null
Invoke-WebRequest -UseBasicParsing -Method Put -InFile "$env:APPDATA\trcc\trcc.log" -Uri "$up/trcc.log" | Out-Null
"UPLOADED"
