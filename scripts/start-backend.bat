@echo off
REM ============================================================
REM   BOLL Scanner - Backend Launcher (Windows)
REM   Pure ASCII to avoid GBK / UTF-8 mojibake in cmd.exe
REM ============================================================
setlocal

REM ------------------------------------------------------------
REM   Proxy settings - uncomment / edit ONE of the lines below.
REM   Leave them all REM'd to disable proxy.
REM ------------------------------------------------------------

REM HTTP / HTTPS proxy (Clash, v2rayN, etc., default port 7890)
set HTTPS_PROXY=http://127.0.0.1:7897
set HTTP_PROXY=http://127.0.0.1:7897

REM SOCKS5 proxy (uncomment both lines if you use one)
REM set HTTPS_PROXY=socks5://127.0.0.1:1080
REM set HTTP_PROXY=socks5://127.0.0.1:1080

REM Force backend to use OKX directly (skip Binance)
REM set CRYPTO_PROVIDER=okx

REM Switch to backend dir
pushd "%~dp0..\backend"

if not exist ".venv\Scripts\python.exe" (
  echo [setup] creating virtualenv .venv ...
  python -m venv .venv
  if errorlevel 1 goto :err
)

call ".venv\Scripts\activate.bat"
if errorlevel 1 goto :err

REM SOCKS5 needs httpx[socks] extra; install on demand
echo %HTTPS_PROXY% | findstr /I "socks5://" >nul
if not errorlevel 1 (
  echo [deps] socks5 proxy detected, ensuring httpx[socks] ...
  python -m pip install --disable-pip-version-check -q "httpx[socks]"
  if errorlevel 1 goto :err
)

echo [install] installing requirements ...
python -m pip install --disable-pip-version-check -q -r requirements.txt
if errorlevel 1 goto :err

echo [run] starting FastAPI on http://localhost:8000 ...
python main.py
goto :eof

:err
echo.
echo [ERROR] setup failed. Make sure Python 3.10+ is on PATH.
echo         Then run manually:  python -m venv .venv ^&^& .venv\Scripts\activate ^&^& pip install -r requirements.txt ^&^& python main.py
popd
exit /b 1
