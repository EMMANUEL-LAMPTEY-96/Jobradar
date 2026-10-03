@echo off
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py app.py
) else (
  python app.py
)
if errorlevel 1 (
  echo.
  echo JobRadar could not start. Is Python installed with "Add python.exe to PATH" ticked?
)
pause
