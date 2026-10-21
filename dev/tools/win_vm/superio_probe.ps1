# ADMIN (PawnIO admits only administrators): the Super I/O reader (Nuvoton, MSI NCT668x, ITE) on the REAL
# PawnIO driver and the real LpcIO.bin, from the CURRENT tree.  -> inbox/superio_probe.txt
# Proves the ioctl names and argument counts PawnIO accepts.  The win11 VM has no Super I/O chip, so the
# expected answer there is "no Super I/O chip at 0x2e; no Super I/O chip at 0x4e" -- with no PawnIO error.
# Needs payload/pawnio_modules_0_2_11.zip (PawnIO.Modules 0.2.11, the release TRCC pins; checked below).
$up = "http://10.0.2.2:8765"
$py = "$env:TEMP\trccipc\venv\Scripts\python.exe"
$tree = "C:\trccdev\siotree-$(Get-Date -Format yyyyMMddHHmmss)"
$out = "C:\trccdev\superio_probe.txt"
& {
    Set-Location C:\trccdev
    "integrity: " + ((whoami /groups | Select-String "Mandatory Level") -replace '\s+', ' ')
    "PawnIO driver: " + ((Get-Service PawnIO -ErrorAction SilentlyContinue).Status)
    Invoke-WebRequest -UseBasicParsing -Uri "$up/src.zip" -OutFile "$tree.zip"
    Expand-Archive -Force "$tree.zip" $tree
    Invoke-WebRequest -UseBasicParsing -Uri "$up/pawnio_modules_0_2_11.zip" -OutFile "$tree-mods.zip"
    $hash = (Get-FileHash "$tree-mods.zip" -Algorithm SHA256).Hash
    "modules zip: $hash (pinned 43608CB89BC84247FEF1368A139013F7D043E17DB6D6C8DFC9B46BF0905A81F4)"
    Expand-Archive -Force "$tree-mods.zip" "$tree-mods"
    $env:PYTHONPATH = "$tree\src"
    $env:TRCC_LPCIO = "$tree-mods\LpcIO.bin"
    & $py -c @"
import logging, os, pathlib
logging.basicConfig(level=logging.DEBUG, format='%(name)s: %(message)s')
logging.getLogger('trcc.frame').setLevel(logging.DEBUG)
from functools import partial
from trcc.adapters.sensors._pawnio import IsaBusLock, PawnIoModule
from trcc.adapters.sensors._superio import SuperIoReader
blob = pathlib.Path(os.environ['TRCC_LPCIO']).read_bytes()
reader = SuperIoReader(partial(PawnIoModule, blob, name='LpcIO'), IsaBusLock().held)
print('SAMPLE', reader.sample())
reader.close()
"@
    Set-Location C:\trccdev
} *> $out
Invoke-WebRequest -UseBasicParsing -Method Put -InFile $out -Uri "$up/superio_probe.txt" | Out-Null
Get-Content $out
"UPLOADED"
