# NON-ADMIN (an elevated CLI builds its own App and takes the panel): play a 320x320 cloud video on the panel for
# TRCC_VIDEO_S seconds (default 180) at PlayVideo's default 15 fps, then stop-video back to the active theme.
# Run between usbtrace_start and usbtrace_stop (ADMIN); usbtrace_summary.py --cadence reads the frame spacing.
# Needs payload/a008.mp4 (a cloud video from ~/.trcc/data/web/320320/).  -> inbox/video_cadence.txt
$up = "http://10.0.2.2:8765"
$exe = "C:\Program Files\TRCC\trcc.exe"
$secs = if ($env:TRCC_VIDEO_S) { [int]$env:TRCC_VIDEO_S } else { 180 }
$dir = "$env:APPDATA\trcc\data\web\320320"
$video = "$dir\a008.mp4"
$out = "C:\trccdev\video_cadence.txt"
& {
    "integrity: " + ((whoami /groups | Select-String "Mandatory Level") -replace '\s+', ' ')
    New-Item -ItemType Directory -Force $dir | Out-Null
    Invoke-WebRequest -UseBasicParsing -Uri "$up/a008.mp4" -OutFile $video
    "PLAY  $(Get-Date -Format o): " + ((& $exe display play-video 0402:3922 $video 2>&1 | Out-String).Trim())
    Start-Sleep $secs
    "STOP  $(Get-Date -Format o): " + ((& $exe display stop-video 0402:3922 2>&1 | Out-String).Trim())
} *> $out
Invoke-WebRequest -UseBasicParsing -Method Put -InFile $out -Uri "$up/video_cadence.txt" | Out-Null
Get-Content $out
"UPLOADED"
