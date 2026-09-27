@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Python environment was not found.
  echo Run install.bat first.
  echo.
  pause
  exit /b 1
)

echo Starting Roblox Account Hub v1.2.3...
echo Open http://127.0.0.1:8765 in your browser.
echo Press Ctrl+C in this window to stop the panel.
echo.

".venv\Scripts\python.exe" app.py
set "APP_EXIT=%ERRORLEVEL%"

if not "%APP_EXIT%"=="0" (
  echo.
  echo The panel stopped with error code %APP_EXIT%.
  echo If port 8765 is already in use, close the old panel and try again.
  pause
)

exit /b %APP_EXIT%
