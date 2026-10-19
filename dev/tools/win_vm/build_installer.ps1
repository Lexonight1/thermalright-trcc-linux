# ADMIN: build trcc-<version>-setup.exe from payload/src.zip (win_vm.py pack), as windows.yml does.
# dev\tools\build_windows.ps1 -Installer in C:\trccdev\build, with its own venv; installs Inno Setup 6 first if missing.
$ErrorActionPreference = "Continue"
$up = "http://10.0.2.2:8765"
$dev = "C:\trccdev"
$build = "$dev\build"
$log = "$dev\build_installer.log"
& {
    "== integrity"; whoami /groups | Select-String "Mandatory Level"
    "== fetch tree"
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
    "== build venv"
    $venv = "$dev\buildvenv"
    if (-not (Test-Path "$venv\Scripts\python.exe")) { py -3.12 -m venv $venv 2>&1 }
    & "$venv\Scripts\python.exe" -m pip install -q --upgrade pip 2>&1 | Select-Object -Last 2
    & "$venv\Scripts\python.exe" -m pip install -q pyinstaller "$build[nvidia,windows]" libusb-package 2>&1 | Select-Object -Last 5
    "== build"
    $env:PATH = "$venv\Scripts;$env:PATH"
    Set-Location $build
    powershell -NoProfile -ExecutionPolicy Bypass -File .\dev\tools\build_windows.ps1 -Installer 2>&1 | Select-Object -Last 25
    "== output"
    Get-ChildItem "$build\installer\Output" -ErrorAction SilentlyContinue | ForEach-Object { "$($_.Name) $([math]::Round($_.Length / 1MB, 1)) MB" }
} *> $log
Invoke-WebRequest -UseBasicParsing -Method Put -InFile $log -Uri "$up/build_installer.log" | Out-Null
Get-Content $log -Tail 8
"BUILT"
