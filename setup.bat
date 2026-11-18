@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
  echo Python 3.11 or newer is required. Install from https://www.python.org/downloads/
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" py -3 -m venv .venv
if errorlevel 1 (echo Could not create Python environment. & pause & exit /b 1)
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip uninstall -y opencv-python >nul
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (echo Installation failed. & pause & exit /b 1)
".venv\Scripts\python.exe" tools\check_requirements.py
if errorlevel 1 (echo Dependency self-check failed. & pause & exit /b 1)
echo.
echo Installed successfully. Double-click start.vbs to launch the GUI without a console, or start.bat for diagnostics.
pause
