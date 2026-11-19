@echo off
REM Double-click to start the Chat Media dashboard, then use it in your browser.
setlocal
cd /d "%~dp0"
if "%PORT%"=="" set PORT=8000
REM Set HOST=0.0.0.0 to also open it from your phone on the same Wi-Fi (passcode protected).
if "%HOST%"=="" set HOST=127.0.0.1
REM Web workers, and how many requests each one serves before it is replaced with a fresh one.
if "%WEB_WORKERS%"=="" set WEB_WORKERS=4
if "%MAX_REQUESTS%"=="" set MAX_REQUESTS=2000
if "%MAX_REQUESTS_JITTER%"=="" set MAX_REQUESTS_JITTER=200
REM The background engine (Instagram work) is replaced after this many tasks.
if "%ENGINE_MAX_JOBS%"=="" set ENGINE_MAX_JOBS=50

if not exist ".venv\Scripts\python.exe" (
  echo Setting up Python for the first time...
  py -3 -m venv .venv 2>nul || python -m venv .venv
  if errorlevel 1 ( echo Please install Python 3.10+ from https://www.python.org/downloads/ & pause & exit /b 1 )
)
echo Checking Python packages...
".venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check -r dashboard\backend\requirements.txt
if errorlevel 1 ( echo Installing packages failed. Check the internet connection. & pause & exit /b 1 )

if not exist "dashboard\frontend\dist\index.html" (
  where npm >nul 2>nul
  if errorlevel 1 ( echo The web app isn't built and Node.js isn't installed. Install Node.js 20+ from https://nodejs.org or use Docker. & pause & exit /b 1 )
  echo Building the web app (first time only^)...
  pushd dashboard\frontend
  call npm ci --no-audit --no-fund && call npm run build
  popd
)

echo.
echo  Chat Media is starting at http://localhost:%PORT%
echo  Keep this window open while you use it. Close it to stop the app.
echo.
start "" cmd /c "timeout /t 3 >nul & start http://localhost:%PORT%"
set "PYTHONPATH=%~dp0dashboard\backend"
".venv\Scripts\python.exe" -m app.serve
pause
