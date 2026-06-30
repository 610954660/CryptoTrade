@echo off
REM ============================================================
REM   BOLL Scanner - Stop backend on port 8000
REM ============================================================
setlocal
set PORT=8000

set FOUND=0
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":%PORT% " ^| findstr "LISTENING"') do (
  echo [stop] killing PID %%P on port %PORT% ...
  taskkill /F /PID %%P
  set FOUND=1
)

if "%FOUND%"=="0" echo [stop] no process listening on port %PORT%

REM Also clean up any stray python.exe just in case
for /f "tokens=2" %%P in ('tasklist /FI "IMAGENAME eq python.exe" /FO LIST 2^>nul ^| findstr "PID:"') do (
  tasklist /FI "PID eq %%P" /FO LIST 2^>nul | findstr "main.py" >nul
  if not errorlevel 1 (
    echo [stop] killing stray python main.py PID %%P ...
    taskkill /F /PID %%P
  )
)
