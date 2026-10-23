@echo off
cd /d "%~dp0"
set "FOUND=0"
for %%P in (8080 8081 8082) do (
    for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%%P " ^| findstr LISTENING') do (
        echo Stopping QAI port %%P PID %%a ...
        taskkill /F /PID %%a >nul 2>&1
        set "FOUND=1"
    )
)
if "%FOUND%"=="0" echo QAI is not running.
if exist "data\qai_window.pid" set /p WINDOW_PID=<"data\qai_window.pid"
if not "%WINDOW_PID%"=="" taskkill /F /PID %WINDOW_PID% >nul 2>&1
del "data\qai_window.pid" >nul 2>&1
ping -n 3 127.0.0.1 >nul
