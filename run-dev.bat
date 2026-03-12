@echo off
setlocal enabledelayedexpansion

echo ==============================
echo RUN PROGRAM
echo ==============================

REM Start Python
start "PYTHON_APP" cmd /c python app.py
set PY_PID=%!

REM Start NPM
start "NPM_DEV" cmd /c npm run dev
set NPM_PID=%!

echo.
echo 🚀 Apps running
exit /b
