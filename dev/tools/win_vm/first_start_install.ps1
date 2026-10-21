# ADMIN: install the setup build_installer.ps1 just built, silently, over the running TRCC -- the "fresh files" a
# first start after an install meets (Defender scans them: the cold start that took ~9 s on 2026-10-20).
# `trcc kill` first, so no App holds the files.  The silent install does NOT start TRCC (skipifsilent):
# first_start_check.ps1 (NON-ADMIN) does, and measures it.  -> inbox/first_start_install.txt
$up = "http://10.0.2.2:8765"
$exe = "C:\Program Files\TRCC\trcc.exe"
$out = "C:\trccdev\first_start_install.txt"
& {
    Set-Location C:\trccdev
    "integrity: " + ((whoami /groups | Select-String "Mandatory Level") -replace '\s+', ' ')
    "before: " + ((& $exe --version 2>&1 | Out-String).Trim())
    "kill: " + ((& $exe kill 2>&1 | Out-String).Trim() -split "`n")[-1]
    $setup = Get-ChildItem "C:\trccdev\build\installer\Output\*.exe" -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime | Select-Object -Last 1
    if (-not $setup) { "NO setup exe in C:\trccdev\build\installer\Output -- run build_installer.ps1 first"; return }
    "setup: $($setup.Name) built $($setup.LastWriteTime.ToString('s'))"
    $p = Start-Process $setup.FullName -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART" -Wait -PassThru
    "setup exit: $($p.ExitCode)"
    "after: " + ((& $exe --version 2>&1 | Out-String).Trim())
    "trcc processes now: [$((@(Get-Process trcc*, trcc-gui* -ErrorAction SilentlyContinue | ForEach-Object Id)) -join ',')]"
    Set-Location C:\trccdev
} *> $out
Invoke-WebRequest -UseBasicParsing -Method Put -InFile $out -Uri "$up/first_start_install.txt" | Out-Null
Get-Content $out
"UPLOADED"
