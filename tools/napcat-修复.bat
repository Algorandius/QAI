@echo off
chcp 65001 >nul
title NapCat duplicate-login fixer
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0napcat-修复.ps1"
if errorlevel 1 pause
