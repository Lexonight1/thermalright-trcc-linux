# ADMIN: every segment of TRCCService's log (it runs as SYSTEM) -> inbox/svc_*.
# The lock files cannot be copied; their errors are expected.
$d = "$env:SystemRoot\System32\config\systemprofile\AppData\Roaming\trcc"
Get-ChildItem $d -Filter "trcc*.log*" | ForEach-Object {
    "{0} {1} {2:HH:mm:ss}" -f $_.Name, $_.Length, $_.LastWriteTime
    Invoke-WebRequest -UseBasicParsing -Method Put -InFile $_.FullName -Uri ("http://10.0.2.2:8765/svc_" + $_.Name) | Out-Null
}
"UPLOADED"
