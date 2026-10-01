@echo off
REM Digital Sales Room - local dev launcher.
REM Double-click this, or run it from a terminal. All arguments pass through:
REM   dsr.cmd              start the app
REM   dsr.cmd --stop       stop and exit
REM   dsr.cmd --restart    stop, then start again
REM   dsr.cmd --nobrowser   start without opening the browser

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-dev.ps1" %*
if errorlevel 1 (
  echo.
  echo   Launcher exited with an error. See the message above.
  pause
)
