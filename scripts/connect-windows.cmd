@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0connect-windows.ps1"
if errorlevel 1 (
    echo Windows control did not connect. Keep this window open for the operator.
    pause
)
endlocal
