@echo off
REM ============================================================
REM  QQ Bot Launcher  (pure ASCII on purpose: non-ASCII text in
REM  a .bat file breaks cmd parsing on Chinese Windows / GBK)
REM ============================================================
cd /d %~dp0
setlocal enabledelayedexpansion

echo ============================================
echo   QQ Bot Launcher
echo ============================================

REM ---- 1) kill old instances holding port 8080 ----
set "PORT=8080"
for /f "tokens=1-5" %%a in ('netstat -ano ^| findstr ":%PORT%" ^| findstr LISTENING') do (
    if not "%%e"=="" (
        echo [!] Port %PORT% is used by PID %%e, killing it...
        taskkill /F /PID %%e >nul 2>&1
    )
)
REM NOTE: no PowerShell-wide process kill here on purpose.
REM A previous version used Get-CimInstance + Terminate() with broken quote
REM escaping, which could kill EVERY python.exe on the machine. The port-level
REM taskkill above is precise enough, so we only wait for the port to release.
ping -n 3 127.0.0.1 >nul
echo [OK] old instance cleaned

REM ---- 2) virtual env ----
if not exist ".venv\Scripts\python.exe" (
    echo [*] Creating virtual environment...
    python -m venv .venv
    if errorlevel 1 (
        echo [X] Failed to create venv. Please install Python 3.10+ and add it to PATH.
        pause
        exit /b 1
    )
)

REM ---- 3) dependencies: install only if missing (skip slow pip on every start) ----
".venv\Scripts\python.exe" -c "import nonebot, httpx, dotenv" >nul 2>&1
if errorlevel 1 (
    echo [*] Installing dependencies, first run may take a while...
    ".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
    if errorlevel 1 (
        echo [X] Dependency install failed. Run manually:
        echo     .venv\Scripts\python.exe -m pip install -r requirements.txt
        pause
        exit /b 1
    )
)

echo [*] Starting bot, listening on 127.0.0.1:8080
echo [*] KEEP THIS WINDOW OPEN. Press Ctrl+C to stop.
echo ============================================
".venv\Scripts\python.exe" bot.py

echo.
echo [*] Bot exited. Press any key to close.
pause
