@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
    echo Setting up VEIL's Python environment...
    py -3 -m venv .venv
    if errorlevel 1 (echo Python 3 could not be found. & pause & exit /b 1)
)
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 (echo Dependency installation failed. & pause & exit /b 1)
.venv\Scripts\python.exe main.py
if errorlevel 1 pause
