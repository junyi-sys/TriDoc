@echo off
echo === TriDoc Setup ===
echo.
echo Installing backend dependencies...
cd /d %~dp0backend
pip install -r requirements.txt
echo.
echo Installing frontend dependencies...
cd /d %~dp0frontend
call npm install
echo.
echo === Setup Complete ===
echo Run start.bat to launch the application.
pause
