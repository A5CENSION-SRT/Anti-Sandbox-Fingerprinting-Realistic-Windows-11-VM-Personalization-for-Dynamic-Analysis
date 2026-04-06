@echo off
echo ====================================
echo  Installing python-dotenv
echo ====================================
echo.

python -m pip install python-dotenv

echo.
echo ====================================
echo  Testing .env loading...
echo ====================================
echo.

python test_env.py

echo.
pause
