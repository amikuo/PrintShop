$root = [IO.Path]::GetFullPath((Split-Path -Parent $PSCommandPath)).TrimEnd('\')
$healthUrl = "http://127.0.0.1:5000/healthz"
$expectedVersion = "3.11.1"

function Get-PrintShopHealth {
    try {
        return Invoke-RestMethod -Uri $healthUrl -TimeoutSec 1
    } catch {
        return $null
    }
}

function Test-Port5000Open {
    $client = New-Object Net.Sockets.TcpClient
    try {
        $client.Connect("127.0.0.1", 5000)
        return $true
    } catch {
        return $false
    } finally {
        $client.Dispose()
    }
}

function Test-CorrectInstance($health) {
    if ($null -eq $health -or $health.app -ne "PrintShop") {
        return $false
    }
    $sameDataRoot = [string]::Equals(
        ([IO.Path]::GetFullPath([string]$health.data_root)).TrimEnd('\'),
        $root,
        [StringComparison]::OrdinalIgnoreCase
    )
    return $sameDataRoot -and ([string]$health.version -eq $expectedVersion)
}

$health = Get-PrintShopHealth
if (Test-Port5000Open) {
    if (Test-CorrectInstance $health) {
        Start-Process "http://127.0.0.1:5000"
        exit 0
    }

    if ($null -ne $health -and $health.app -eq "PrintShop") {
        $runningVersion = [string]$health.version
        if (-not $runningVersion) { $runningVersion = "unknown" }
        Write-Host "PrintShop is still running as version $runningVersion; this folder requires version $expectedVersion." -ForegroundColor Red
        Write-Host "Close the old PrintShop Server window completely, then run Start_PrintShop.bat again."
        Write-Host "Updating files while the old server is running can show a new page with old save logic."
        Read-Host "Press Enter to close"
        exit 3
    }

    Write-Host "Port 5000 is already used by a different program or PrintShop data folder." -ForegroundColor Red
    Write-Host "This launcher did not open that program, so the wrong database cannot be mistaken for the store database."
    Write-Host "Close the other server window, then run Start_PrintShop.bat again."
    Read-Host "Press Enter to close"
    exit 2
}

$command = 'cd /d "' + $root + '"' +
    ' && set "PRINTSHOP_DATA_DIR=' + $root + '"' +
    ' && set "PRINTSHOP_PORT=5000"' +
    ' && set "PRINTSHOP_DEBUG=0"' +
    ' && py -m app.main'
Start-Process -FilePath "cmd.exe" -ArgumentList @("/k", $command) -WorkingDirectory $root

for ($attempt = 0; $attempt -lt 20; $attempt++) {
    Start-Sleep -Milliseconds 500
    $health = Get-PrintShopHealth
    if (Test-CorrectInstance $health) {
        Start-Process "http://127.0.0.1:5000"
        exit 0
    }
}

Write-Host "PrintShop did not start within 10 seconds. Check the PrintShop Server window for an error." -ForegroundColor Red
Read-Host "Press Enter to close"
exit 1
