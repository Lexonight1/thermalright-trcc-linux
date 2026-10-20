# EITHER shell: the shared log's lock under contention, LockFileEx vs msvcrt (log_lock_bench.py) -> inbox/log_lock_bench.txt
# Runs the CURRENT tree (payload/src.zip, `win_vm.py pack`) from C:\trccdev\benchtree; touches nothing of TRCC's.
$up = "http://10.0.2.2:8765"
$py = "$env:TEMP\trccipc\venv\Scripts\python.exe"
$tree = "C:\trccdev\benchtree"
$out = "C:\trccdev\log_lock_bench.txt"
& {
    Invoke-WebRequest -UseBasicParsing -Uri "$up/src.zip" -OutFile "C:\trccdev\benchtree.zip"
    if (Test-Path $tree) { Remove-Item -Recurse -Force $tree }
    Expand-Archive -Force "C:\trccdev\benchtree.zip" $tree
    Remove-Item "C:\trccdev\benchtree.zip"
    Invoke-WebRequest -UseBasicParsing -Uri "$up/log_lock_bench.py" -OutFile "C:\trccdev\log_lock_bench.py"
    & $py C:\trccdev\log_lock_bench.py "$tree\src" 3 3000
} *> $out
Invoke-WebRequest -UseBasicParsing -Method Put -InFile $out -Uri "$up/log_lock_bench.txt" | Out-Null
Get-Content $out
"UPLOADED"
