# ADMIN: install the current QEMU guest agent (guest-exec, guest-file-*).
Invoke-WebRequest -UseBasicParsing -Uri "http://10.0.2.2:8765/qemu-ga-x86_64.msi" -OutFile C:\trccdev\qemu-ga-x86_64.msi
(Get-FileHash C:\trccdev\qemu-ga-x86_64.msi -Algorithm SHA256).Hash
$p = Start-Process msiexec.exe -ArgumentList '/i', 'C:\trccdev\qemu-ga-x86_64.msi', '/qn', '/norestart' -Wait -PassThru
"msiexec exit: " + $p.ExitCode
Get-Service *qemu* | Format-Table Name, Status -AutoSize | Out-String
