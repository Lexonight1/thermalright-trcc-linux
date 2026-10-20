# ADMIN: start an ETW trace of the USB + storage stack until a guest-side reset of the panel happens.
$etl = "C:\trccdev\usbreset.etl"
logman stop trccusb -ets 2>$null | Out-Null
Remove-Item $etl -ErrorAction SilentlyContinue
logman create trace trccusb -ets -o $etl -nb 64 256 -bs 1024 -mode Circular -max 256 `
    -p "Microsoft-Windows-USB-USBXHCI" 0xFFFFFFFFFFFFFFFF 0xff
foreach ($p in "Microsoft-Windows-USB-UCX", "Microsoft-Windows-USB-USBHUB3", "Microsoft-Windows-StorPort", "Microsoft-Windows-Kernel-PnP") {
    logman update trace trccusb -ets -p $p 0xFFFFFFFFFFFFFFFF 0xff
}
logman query trccusb -ets | Select-String "Status|Output|Provider|Name:"
"TRACE STARTED $(Get-Date -Format o)"
