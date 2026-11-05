@echo off
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Build-Windows-Release.ps1"
if errorlevel 1 (echo BUILD FAILED - see log above. & pause & exit /b 1)
echo Build finished.
pause
