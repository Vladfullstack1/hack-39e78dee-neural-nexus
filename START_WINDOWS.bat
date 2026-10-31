@echo off
setlocal
cd /d "%~dp0"
title Grand Mobile Support Copilot

echo ================================================
echo   Grand Mobile - Support Copilot
echo ================================================
echo.

where py >nul 2>nul
if %errorlevel%==0 (
    py -3.11 --version >nul 2>nul
    if %errorlevel%==0 (
        set "PYTHON=py -3.11"
    ) else (
        set "PYTHON=py"
    )
) else (
    set "PYTHON=python"
)

if not exist ".venv\Scripts\python.exe" (
    echo [1/3] Creating virtual environment...
    %PYTHON% -m venv .venv
    if errorlevel 1 goto :error
)

call ".venv\Scripts\activate.bat"

.venv\Scripts\python.exe -c "import fastapi, uvicorn, pydantic, dotenv" >nul 2>nul
if errorlevel 1 (
    echo [2/3] Installing dependencies. Internet connection is required once...
    .venv\Scripts\python.exe -m pip install --upgrade pip
    if errorlevel 1 goto :error
    .venv\Scripts\python.exe -m pip install -r requirements.txt
    if errorlevel 1 goto :error
) else (
    echo [2/3] Dependencies already installed.
)

if not exist ".env" copy ".env.example" ".env" >nul

echo [3/3] Starting local service...
start "" "http://127.0.0.1:8000"
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000

echo.
echo The service has stopped.
pause
exit /b 0

:error
echo.
echo Startup failed. Check that Python 3.11+ is installed and that your internet connection works.
pause
exit /b 1
