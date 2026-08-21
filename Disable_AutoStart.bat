@echo off
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -Command "$shortcutPath = Join-Path ([Environment]::GetFolderPath('Startup')) 'PrintShop.lnk'; if (Test-Path -LiteralPath $shortcutPath) { Remove-Item -LiteralPath $shortcutPath -Force }"
if errorlevel 1 (
    echo Failed to disable automatic startup.
) else (
    echo PrintShop automatic startup is disabled.
)
pause
endlocal
