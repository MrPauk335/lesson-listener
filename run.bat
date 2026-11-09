@echo off
cd /d "%~dp0"
taskkill /F /IM pythonw.exe >nul 2>&1
if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" app.py
) else if exist "C:\Python314\pythonw.exe" (
    start "" "C:\Python314\pythonw.exe" app.py
) else (
    start "" pythonw.exe app.py
)
