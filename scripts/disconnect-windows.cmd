@echo off
setlocal
set "SHORTPOST_CONNECT_DIR=%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$q=[char]34; $file=Join-Path $env:SHORTPOST_CONNECT_DIR 'connect-windows.ps1'; $a='-NoProfile -ExecutionPolicy Bypass -File '+$q+$file+$q+' -Disconnect'; $p=Start-Process -FilePath powershell.exe -Verb RunAs -ArgumentList $a -PassThru -Wait; exit $p.ExitCode"
if errorlevel 1 pause
endlocal
