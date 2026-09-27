@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if %ERRORLEVEL%==0 (
  set "PYTHON_CMD=py"
) else (
  where python >nul 2>nul
  if not %ERRORLEVEL%==0 (
    echo Python was not found in PATH.
    echo Install Python 3.11 or newer and enable Add Python to PATH.
    pause
    exit /b 1
  )
  set "PYTHON_CMD=python"
)

if not exist ".venv\Scripts\python.exe" (
  echo Creating Python environment...
  %PYTHON_CMD% -m venv .venv
  if errorlevel 1 (
    echo Failed to create .venv.
    pause
    exit /b 1
  )
)

echo Installing dependencies...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo Installation failed.
  pause
  exit /b 1
)

echo.
echo Installation completed successfully.
echo Run start.bat to open the panel.
pause
