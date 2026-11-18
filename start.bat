@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run setup.bat before launching OmniFlow.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" omniflow_watchdog.py
if errorlevel 1 (
  echo OmniFlow exited abnormally. See %LOCALAPPDATA%\OmniFlow\desktop_crashes.log
  pause
)
