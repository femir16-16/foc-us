@echo off
cd /d "%~dp0"
python main.py
if %errorlevel% neq 0 (
    echo.
    echo Error running Foc^&US. Run setup.bat first to install dependencies.
    pause
)
