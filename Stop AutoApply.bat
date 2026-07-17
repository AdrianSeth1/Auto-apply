@echo off
title Stop AutoApply
cd /d "%~dp0"

echo ============================================
echo  AutoApply - stopping everything
echo ============================================
echo.

REM --- 1. AutoApply python processes (web server, Celery worker, Beat) ---
REM Matched by executable path inside this project's .venv, so nothing
REM else on the machine gets touched.
echo Stopping web server + Celery worker/beat...
powershell -NoProfile -Command ^
  "Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -like '%~dp0.venv*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"

REM --- 2. Postgres + Redis containers ------------------------------------
echo Stopping Postgres + Redis containers...
docker compose down >NUL 2>&1

REM --- 3. Docker Desktop + the WSL VM (vmmem eats gigabytes of RAM) -------
echo Stopping Docker Desktop...
powershell -NoProfile -Command ^
  "Stop-Process -Name 'Docker Desktop' -Force -ErrorAction SilentlyContinue; Stop-Process -Name 'com.docker.backend' -Force -ErrorAction SilentlyContinue; Stop-Process -Name 'com.docker.build' -Force -ErrorAction SilentlyContinue"
wsl --shutdown >NUL 2>&1

REM --- 4. Ollama (frees VRAM: unloads models, kills server + tray app) ----
echo Stopping Ollama and freeing VRAM...
powershell -NoProfile -Command ^
  "Stop-Process -Name 'ollama' -Force -ErrorAction SilentlyContinue; Stop-Process -Name 'ollama app' -Force -ErrorAction SilentlyContinue; Stop-Process -Name 'ollama_llama_server' -Force -ErrorAction SilentlyContinue; Stop-Process -Name 'llama-server' -Force -ErrorAction SilentlyContinue"

echo.
echo Done. Web server, workers, Postgres, Redis, Docker Desktop, WSL VM,
echo and Ollama are stopped. RAM and VRAM released.
echo.
pause
