# ADMIN: App log, service log, and the last $env:TRCC_EVENTS_HOURS hours (default 0.5) of events from System, Application and the PnP / USB / storage channels.
$up = "http://10.0.2.2:8765"
function Send($path, $name) { Invoke-WebRequest -UseBasicParsing -Method Put -InFile $path -Uri "$up/$name" | Out-Null }
Send "$env:SystemRoot\System32\config\systemprofile\AppData\Roaming\trcc\trcc.log" "svc.log"
Send "C:\Users\ignorant\AppData\Roaming\trcc\trcc.log" "app.log"
$hours = if ($env:TRCC_EVENTS_HOURS) { [double]$env:TRCC_EVENTS_HOURS } else { 0.5 }
$since = (Get-Date).AddHours(-$hours)
$logs = @("System", "Application", "Microsoft-Windows-Kernel-PnP/Configuration",
          "Microsoft-Windows-DriverFrameworks-UserMode/Operational", "Microsoft-Windows-Storage-Storport/Operational",
          "Microsoft-Windows-Partition/Diagnostic", "Microsoft-Windows-Ntfs/Operational", "Microsoft-Windows-StorageSpaces-Driver/Operational",
          "Microsoft-Windows-Kernel-Power/Thermal-Operational")
& {
    foreach ($l in $logs) {
        "===== $l"
        Get-WinEvent -FilterHashtable @{LogName=$l; StartTime=$since} -ErrorAction SilentlyContinue |
            Sort-Object TimeCreated | ForEach-Object { "{0:HH:mm:ss.fff} {1} {2}: {3}" -f $_.TimeCreated, $_.ProviderName, $_.Id, (($_.Message -split "`n")[0]) }
    }
} *> C:\trccdev\allevents.txt
Send C:\trccdev\allevents.txt "allevents.txt"
"UPLOADED"
