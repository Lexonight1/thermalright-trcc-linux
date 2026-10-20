# EITHER shell, read-only: does Get-WinEvent see the System log at all, and what did the time filter do?
$out = & {
    "== integrity"; whoami /groups | Select-String "Mandatory Level"
    "== guest now $(Get-Date -Format o)"
    "== last 15 System events, no filter"
    try { Get-WinEvent -LogName System -MaxEvents 15 -ErrorAction Stop |
            ForEach-Object { "$($_.TimeCreated.ToString('MM-dd HH:mm:ss')) $($_.ProviderName) $($_.Id)" } }
    catch { "ERROR: $($_.Exception.Message)" }
    "== the earlier filter, errors shown"
    try { $n = @(Get-WinEvent -FilterHashtable @{LogName = "System"; StartTime = (Get-Date "2026-10-20 10:25"); EndTime = (Get-Date "2026-10-20 11:30")} -ErrorAction Stop).Count; "count: $n" }
    catch { "ERROR: $($_.Exception.Message)" }
    "== Kernel-PnP / USB / disk events since 09:00"
    try { Get-WinEvent -FilterHashtable @{LogName = "System"; StartTime = (Get-Date "2026-10-20 09:00")} -ErrorAction Stop |
            Where-Object { $_.ProviderName -match "PnP|USB|disk|storahci|stornvme|Partmgr|Kernel-Power|UsbHub" } |
            Select-Object -First 40 | ForEach-Object { "$($_.TimeCreated.ToString('HH:mm:ss')) $($_.ProviderName) $($_.Id) $((($_.Message -split "`n")[0]).Substring(0, [Math]::Min(110, (($_.Message -split "`n")[0]).Length)))" } }
    catch { "ERROR: $($_.Exception.Message)" }
} | Out-String -Width 220
$out | Set-Content "$env:TEMP\events_probe.txt"
Invoke-WebRequest -UseBasicParsing -Method Put -InFile "$env:TEMP\events_probe.txt" -Uri "http://10.0.2.2:8765/events_probe.txt" | Out-Null
"UPLOADED"
