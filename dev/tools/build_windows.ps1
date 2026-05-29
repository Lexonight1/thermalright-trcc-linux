# TRCC Windows Development Build Script
#
# Emulates windows.yml CI exactly - same tools, same bundle contents.
# Downloads 7z and ffmpeg automatically on first run (cached for subsequent builds).
#
# Prerequisites (one-time setup):
#   1. Install Python 3.12 from python.org (check "Add to PATH")
#   2. python -m pip install pyinstaller
#   3. python -m pip install ".[nvidia,windows]"
#   4. python -m pip install libusb-package tzdata
#
# Usage:
#   cd <repo-root>
#   powershell -ExecutionPolicy Bypass -File .\tools\build_windows.ps1
#
# Output: dist\trcc\ - run directly, no installer needed.
#   .\dist\trcc\trcc.exe detect
#   .\dist\trcc\trcc.exe report
#   .\dist\trcc\trcc-gui.exe

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
# Download 7-Zip standalone (LGPL)
# Bootstrap: download 7zr.exe (self-contained, no install required),
# use it to extract 7za.exe from the extra package, then discard 7zr.exe.
# -----------------------------------------------------------------------
Log "--- Downloading 7-Zip standalone ---"
if (Test-Path "7z-standalone\7za.exe") {
  Log "7za.exe already present - skipping download"
} else {
  try {
    Invoke-WebRequest -Uri "https://www.7-zip.org/a/7zr.exe" -OutFile "7zr.exe" -UseBasicParsing
    Invoke-WebRequest -Uri "https://www.7-zip.org/a/7z2409-extra.7z" -OutFile "7z-extra.7z" -UseBasicParsing
    & ".\7zr.exe" x "7z-extra.7z" "-o7z-standalone" "7za.exe" | Out-Null
    Remove-Item "7zr.exe" -Force
    Remove-Item "7z-extra.7z" -Force
    if (-not (Test-Path "7z-standalone\7za.exe")) { Die "7za.exe not found after extraction" }
    Log "7z standalone downloaded OK"
  } catch {
    Die "7z download failed: $_"
  }
}

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
  --uac-admin `
  --icon "src\trcc\assets\icons\app.ico" `
  --add-data "src\trcc\assets;trcc\assets" `
  --hidden-import PySide6.QtSvg `
  --hidden-import pynvml `
  --hidden-import wmi `
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
  --uac-admin `
  --icon "src\trcc\assets\icons\app.ico" `
  --add-data "src\trcc\assets;trcc\assets" `
  --hidden-import PySide6.QtSvg `
  --hidden-import pynvml `
  --hidden-import wmi `
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
Log "Bundled 7z.exe"
Copy-Item "ffmpeg.exe" "dist\trcc\ffmpeg.exe" -Force
Log "Bundled ffmpeg.exe"
if ($libusbPath -and (Test-Path $libusbPath)) {
  Copy-Item $libusbPath "dist\trcc\" -Force
  Log "Bundled libusb: $(Split-Path $libusbPath -Leaf)"
} else {
  Log "WARNING: libusb-package not installed - pyusb may not work"
}

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
if (-not (Test-Path "dist\trcc\ffmpeg.exe"))   { $missing += "ffmpeg.exe" }
if (-not (Get-ChildItem "dist\trcc\libusb*" -ErrorAction SilentlyContinue)) {
  $missing += "libusb-1.0.dll"
}

if ($missing.Count -gt 0) {
  Die "FAILED - Missing: $($missing -join ', ')"
}

$ver = python -c "from trcc.__version__ import __version__; print(__version__)" 2>&1
Log "PASSED - trcc.exe, trcc-gui.exe, 7z.exe, ffmpeg.exe, libusb OK"
Log "Version: $ver"
Log ""
Log "=== Build Complete ==="
Log "Test with:"
Log "  .\dist\trcc\trcc.exe detect"
Log "  .\dist\trcc\trcc.exe report"
Log "  .\dist\trcc\trcc-gui.exe"
Log ""
Log "Build log: $logFile"
