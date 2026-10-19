# ADMIN: build trcc-<version>-setup.exe from payload/src.zip (win_vm.py pack), as windows.yml does.
# dev\tools\build_windows.ps1 -Installer in C:\trccdev\build; installs Inno Setup 6 first if missing.
# Builds with the dev venv rebuild_svc made (a second one does not fit: the VM's C: is ~42 GB), pointed at
# THIS tree by PYTHONPATH -- the venv's editable trcc is C:\trccdev\tree, which may be older.
$ErrorActionPreference = "Continue"
$up = "http://10.0.2.2:8765"
$dev = "C:\trccdev"
$build = "$dev\build"
$log = "$dev\build_installer.log"
& {
    "== integrity"; whoami /groups | Select-String "Mandatory Level"
    "== fetch tree"
    Set-Location $dev
    Invoke-WebRequest -UseBasicParsing -Uri "$up/src.zip" -OutFile "$dev\build.zip"
    if (Test-Path $build) { Remove-Item -Recurse -Force $build }
    Expand-Archive -Force "$dev\build.zip" $build
    "== Inno Setup"
    $iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
    if (-not (Test-Path $iscc)) {
        Invoke-WebRequest -UseBasicParsing -Uri "https://jrsoftware.org/download.php/is.exe" -OutFile "$dev\is.exe"
        Start-Process "$dev\is.exe" -ArgumentList "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/SP-" -Wait
    }
    "ISCC present: $(Test-Path $iscc)"
    "== venv"
    $venv = "$env:TEMP\trccipc\venv"
    & "$venv\Scripts\python.exe" -m pip install -q pyinstaller libusb-package 2>&1 | Select-Object -Last 3
    "free on C: $([math]::Round((Get-PSDrive C).Free / 1MB)) MB"
    "== build"
    $env:PATH = "$venv\Scripts;$env:PATH"
    $env:PYTHONPATH = "$build\src"
    Set-Location $build
    powershell -NoProfile -ExecutionPolicy Bypass -File .\dev\tools\build_windows.ps1 -Installer 2>&1 | Select-Object -Last 25
    "== output"
    Get-ChildItem "$build\installer\Output" -ErrorAction SilentlyContinue | ForEach-Object { "$($_.Name) $([math]::Round($_.Length / 1MB, 1)) MB" }
} *> $log
Invoke-WebRequest -UseBasicParsing -Method Put -InFile $log -Uri "$up/build_installer.log" | Out-Null
Get-Content $log -Tail 8
"BUILT"
