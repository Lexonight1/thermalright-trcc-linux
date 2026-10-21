# NON-ADMIN: what the Windows GPU readers (D3DKMT + PDH, ADL) see on this machine, from the CURRENT tree.
# Touches nothing of TRCC's.  -> inbox/gpu_probe.txt
$up = "http://10.0.2.2:8765"
$py = "$env:TEMP\trccipc\venv\Scripts\python.exe"
$tree = "C:\trccdev\benchtree"
$out = "C:\trccdev\gpu_probe.txt"
& {
    Invoke-WebRequest -UseBasicParsing -Uri "$up/src.zip" -OutFile "C:\trccdev\benchtree.zip"
    if (Test-Path $tree) { Remove-Item -Recurse -Force $tree }
    Expand-Archive -Force "C:\trccdev\benchtree.zip" $tree
    $env:PYTHONPATH = "$tree\src"
    & $py -c @"
import logging, time
logging.basicConfig(level=logging.INFO, format='%(name)s: %(message)s')
from trcc.adapters.sensors._d3dkmt import _Kmt, discover_d3dkmt_gpus, describe
from trcc.adapters.sensors._adl import discover_adl_gpus
kmt = _Kmt()
raw = kmt.adapters()
print('D3DKMT adapters enumerated:', len(raw))
for info in raw:
    print('  handle', info.hAdapter, '->', describe(kmt, info))
gpus = discover_d3dkmt_gpus()
time.sleep(1.2)
for g in gpus:
    print('D3DKMT', g.key, g.name, 'usage', g.usage(), 'vram', g.vram_used(), '/', g.vram_total(), 'temp', g.temp(), 'fan', g.fan_rpm())
print('ADL GPUs:', discover_adl_gpus())
"@
} *> $out
Invoke-WebRequest -UseBasicParsing -Method Put -InFile $out -Uri "$up/gpu_probe.txt" | Out-Null
Get-Content $out
"UPLOADED"
