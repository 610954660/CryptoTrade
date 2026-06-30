@echo off
REM ============================================================
REM   BOLL Scanner - Backend Restart (Windows)
REM   Pure ASCII, no Chinese in echo to avoid GBK mojibake.
REM ============================================================
setlocal

REM ----- 1. Find and kill the running uvicorn/python on port 8000 -----
set PORT=8000
echo [stop] looking for process on port %PORT% ...

for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":%PORT% " ^| findstr "LISTENING"') do (
  echo [stop] killing PID %%P ...
  taskkill /F /PID %%P >nul 2>&1
)

REM Also kill any stray python main.py (uvicorn reloader children etc.)
for /f "tokens=2" %%P in ('tasklist /FI "IMAGENAME eq python.exe" /FO LIST 2^>nul ^| findstr "PID:"') do (
  echo [stop] killing stray python.exe PID %%P ...
  taskkill /F /PID %%P >nul 2>&1
)

REM Give OS a moment to release the port
timeout /t 2 /nobreak >nul

REM ----- 2. Delegate to start-backend.bat -----
echo [start] launching backend ...
call "%~dp0start-backend.bat"
