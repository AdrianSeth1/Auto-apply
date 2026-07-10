@echo off
title AutoApply - Run Plans Now
cd /d "%~dp0"

REM Manual trigger for the overnight pipeline: pick which saved searches run,
REM right now, with identical behavior to the 2:30am schedule.

REM Stack must be up (worker executes the plans, Redis carries the queue).
powershell -NoProfile -Command "try { Invoke-WebRequest -Uri 'http://127.0.0.1:8000/api/dashboard' -UseBasicParsing -TimeoutSec 3 | Out-Null; exit 0 } catch { exit 1 }"
if errorlevel 1 (
    echo The AutoApply stack is not running. Start it first with "Start AutoApply.bat".
    pause
    exit /b 1
)

uv run python scripts\run_plans_now.py

echo.
pause
