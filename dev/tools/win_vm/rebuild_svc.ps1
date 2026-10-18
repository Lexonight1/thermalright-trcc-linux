# ADMIN: rebuild TRCCService from payload/src.zip (win_vm.py pack) and restart it.
# Needs: C:\trccdev, the TRCCService registration (binPath C:\trccdev\dist\trccsvc\trccsvc.exe service).
$ErrorActionPreference = "Continue"
$up = "http://10.0.2.2:8765"
$dev = "C:\trccdev"
$log = "$dev\rebuild.log"
Set-Location $dev
& {
    "== stop"; net stop TRCCService 2>&1
    "== fetch tree"
    Invoke-WebRequest -UseBasicParsing -Uri "$up/src.zip" -OutFile "$dev\src.zip"
    Invoke-WebRequest -UseBasicParsing -Uri "$up/trccsvc.spec" -OutFile "$dev\trccsvc.spec"
    if (Test-Path "$dev\tree") { Remove-Item -Recurse -Force "$dev\tree" }
    Expand-Archive -Force "$dev\src.zip" "$dev\tree"
    Get-ChildItem "$dev\tree" | Select-Object -ExpandProperty Name
    "== venv"
    $venv = "$env:TEMP\trccipc\venv"
    if (-not (Test-Path "$venv\Scripts\python.exe")) {
        $venv = "$dev\venv"
        if (-not (Test-Path "$venv\Scripts\python.exe")) { py -3.12 -m venv $venv 2>&1 }
    }
    "venv = $venv"
    & "$venv\Scripts\python.exe" --version 2>&1
    & "$venv\Scripts\python.exe" -m pip install -q -e "$dev\tree" pyinstaller 2>&1 | Select-Object -Last 5
    "== keep pawnio"
    if (Test-Path "$dev\dist\trccsvc\pawnio") { Copy-Item -Recurse -Force "$dev\dist\trccsvc\pawnio" "$dev\pawnio_keep" }
    "== build"
    & "$venv\Scripts\pyinstaller.exe" --noconfirm trccsvc.spec 2>&1 | Select-Object -Last 3
    Copy-Item -Recurse -Force "$dev\pawnio_keep" "$dev\dist\trccsvc\pawnio"
    Get-ChildItem "$dev\dist\trccsvc\pawnio" | Select-Object -ExpandProperty Name
    "== start"; sc.exe start TRCCService 2>&1
    Start-Sleep 3
    sc.exe query TRCCService 2>&1 | Select-String STATE
    "venv for the user side: $venv"
} *> $log
Invoke-WebRequest -UseBasicParsing -Method Put -InFile $log -Uri "$up/rebuild.log" | Out-Null
Get-Content $log -Tail 12
"REBUILT"
