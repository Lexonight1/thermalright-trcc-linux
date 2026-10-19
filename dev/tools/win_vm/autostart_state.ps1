# EITHER shell: TRCC's autostart + install state -> inbox/autostart_state.txt (old sign-in task, Run value, processes, installed exe).
$up = "http://10.0.2.2:8765"
$out = "C:\trccdev\autostart_state.txt"
& {
    "at " + (Get-Date).ToString("HH:mm:ss")
    "== integrity"; whoami /groups | Select-String "Mandatory Level"
    "== old sign-in task"; schtasks /query /tn "TRCC Linux" /fo list 2>&1 | Out-String
    "exit $LASTEXITCODE"
    "== Run values"
    Get-ItemProperty "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" -ErrorAction SilentlyContinue |
        Select-Object TRCCNext, "TRCC Linux" | Format-List | Out-String -Width 250
    "== processes"
    Get-CimInstance Win32_Process -Filter "Name like 'trcc%' or Name like 'python%'" |
        Format-Table ProcessId, ParentProcessId, CreationDate, Name, CommandLine -AutoSize -Wrap | Out-String -Width 250
    "== installed"
    $exe = "C:\Program Files\TRCC\trcc-gui.exe"
    if (Test-Path $exe) { (Get-Item $exe).VersionInfo.ProductVersion; (Get-Item $exe).LastWriteTime }
    "== service"; sc.exe qc TRCCService | Select-String "BINARY_PATH|START_TYPE"
} *> $out
Invoke-WebRequest -UseBasicParsing -Method Put -InFile $out -Uri "$up/autostart_state.txt" | Out-Null
Get-Content $out
"UPLOADED"
