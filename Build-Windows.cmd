@echo off
setlocal
cd /d "%~dp0"
py -3.11 desktop\build_windows.py
if errorlevel 1 (
  echo Build failed. See the error above. Python 3.11 x64 is required.
  pause
  exit /b 1
)
echo Build complete. See dist\Callnote-Windows-x64.zip
pause
