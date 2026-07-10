@echo off
title AutoApply
cd /d "%~dp0"

echo ============================================
echo  AutoApply - starting everything
echo ============================================
echo.

REM --- 1. Ollama (local LLM) -------------------------------------------
REM autoapply start handles Docker/Postgres/Redis/worker/web itself,
REM but not Ollama, so we make sure it's up first.
where ollama >NUL 2>&1
if errorlevel 1 (
    echo [warn] ollama not found on PATH - LLM features will be unavailable.
) else (
    tasklist /FI "IMAGENAME eq ollama.exe" 2>NUL | find /I "ollama.exe" >NUL
    if errorlevel 1 (
        echo Starting Ollama...
        start "Ollama" /MIN ollama serve
    ) else (
        echo Ollama already running.
    )
)
echo.

REM --- 2. Everything else ----------------------------------------------
REM Starts Docker Desktop if needed, Postgres+Redis via compose, runs DB
REM migrations, launches the Celery worker + beat, starts the web server,
REM and opens http://localhost:8000 in the browser.
REM threads pool + concurrency 2 (the runbook flow) instead of the Windows
REM default solo pool, so generation doesn't block search/maintenance tasks.
uv run autoapply start --worker-pool threads --worker-concurrency 2

echo.
echo AutoApply stopped. Press any key to close this window.
pause >NUL
