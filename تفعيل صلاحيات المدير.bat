@echo off
rem RX16 Control Center - one-time admin setup (asks for UAC once)
powershell -NoProfile -Command "Start-Process powershell -Verb RunAs -Wait -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','\"%~dp0app\setup-admin.ps1\"'"
if exist "%~dp0app\setup-admin.log" type "%~dp0app\setup-admin.log"
echo.
pause
