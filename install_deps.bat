@echo off
REM Quick dependency installer for ARC
echo ====================================
echo  ARC Dependency Installer
echo ====================================
echo.

echo Installing Python dependencies...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

echo.
echo ====================================
echo  Installation Complete!
echo ====================================
echo.
echo Next steps:
echo  1. Copy .env.example to .env
echo  2. Add your GEMINI_API_KEY to .env
echo  3. Run: python arc_wizard.py
echo.
pause
