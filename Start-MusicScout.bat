@echo off
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 MusicScout-auto.py
) else (
  python MusicScout-auto.py
)
if errorlevel 1 (
  echo Python 3 is needed to run MusicScout.
  pause
)
