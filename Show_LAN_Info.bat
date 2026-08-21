@echo off
powershell -NoProfile -Command "Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway -ne $null } | ForEach-Object { $adapter = Get-NetAdapter -InterfaceIndex $_.InterfaceIndex; [PSCustomObject]@{ Adapter=$_.InterfaceAlias; IPv4=($_.IPv4Address.IPAddress -join ', '); Gateway=$_.IPv4DefaultGateway.NextHop; MAC=$adapter.MacAddress } } | Format-List"
echo Use the IPv4 and MAC values above to create a DHCP reservation in the router.
pause
