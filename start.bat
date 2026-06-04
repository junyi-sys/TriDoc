@echo off
echo === TriDoc ===
echo Backend: http://localhost:8086
echo Frontend: http://localhost:5173
echo API Docs: http://localhost:8086/docs
echo.
echo Starting backend...
start "TriDoc Backend" cmd /c "cd /d %~dp0backend && python -m uvicorn main:app --reload --port 8086"
echo.
echo Starting frontend...
start "TriDoc Frontend" cmd /c "cd /d %~dp0frontend && npx vite --port 5173"
echo.
echo Both servers started. Press any key to stop...
pause >nul
taskkill /FI "WINDOWTITLE eq TriDoc*" 2>nul
