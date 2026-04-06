@echo off
echo ====================================
echo  Installing AI Dependencies
echo ====================================
echo.

echo This will install:
echo   - python-dotenv (for .env files)
echo   - google-generativeai (for Gemini API)
echo   - pydantic (for data validation)
echo   - jinja2 (for templates)
echo.

python -m pip install python-dotenv google-generativeai pydantic jinja2

echo.
echo ====================================
echo  Installation Complete!
echo ====================================
echo.
echo Next: python test_env.py
echo.
pause
