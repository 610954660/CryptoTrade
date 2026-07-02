@echo off
REM ============================================================
REM   Chrome Incognito - Restart and open http://localhost:8000/
REM
REM   关闭所有 Chrome 进程, 然后以 --incognito 启动, 直接打开
REM   本地 BOLL Scanner 前端。
REM
REM   注意: 会同时关掉所有 Chrome 标签页 (包括普通标签),
REM         这是为了确保无痕窗口的"干净重启"。
REM         如果不想全部关闭, 把 KILL_CHROME 设为 0 即可。
REM ============================================================
setlocal

set URL=http://localhost:8000/
set KILL_CHROME=1

echo [chrome-incognito] target URL: %URL%

REM ----- 1. 关掉所有 Chrome 进程 -----
if "%KILL_CHROME%"=="1" (
  echo [chrome-incognito] killing all chrome.exe ...
  taskkill /F /IM chrome.exe >nul 2>&1
  REM 等系统释放句柄
  timeout /t 2 /nobreak >nul
)

REM ----- 2. 找 Chrome 安装路径 -----
set CHROME=
if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" (
  set CHROME="%ProgramFiles%\Google\Chrome\Application\chrome.exe"
)
if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" (
  set CHROME="%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
)
if exist "%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe" (
  set CHROME="%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"
)

if not defined CHROME (
  echo [chrome-incognito] ERROR: Chrome not found in standard paths.
  echo   Checked:
  echo     - %%ProgramFiles%%\Google\Chrome\Application\chrome.exe
  echo     - %%ProgramFiles(x86)%%\Google\Chrome\Application\chrome.exe
  echo     - %%LOCALAPPDATA%%\Google\Chrome\Application\chrome.exe
  pause
  exit /b 1
)

echo [chrome-incognito] launching: %CHROME%
echo [chrome-incognito] mode: --incognito --new-window

REM ----- 3. 启动 Chrome 无痕窗口 -----
start "" %CHROME% --incognito --new-window "%URL%"

echo [chrome-incognito] done.
endlocal
