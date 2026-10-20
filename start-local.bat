@echo off
rem ErnestOS on this computer (Windows): double-click this file.
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 run_local.py %*
) else (
  python run_local.py %*
)
pause
