@echo off
REM Runs Gemma on the 10 sample emails and prints the morning digest.
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
.venv\Scripts\python.exe -m cutoff run --inbox data\fixtures\emails --now 2026-10-24T08:00
echo.
echo Full details (why each email got its verdict) are in digest.md
pause
