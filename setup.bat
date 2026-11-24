@echo off
cd /d "%~dp0"
echo ============================================
echo   Foc^&US - Dependency Setup
echo ============================================
echo.
echo Installing the tested package versions...
pip install -r requirements.txt
echo.
echo Setup complete! Run foc_us.bat to start.
pause
