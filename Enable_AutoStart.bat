@echo off
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -Command "$root = (Resolve-Path '%~dp0').Path; $shortcutPath = Join-Path ([Environment]::GetFolderPath('Startup')) 'PrintShop.lnk'; $shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut($shortcutPath); $shortcut.TargetPath = Join-Path $root 'Start_PrintShop.bat'; $shortcut.WorkingDirectory = $root; $shortcut.Description = 'Start PrintShop after Windows sign-in'; $shortcut.Save()"
if errorlevel 1 (
    echo Failed to enable automatic startup.
) else (
    echo PrintShop will start automatically after Windows sign-in.
)
pause
endlocal
