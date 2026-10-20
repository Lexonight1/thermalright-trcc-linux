# ADMIN: stop the trace, convert to CSV, keep only reset/error/storport lines, upload.
$etl = "C:\trccdev\usbreset.etl"; $csv = "C:\trccdev\usbreset.csv"
logman stop trccusb -ets | Out-Null
tracerpt $etl -o $csv -of CSV -y | Out-Null
$hits = Select-String -Path $csv -Pattern "Reset|Abort|Timeout|Error|Fail|Suspend|D3|Power" | Select-Object -First 400 | ForEach-Object { $_.Line.Substring(0, [Math]::Min(300, $_.Line.Length)) }
$hits | Set-Content "C:\trccdev\usbreset_hits.txt"
"$((Get-Item $csv).Length) bytes csv, $($hits.Count) hits" | Add-Content "C:\trccdev\usbreset_hits.txt"
Invoke-WebRequest -UseBasicParsing -Method Put -InFile "C:\trccdev\usbreset_hits.txt" -Uri "http://10.0.2.2:8765/usbreset_hits.txt" | Out-Null
"UPLOADED"
