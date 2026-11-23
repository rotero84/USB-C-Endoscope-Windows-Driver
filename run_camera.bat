@echo off
setlocal
if not exist ".venv\Scripts\python.exe" (
    echo Virtual environment not found. Running setup.ps1...
    powershell -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
)
".venv\Scripts\python.exe" "%~dp0useeplus_driver.py" %*
pause
