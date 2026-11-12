@echo off
TITLE Beldex Nexus Rate Sentinel
echo ========================================================
echo     Starting Beldex Nexus Rate Sentinel (Windows)
echo     Autonomous 30m Rate Difference ^& Selling Alert System
echo ========================================================

where python >nul 2>nul
if %ERRORLEVEL% NEQ 0 (
    echo Error: Python is required but not found in PATH.
    pause
    exit /b 1
)

if not exist venv (
    echo Creating virtual environment...
    python -m venv venv
    call venv\Scripts\activate.bat
    python -m pip install --upgrade pip
    pip install -r requirements.txt
) else (
    call venv\Scripts\activate.bat
)

if "%1"=="cli" (
    shift
    python cli.py %*
) else if "%1"=="status" (
    python cli.py status
) else (
    echo Starting web dashboard on http://localhost:8080
    python -m uvicorn app.main:app --host 0.0.0.0 --port 8080
)
pause
