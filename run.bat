@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
echo.
echo  =====================================================
echo   Enterprise Code Review Automation
echo  =====================================================
echo.

REM Check .env exists
if not exist "%~dp0.env" (
    echo  [WARN] .env file not found.
    echo  Please copy .env.example to .env and add your ANTHROPIC_API_KEY.
    echo.
    copy "%~dp0.env.example" "%~dp0.env" >nul 2>&1
    echo  Created .env from template. Edit it before continuing.
    echo.
    pause
    exit /b 1
)

REM Install dependencies if needed
python -c "import anthropic" 2>nul
if errorlevel 1 (
    echo  Installing dependencies...
    pip install -r "%~dp0requirements.txt"
    echo.
)

echo  Starting server at http://localhost:7000
echo  Press Ctrl+C to stop.
echo.
python "%~dp0server.py"
pause
