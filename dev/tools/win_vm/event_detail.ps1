# ADMIN: full text of the events a summary hides, for the last $env:TRCC_EVENTS_HOURS hours (default 6) -> inbox/event_detail.txt.
# Application crashes (1000/1001), storport error summaries (504/549), NTFS mount failures (305), disk resets / surprise removals (129/153/157).
$up = "http://10.0.2.2:8765"
$hours = if ($env:TRCC_EVENTS_HOURS) { [double]$env:TRCC_EVENTS_HOURS } else { 6 }
$since = (Get-Date).AddHours(-$hours)
$out = "C:\trccdev\event_detail.txt"
$wanted = @(
    @{LogName = "Application"; Id = 1000, 1001},
    @{LogName = "Microsoft-Windows-Storage-Storport/Operational"; Id = 504, 549},
    @{LogName = "Microsoft-Windows-Ntfs/Operational"; Id = 305},
    @{LogName = "System"; Id = 129, 153, 157}
)
& {
    foreach ($w in $wanted) {
        "===== $($w.LogName) $($w.Id -join ',')"
        Get-WinEvent -FilterHashtable ($w + @{StartTime = $since}) -ErrorAction SilentlyContinue |
            Sort-Object TimeCreated | Select-Object -Last 6 |
            ForEach-Object { "--- {0:yyyy-MM-dd HH:mm:ss.fff} {1} {2}" -f $_.TimeCreated, $_.ProviderName, $_.Id; $_.Message }
    }
} *> $out
Invoke-WebRequest -UseBasicParsing -Method Put -InFile $out -Uri "$up/event_detail.txt" | Out-Null
"UPLOADED"
