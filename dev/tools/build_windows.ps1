# TRCC Windows Development Build Script
#
# Emulates windows.yml CI exactly - same tools, same bundle contents.
# Copies the installed 7-Zip; downloads ffmpeg and PawnIO on first run (cached after).
#
# Prerequisites (one-time setup):
#   1. Install Python 3.12 from python.org (check "Add to PATH")
#   2. python -m pip install pyinstaller
#   3. python -m pip install ".[nvidia,windows]"
#   4. python -m pip install libusb-package tzdata
#   5. 7-Zip installed in C:\Program Files\7-Zip (bundled, as CI does)
#
# Usage:
#   cd <repo-root>
#   powershell -ExecutionPolicy Bypass -File .\dev\tools\build_windows.ps1 [-Installer]
#
# Output: dist\trcc\ - run directly, no installer needed.
#   -Installer also compiles installer\trcc.iss with Inno Setup 6 (ISCC.exe on
#   PATH or in its default folder) into installer\Output\, as windows.yml does.
#
# The PyInstaller flags must match windows.yml's -- gated by
# tests/test_build_windows_matches_ci.py.  This file once claimed to emulate CI
# "exactly" while leaving out the GUI's skin images and PawnIO.
#   .\dist\trcc\trcc.exe detect
#   .\dist\trcc\trcc.exe report
#   .\dist\trcc\trcc-gui.exe

param([switch]$Installer)

Set-StrictMode -Off

$logFile = "build.log"

function Log($msg) {
  $ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
  $line = "[$ts] $msg"
  Write-Host $line
  Add-Content -Path $logFile -Value $line -Encoding UTF8
}

function Die($msg) {
  Log "ERROR: $msg"
  exit 1
}

# Clear previous log
if (Test-Path $logFile) { Remove-Item $logFile -Force }

# Ensure Python Scripts dir is on PATH (pip-installed tools live there)
$scriptsDir = python -c "import sysconfig; print(sysconfig.get_path('scripts'))" 2>$null
if ($scriptsDir -and (Test-Path $scriptsDir)) {
  $env:PATH = "$scriptsDir;$env:PATH"
}

# Kill running TRCC processes before build
Log "--- Stopping running TRCC processes ---"
taskkill /F /IM trcc-gui.exe 2>$null | Out-Null
taskkill /F /IM trcc.exe 2>$null | Out-Null
Start-Sleep -Seconds 1

Log "=== TRCC Windows Build ==="
Log "Python: $(python --version 2>&1)"
Log "PyInstaller: $(python -m PyInstaller --version 2>&1)"
Log "PySide6: $(python -c 'import PySide6; print(PySide6.__version__)' 2>&1)"
Log "Platform: $([System.Environment]::OSVersion)"
Log "Working dir: $(Get-Location)"
Log ""

# -----------------------------------------------------------------------
# 7-Zip (LGPL): the installed one, copied as windows.yml does.  This used to
# download 7z2409-extra.7z from 7-zip.org, which answers 404 now (2026-10-19).
# -----------------------------------------------------------------------
Log "--- Preparing 7-Zip ---"
$sevenZip = "C:\Program Files\7-Zip"
if (-not (Test-Path "$sevenZip\7z.exe")) { Die "7-Zip not installed ($sevenZip) - install it from https://www.7-zip.org" }
New-Item -ItemType Directory -Force -Path "7z-standalone" | Out-Null
Copy-Item "$sevenZip\7z.exe" "7z-standalone\7za.exe" -Force
Copy-Item "$sevenZip\7z.dll" "7z-standalone\7za.dll" -Force
Log "7-Zip OK"

# -----------------------------------------------------------------------
# Download ffmpeg essentials (LGPL)
# -----------------------------------------------------------------------
Log "--- Downloading ffmpeg ---"
if (Test-Path "ffmpeg.exe") {
  Log "ffmpeg.exe already present - skipping download"
} else {
  try {
    Invoke-WebRequest -Uri "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip" -OutFile "ffmpeg.zip" -UseBasicParsing
    Expand-Archive "ffmpeg.zip" -DestinationPath "ffmpeg-tmp" -Force
    $ffmpegExe = Get-ChildItem -Path "ffmpeg-tmp" -Recurse -Filter "ffmpeg.exe" | Select-Object -First 1
    if (-not $ffmpegExe) { Die "ffmpeg.exe not found in archive" }
    Copy-Item $ffmpegExe.FullName -Destination "ffmpeg.exe" -Force
    Remove-Item "ffmpeg.zip" -Force
    Remove-Item "ffmpeg-tmp" -Recurse -Force
    Log "ffmpeg downloaded OK"
  } catch {
    Die "ffmpeg download failed: $_"
  }
}

# -----------------------------------------------------------------------
# PawnIO (namazso): the two signed modules TRCC runs, and the driver's
# installer the setup lays down -- pinned by SHA-256, the same values as
# windows.yml (gated by tests/test_installer_uninstall.py).
# -----------------------------------------------------------------------
Log "--- Downloading PawnIO ---"
if (-not (Test-Path "pawnio-modules\IntelMSR.bin")) {
  Invoke-WebRequest -Uri "https://github.com/namazso/PawnIO.Modules/releases/download/0.2.11/release_0_2_11.zip" -OutFile "pawnio-modules.zip" -UseBasicParsing
  $hash = (Get-FileHash "pawnio-modules.zip" -Algorithm SHA256).Hash
  if ($hash -ne "43608CB89BC84247FEF1368A139013F7D043E17DB6D6C8DFC9B46BF0905A81F4") { Die "PawnIO.Modules 0.2.11 hash mismatch: $hash" }
  Expand-Archive "pawnio-modules.zip" -DestinationPath "pawnio-modules" -Force
}
if (-not (Test-Path "installer\pawnio\PawnIO_setup.exe")) {
  New-Item -ItemType Directory -Force -Path "installer\pawnio" | Out-Null
  Invoke-WebRequest -Uri "https://github.com/namazso/PawnIO.Setup/releases/download/2.2.0/PawnIO_setup.exe" -OutFile "installer\pawnio\PawnIO_setup.exe" -UseBasicParsing
  $hash = (Get-FileHash "installer\pawnio\PawnIO_setup.exe" -Algorithm SHA256).Hash
  if ($hash -ne "1F519A22E47187F70A1379A48CA604981C4FCF694F4E65B734AAA74A9FBA3032") { Die "PawnIO_setup 2.2.0 hash mismatch: $hash" }
}
Log "PawnIO OK"

# -----------------------------------------------------------------------
# Get libusb path (optional - pyusb dependency)
# -----------------------------------------------------------------------
$libusbPath = python -c "import libusb_package; print(libusb_package.get_library_path())" 2>$null

# -----------------------------------------------------------------------
# PyInstaller: CLI build (with console window)
# -----------------------------------------------------------------------
Log ""
Log "--- Building CLI ---"
python -m PyInstaller `
  --name trcc `
  --onedir `
  --console `
  --icon "src\trcc\assets\icons\app.ico" `
  --add-data "src\trcc\assets;trcc\assets" `
  --add-data "src\trcc\ui\gui\assets;trcc\ui\gui\assets" `
  --hidden-import PySide6.QtSvg `
  --hidden-import pynvml `
  --hidden-import wmi `
  --hidden-import soundcard `
  --collect-submodules trcc `
  --noconfirm `
  "src\trcc\__main__.py"

if ($LASTEXITCODE -ne 0) { Die "CLI build failed (exit $LASTEXITCODE)" }
Log "CLI build OK"

# -----------------------------------------------------------------------
# PyInstaller: GUI build (no console window)
# -----------------------------------------------------------------------
Log ""
Log "--- Building GUI ---"
python -m PyInstaller `
  --name trcc-gui `
  --onedir `
  --windowed `
  --icon "src\trcc\assets\icons\app.ico" `
  --add-data "src\trcc\assets;trcc\assets" `
  --add-data "src\trcc\ui\gui\assets;trcc\ui\gui\assets" `
  --hidden-import PySide6.QtSvg `
  --hidden-import pynvml `
  --hidden-import wmi `
  --hidden-import soundcard `
  --collect-submodules trcc `
  --noconfirm `
  "src\trcc\__main__.py"

if ($LASTEXITCODE -ne 0) { Die "GUI build failed (exit $LASTEXITCODE)" }
Log "GUI build OK"

# -----------------------------------------------------------------------
# Merge into dist\trcc\ (same layout as CI installer)
# -----------------------------------------------------------------------
Log ""
Log "--- Merging ---"
Copy-Item "dist\trcc-gui\trcc-gui.exe" "dist\trcc\" -Force
Log "Copied trcc-gui.exe"
Copy-Item "7z-standalone\7za.exe" "dist\trcc\7z.exe" -Force
Copy-Item "7z-standalone\7za.dll" "dist\trcc\7z.dll" -Force
Log "Bundled 7z.exe + 7z.dll"
Copy-Item "ffmpeg.exe" "dist\trcc\ffmpeg.exe" -Force
Log "Bundled ffmpeg.exe"
if ($libusbPath -and (Test-Path $libusbPath)) {
  Copy-Item $libusbPath "dist\trcc\" -Force
  Log "Bundled libusb: $(Split-Path $libusbPath -Leaf)"
} else {
  Log "WARNING: libusb-package not installed - pyusb may not work"
}
# The PawnIO modules beside trcc.exe (_pawnio_cpu.module_dir).
New-Item -ItemType Directory -Force -Path "dist\trcc\pawnio" | Out-Null
foreach ($f in "IntelMSR.bin", "AMDFamily17.bin", "LpcIO.bin", "COPYING") {
  Copy-Item "pawnio-modules\$f" "dist\trcc\pawnio\" -Force
}
Log "Bundled the PawnIO modules"

# -----------------------------------------------------------------------
# Verify (same checks as CI)
# -----------------------------------------------------------------------
Log ""
Log "=== Build Verification ==="

Get-ChildItem "dist\trcc\*.exe" -ErrorAction SilentlyContinue |
  ForEach-Object { Log "  $($_.Name) ($([math]::Round($_.Length / 1MB, 1)) MB)" }
Get-ChildItem "dist\trcc\*.dll" -ErrorAction SilentlyContinue |
  ForEach-Object { Log "  $($_.Name) ($([math]::Round($_.Length / 1KB, 0)) KB)" }

$total = (Get-ChildItem "dist\trcc" -Recurse -ErrorAction SilentlyContinue | Measure-Object).Count
$size  = (Get-ChildItem "dist\trcc" -Recurse -ErrorAction SilentlyContinue |
          Measure-Object -Property Length -Sum).Sum / 1MB
Log "Total: $total files, $([math]::Round($size, 1)) MB"

$missing = @()
if (-not (Test-Path "dist\trcc\trcc.exe"))     { $missing += "trcc.exe" }
if (-not (Test-Path "dist\trcc\trcc-gui.exe")) { $missing += "trcc-gui.exe" }
if (-not (Test-Path "dist\trcc\7z.exe"))       { $missing += "7z.exe" }
if (-not (Test-Path "dist\trcc\7z.dll"))       { $missing += "7z.dll" }
if (-not (Test-Path "dist\trcc\ffmpeg.exe"))   { $missing += "ffmpeg.exe" }
if (-not (Get-ChildItem "dist\trcc\libusb*" -ErrorAction SilentlyContinue)) {
  $missing += "libusb-1.0.dll"
}
foreach ($f in "IntelMSR.bin", "AMDFamily17.bin", "LpcIO.bin", "COPYING") {
  if (-not (Test-Path "dist\trcc\pawnio\$f")) { $missing += "pawnio\$f" }
}

if ($missing.Count -gt 0) {
  Die "FAILED - Missing: $($missing -join ', ')"
}

$ver = python -c "from trcc.__version__ import __version__; print(__version__)" 2>&1
Log "PASSED - trcc.exe, trcc-gui.exe, 7z.exe, ffmpeg.exe, libusb, PawnIO OK"
Log "Version: $ver"

if ($Installer) {
  Log ""
  Log "--- Compiling the installer ---"
  $iscc = (Get-Command ISCC.exe -ErrorAction SilentlyContinue).Source
  if (-not $iscc) { $iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" }
  if (-not (Test-Path $iscc)) { Die "Inno Setup 6 not found (ISCC.exe)" }
  & $iscc "/DAPP_VERSION=$ver" "installer\trcc.iss"
  if ($LASTEXITCODE -ne 0) { Die "ISCC failed (exit $LASTEXITCODE)" }
  Log "Installer: installer\Output\trcc-$ver-setup.exe"
}
Log ""
Log "=== Build Complete ==="
Log "Test with:"
Log "  .\dist\trcc\trcc.exe detect"
Log "  .\dist\trcc\trcc.exe report"
Log "  .\dist\trcc\trcc-gui.exe"
Log ""
Log "Build log: $logFile"
