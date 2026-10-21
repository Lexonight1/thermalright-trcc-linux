# NON-ADMIN (the user's desktop): time GdiScreenCapture grabs (gdi_bench.py) on the CURRENT tree -> inbox/gdi_bench.txt
$up = "http://10.0.2.2:8765"
$py = "$env:TEMP\trccipc\venv\Scripts\python.exe"
$tree = "C:\trccdev\benchtree"
$out = "C:\trccdev\gdi_bench.txt"
& {
    Invoke-WebRequest -UseBasicParsing -Uri "$up/src.zip" -OutFile "C:\trccdev\benchtree.zip"
    if (Test-Path $tree) { Remove-Item -Recurse -Force $tree }
    Expand-Archive -Force "C:\trccdev\benchtree.zip" $tree
    Remove-Item "C:\trccdev\benchtree.zip"
    Invoke-WebRequest -UseBasicParsing -Uri "$up/gdi_bench.py" -OutFile "C:\trccdev\gdi_bench.py"
    & $py C:\trccdev\gdi_bench.py "$tree\src" 100
} *> $out
Invoke-WebRequest -UseBasicParsing -Method Put -InFile $out -Uri "$up/gdi_bench.txt" | Out-Null
Get-Content $out
"UPLOADED"
