# NON-ADMIN: the named-pipe kill tests on real Windows, from the CURRENT tree -- the kill test 25 times more (it
# failed ~1 run in 10 on CI), then each fix taken out in turn, which must FAIL (proves each test can see its bug).
# Touches nothing of TRCC's: the tests serve a pipe of their own.  -> inbox/pipe_kill_race.txt
$up = "http://10.0.2.2:8765"
$py = "$env:TEMP\trccipc\venv\Scripts\python.exe"
# A fresh folder per run: a reused one can be held open by an earlier shell or python.
$tree = "C:\trccdev\killtree-$(Get-Date -Format yyyyMMddHHmmss)"
$out = "C:\trccdev\pipe_kill_race.txt"
$before = "tests/test_ipc_server.py::test_a_listener_woken_before_it_accepts_answers_no_one"
$after = "tests/test_ipc_server.py::test_a_listener_woken_after_it_handed_over_a_client_answers_no_one"
$kill = "tests/test_ipc_server.py::test_kill_request_shuts_daemon_down"
# A temp folder of its own per pytest run: the shared pytest-of-<user>\pytest-current
# link is "Access is denied" here, and pytest's cleanup of it fails the RUN even when
# the test passed (2026-10-24) -- so passes are read from the summary line, not $LASTEXITCODE.
$script:runs = 0
function Pt([string[]]$t) {
    $script:runs++
    & $py -m pytest @t -q -n 0 -p no:cacheprovider --basetemp "$tree\bt$($script:runs)" 2>&1 | Out-String
}
function Mutate($tree, $old, $new) {
    $f = "$tree\src\trcc\adapters\infra\named_pipe.py"
    $text = (Get-Content -Raw $f).Replace("`r`n", "`n")
    if (-not $text.Contains($old)) { "MUTANT NOT APPLIED -- text not found" }
    $text.Replace($old, $new) | Set-Content -NoNewline $f
    Get-ChildItem -Recurse -Filter "named_pipe*.pyc" $tree | Remove-Item
}
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
    (Pt @($before, $after, $kill)).Trim().Split("`n") | Select-Object -Last 12
    "== the kill test 25 more times"
    $passed = 0
    foreach ($i in 1..25) { if ((Pt @($kill)) -match "1 passed") { $passed++ } }
    "kill test: $passed of 25 passed"
    "== mutant: wake leaves the waiting instance open (the AFTER test must FAIL)"
    Mutate $tree "            if not self._accepting:`n                self._drop_pending()`n" ""
    (Pt @($after)).Trim().Split("`n") | Select-Object -Last 6
    "== mutant: accept's entry ALSO leaves it open -- both fixes out (the BEFORE test must FAIL)"
    Mutate $tree "                # shutdown (Windows CI, 2026-10-21).`n                self._drop_pending()`n" "                # shutdown (Windows CI, 2026-10-21).`n"
    (Pt @($before)).Trim().Split("`n") | Select-Object -Last 6
    Set-Location C:\trccdev
} *> $out
Invoke-WebRequest -UseBasicParsing -Method Put -InFile $out -Uri "$up/pipe_kill_race.txt" | Out-Null
Get-Content $out
"UPLOADED"
