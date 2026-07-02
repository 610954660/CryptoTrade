@echo off
REM ============================================================
REM   BOLL Scanner - Backend Restart (Windows)
REM   Pure ASCII, no Chinese in echo to avoid GBK mojibake.
REM ============================================================
setlocal

REM ----- 1. Find and kill the running uvicorn/python on port 8000 -----
set PORT=8000
echo [stop] looking for process on port %PORT% ...

REM Find PIDs listening on %PORT% (parent uvicorn / uvicorn child)
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":%PORT% " ^| findstr "LISTENING"') do (
  echo [stop] killing PID %%P (listening on %PORT%) ...
  taskkill /F /PID %%P >nul 2>&1
)

REM Also kill any python whose command line contains "main.py" (uvicorn reloaders
REM may not bind the port themselves). DO NOT touch other python.exe — Claude
REM Code, MCP servers and other tooling all run as python.exe and would crash
REM the entire session if killed.
for /f "tokens=2" %%P in ('wmic process where "name='python.exe' and commandline like '%%main.py%%'" get processid /format:list 2^>nul ^| findstr /R "ProcessId="') do (
  set "PIDX=%%P"
  echo [stop] killing python main.py PID !PIDX! ...
  for /f "tokens=2 delims==" %%Q in ("!PIDX!") do taskkill /F /PID %%Q >nul 2>&1
)

REM Give OS a moment to release the port
timeout /t 2 /nobreak >nul

REM ----- 2. Delegate to start-backend.bat -----
echo [start] launching backend ...
call "%~dp0start-backend.bat"
