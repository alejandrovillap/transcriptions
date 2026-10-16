@echo off
rem One-time setup: local virtualenv with all dependencies (CPU build of torch).
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
    python -m venv .venv || goto :error
)
.venv\Scripts\python -m pip install --upgrade pip || goto :error
.venv\Scripts\python -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu || goto :error
.venv\Scripts\python -m pip install -r requirements.txt || goto :error
echo.
echo Setup complete. Run webui.bat (browser UI) or record.bat ^<project^> (console).
exit /b 0
:error
echo Setup failed.
exit /b 1
