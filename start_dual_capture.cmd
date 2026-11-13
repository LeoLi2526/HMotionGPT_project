@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Project Python environment not found. See docs/DUAL_CAPTURE_GUIDE.md.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -X utf8 scripts\dual_capture_wizard.py
set "capture_exit=%errorlevel%"
pause
exit /b %capture_exit%
