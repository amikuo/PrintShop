$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Start-Process -FilePath "powershell.exe" -Verb RunAs -ArgumentList @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", "`"$PSCommandPath`""
    )
    exit
}

$ruleName = "PrintShop TCP 5000"
$existing = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
if ($null -eq $existing) {
    New-NetFirewallRule `
        -DisplayName $ruleName `
        -Direction Inbound `
        -Action Allow `
        -Protocol TCP `
        -LocalPort 5000 `
        -Profile Private | Out-Null
    Write-Host "PrintShop TCP port 5000 is now allowed on Private networks." -ForegroundColor Green
} else {
    Set-NetFirewallRule -DisplayName $ruleName -Enabled True -Profile Private | Out-Null
    Write-Host "The existing PrintShop TCP 5000 rule is enabled." -ForegroundColor Green
}

Read-Host "Press Enter to close"
