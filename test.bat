@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (echo Run setup.bat first & pause & exit /b 1)
".venv\Scripts\python.exe" -m unittest discover -s tests -q
if errorlevel 1 (pause & exit /b 1)
".venv\Scripts\python.exe" -m unittest discover -s tests_omni -q
if errorlevel 1 (pause & exit /b 1)
echo Both OmniFlow and legacy compatibility suites passed.
pause
