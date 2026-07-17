@echo off
title AutoApply Overnight
cd /d "%~dp0"

REM Runs from Windows Task Scheduler at 2:00 AM (wakes the PC).
REM Starts the stack headless; Celery Beat fires the live V2 portfolio at
REM 3:00 AM Central (08:00 UTC during daylight-saving time):
REM search -> score -> select about 20 -> review queue -> generate materials.
REM Materials are generated while cards are still pending; approval is only
REM the later human gate for submission. Local generation can take 1.5-2.5h,
REM so the keep-awake hold below runs until 6:30 AM.
REM Digest lands at 7:00 AM Central. You wake up to the list.

REM --- Skip stack startup if it's already running -------------------------
powershell -NoProfile -Command "try { Invoke-WebRequest -Uri 'http://127.0.0.1:8000/api/dashboard' -UseBasicParsing -TimeoutSec 3 | Out-Null; exit 0 } catch { exit 1 }"
if %errorlevel%==0 (
    echo Stack already running.
    goto keepawake
)

REM --- Ollama --------------------------------------------------------------
where ollama >NUL 2>&1 && (
    REM Prevent concurrent 30B model copies from exhausting 24 GB VRAM.
    set OLLAMA_NUM_PARALLEL=1
    set OLLAMA_MAX_LOADED_MODELS=1
    tasklist /FI "IMAGENAME eq ollama.exe" 2>NUL | find /I "ollama.exe" >NUL || start "Ollama" /MIN ollama serve
)

REM --- Full stack, headless, minimized ------------------------------------
REM threads pool + concurrency 2 (the runbook flow) so material generation
REM doesn't block search/maintenance tasks overnight.
start "AutoApply Stack" /MIN cmd /c "cd /d "%~dp0" && uv run autoapply start --no-open --worker-pool threads --worker-concurrency 2"

:keepawake
REM --- Keep the PC awake for 4.5 hours (2:00 -> 6:30 AM) --------------------
REM ES_CONTINUOUS | ES_SYSTEM_REQUIRED. Sized for the worst case: last plan
REM enqueues materials shortly after 3:00, serialized local-LLM generation for 20 jobs
REM needs up to ~2.5h -> done by ~6:00 with margin. After release the PC
REM may sleep per its power plan; the stack resumes when you wake it.
REM NOTE: decimal literals, not hex — PowerShell parses 0x80000001 as a
REM NEGATIVE Int32 and the uint conversion throws. 2147483649 =
REM ES_CONTINUOUS|ES_SYSTEM_REQUIRED, 2147483648 = ES_CONTINUOUS (release).
powershell -NoProfile -Command "$sig = '[DllImport(\"kernel32.dll\")] public static extern uint SetThreadExecutionState(uint esFlags);'; $k = Add-Type -MemberDefinition $sig -Name 'Power' -Namespace 'Win32' -PassThru; [void]$k::SetThreadExecutionState([uint32]2147483649); Start-Sleep -Seconds 16200; [void]$k::SetThreadExecutionState([uint32]2147483648)"
exit
