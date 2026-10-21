# NON-ADMIN: does a second launch bring back a GUI hidden in the tray (1c5757c4)?  Starts the GUI hidden as
# sign-in does, then a hidden second launch (must NOT raise) and a plain one (MUST raise).  -> inbox/raise_check.txt
# Leaves the GUI running, its window shown.  TRCC_TREE=1: run the GUI from payload/src.zip with the dev venv (no build).
$up = "http://10.0.2.2:8765"
$gui = "C:\Program Files\TRCC\trcc-gui.exe"
$pre = @()
if ($env:TRCC_TREE) {
    $tree = "C:\trccdev\benchtree"
    Invoke-WebRequest -UseBasicParsing -Uri "$up/src.zip" -OutFile "C:\trccdev\benchtree.zip"
    if (Test-Path $tree) { Remove-Item -Recurse -Force $tree }
    Expand-Archive -Force "C:\trccdev\benchtree.zip" $tree
    $env:PYTHONPATH = "$tree\src"
    $gui = "$env:TEMP\trccipc\venv\Scripts\python.exe"
    $pre = @("-m", "trcc")
}
$log = "$env:APPDATA\trcc\trcc.log"
$out = "C:\trccdev\raise_check.txt"
function Guis { @(Get-CimInstance Win32_Process | Where-Object { $_.Name -match 'trcc-gui.exe|python.exe' -and $_.CommandLine -like '* gui*' } | ForEach-Object ProcessId) }
function Since($t, $p) { @(Get-ChildItem "$env:APPDATA\trcc\trcc.log*" -Exclude *.lock | Get-Content -ErrorAction SilentlyContinue |
    Where-Object { $_ -match '^\d{4}-\d\d-\d\dT' -and $_.Substring(0, 23) -ge $t -and $_ -match $p } | Sort-Object) }
& {
    Guis | ForEach-Object { Stop-Process -Id $_ -Force }
    Start-Sleep 2
    Start-Process $gui -ArgumentList ($pre + @("gui", "--resume"))
    Start-Sleep 35
    $hidden = (Get-Date).ToString("yyyy-MM-ddTHH:mm:ss.fff")
    Start-Process $gui -ArgumentList ($pre + @("gui", "--resume")) -Wait
    $plain = (Get-Date).ToString("yyyy-MM-ddTHH:mm:ss.fff")
    Start-Process $gui -ArgumentList ($pre + @("gui")) -Wait
    Start-Sleep 3
    "== hidden second launch (must not raise)"
    Since $hidden 'SingleInstance|RaiseEvent|raise_window' | Where-Object { $_.Substring(0, 23) -lt $plain } | ForEach-Object { $_.Substring(0, [Math]::Min(170, $_.Length)) }
    "== plain second launch (must raise)"
    Since $plain 'SingleInstance|RaiseEvent|raise_window' | ForEach-Object { $_.Substring(0, [Math]::Min(170, $_.Length)) }
    "GUIs running: $((Guis) -join ',')"
} *> $out
Invoke-WebRequest -UseBasicParsing -Method Put -InFile $out -Uri "$up/raise_check.txt" | Out-Null
Get-Content $out
"UPLOADED"
