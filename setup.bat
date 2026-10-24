@echo off
REM One-time setup for Cutoff on Windows. Double-click this file.
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8

echo [1/5] Checking Ollama...
where ollama >nul 2>nul
if errorlevel 1 (
  echo Ollama is not on PATH. Install it from https://ollama.com/download, then run this again.
  pause & exit /b 1
)

echo [2/5] Downloading Gemma 4 (several GB, only the first time)...
ollama pull gemma4:e4b
if errorlevel 1 ( echo Model download failed. Check your internet and run this again. & pause & exit /b 1 )

echo [3/5] Creating a Python environment...
if not exist .venv (
  where py >nul 2>nul && ( py -3 -m venv .venv ) || ( python -m venv .venv )
)
if not exist .venv\Scripts\python.exe ( echo Python not found. Install Python 3.10+ from python.org and tick "Add to PATH". & pause & exit /b 1 )

echo [4/5] Installing Cutoff...
.venv\Scripts\python.exe -m pip install --quiet --upgrade pip
.venv\Scripts\python.exe -m pip install --quiet -e ".[dev]"

if not exist data\inbox mkdir data\inbox

echo [5/5] Testing...
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m cutoff doctor
echo.
echo Setup done. Next: double-click demo.bat
pause
