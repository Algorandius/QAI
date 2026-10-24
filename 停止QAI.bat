@echo off
chcp 65001 >nul
cd /d "%~dp0"
title QAI stop

rem ============================================================
rem  Stop QAI.
rem  The real logic lives in stop.py: it only ends processes that
rem  were started from this folder, so a program of yours sitting
rem  on port 8080 is no longer killed by mistake.
rem  Usage: double-click to run; callers pass /quiet to skip pause.
rem  (ASCII only on purpose: cmd parses this file with the console
rem   code page, so non-ASCII text here would depend on the locale.)
rem ============================================================

set "PY="
if exist "runtime\python\python.exe" set "PY=runtime\python\python.exe"
if not defined PY if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
if not defined PY set "PY=python"

"%PY%" stop.py
if errorlevel 1 echo.& echo [QAI] Stop reported a problem, see the messages above.

if /i "%~1"=="/quiet" goto :eof
echo.
pause
