@echo off
chcp 65001 >nul
cd /d "%~dp0"

set PY=.venv\Scripts\python.exe
if not exist %PY% (
  where python >nul 2>nul
  if errorlevel 1 goto nopython
  echo Creating virtual environment .venv ...
  python -m venv .venv
)

echo Installing/checking libraries ...
%PY% -m pip install -q --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto pipfail
%PY% -m pip install -q --disable-pip-version-check -r requirements-optional.txt >nul 2>&1

if not exist .env copy .env.example .env >nul

echo.
echo  AI Grader: http://localhost:8000
echo  Stop: Ctrl+C
echo.
start "" cmd /c "timeout /t 4 >nul & start http://localhost:8000"
%PY% -m uvicorn app.main:app --reload --port 8000
pause
exit /b 0

:nopython
echo Python was not found. Install Python 3.11 or newer from python.org
echo and tick "Add python.exe to PATH" during installation.
pause
exit /b 1

:pipfail
echo Could not install the libraries. Check the internet connection.
pause
exit /b 1
