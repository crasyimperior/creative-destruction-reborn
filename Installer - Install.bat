@echo off
cd /d "%~dp0"
where python >nul 2>nul || (echo Python 3 est requis / Python 3 is required: https://www.python.org/downloads/ & pause & exit /b 1)
python "%~dp0reborn\tools\install.py"
pause
