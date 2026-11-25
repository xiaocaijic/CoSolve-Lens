@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" goto restore

py -3 --version >nul 2>&1
if %errorlevel%==0 goto use_py
python --version >nul 2>&1
if %errorlevel%==0 goto use_python

echo [ERROR] Python 3.11 or newer was not found.
echo Install Python from https://www.python.org/downloads/windows/ and enable "Add Python to PATH".
pause
exit /b 1

:use_py
py -3 -m venv .venv
goto install

:use_python
python -m venv .venv

:install
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r backend\requirements.txt
if errorlevel 1 goto failed

:restore
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\restore-collaboration-state.ps1
if errorlevel 1 goto failed

:ready
echo CoSolve Lens is ready.
echo Double-click 教师登录.exe, 学生登录.exe, or 管理员登录.exe.
pause
exit /b 0

:failed
echo [ERROR] Dependency installation failed. Check the network and README-WINDOWS.txt.
pause
exit /b 1
