# NON-ADMIN: the named-pipe kill tests on real Windows, from the CURRENT tree -- then once more with the
# woken-before-accept fix taken out, which must FAIL (proves the test can see the bug).  Touches nothing of
# TRCC's: the tests serve a pipe of their own.  -> inbox/pipe_kill_race.txt
$up = "http://10.0.2.2:8765"
$py = "$env:TEMP\trccipc\venv\Scripts\python.exe"
# A fresh folder per run: a reused one can be held open by an earlier shell or python.
$tree = "C:\trccdev\killtree-$(Get-Date -Format yyyyMMddHHmmss)"
$out = "C:\trccdev\pipe_kill_race.txt"
$tests = "tests/test_ipc_server.py::test_a_listener_woken_before_it_accepts_answers_no_one",
         "tests/test_ipc_server.py::test_kill_request_shuts_daemon_down"
& {
    Set-Location C:\trccdev     # never inside the tree: a shell left there locks it
    Invoke-WebRequest -UseBasicParsing -Uri "$up/src.zip" -OutFile "$tree.zip"
    Expand-Archive -Force "$tree.zip" $tree
    "tree: $tree, test file present: $(Test-Path "$tree\tests\test_ipc_server.py")"
    & $py -m pytest --version 2>&1
    if ($LASTEXITCODE -ne 0) { & $py -m pip install -q pytest pytest-xdist pytest-qt 2>&1 | Select-Object -Last 2 }
    Set-Location $tree
    $env:PYTHONPATH = "$tree\src"
    "== the tree"
    & $py -m pytest @tests -q -n 0 -p no:cacheprovider 2>&1 | Select-Object -Last 15
    "== mutant: accept's entry leaves the waiting instance open (must FAIL)"
    $f = "$tree\src\trcc\adapters\infra\named_pipe.py"
    (Get-Content -Raw $f).Replace("                self._drop_pending()`r`n", "").Replace("                self._drop_pending()`n", "") |
        Set-Content -NoNewline $f
    Get-ChildItem -Recurse -Filter "named_pipe*.pyc" $tree | Remove-Item
    & $py -m pytest $tests[0] -q -n 0 -p no:cacheprovider 2>&1 | Select-Object -Last 15
    Set-Location C:\trccdev
} *> $out
Invoke-WebRequest -UseBasicParsing -Method Put -InFile $out -Uri "$up/pipe_kill_race.txt" | Out-Null
Get-Content $out
"UPLOADED"
