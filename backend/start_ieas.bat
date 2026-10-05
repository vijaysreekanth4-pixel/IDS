@echo off
cd /d "c:\Users\N.Sreekanth\OneDrive\Desktop\Intelligent Sytem\candidate_solution"

:: Kill any old instance on port 8001
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":8001" 2^>nul') do (
    taskkill /PID %%a /F >nul 2>&1
)

:: Start server silently in background (no visible window)
start "" /B python -m uvicorn backend.main:app --host 127.0.0.1 --port 8001 >nul 2>&1

:: Wait for server to be ready
timeout /t 4 /nobreak >nul

:: Open browser
start "" "http://127.0.0.1:8001/app/login.html"
