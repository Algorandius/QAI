@echo off
cd /d "%~dp0"
echo ============================================
echo   QAI Control Center
echo ============================================
echo If a center window does not open, check logs\launcher.log
".venv\Scripts\python.exe" launcher.py
pause
