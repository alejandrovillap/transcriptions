@echo off
rem Launch the voice-rec web UI (http://127.0.0.1:8765).
rem Uses %VOICE_REC_PYTHON% if set, else the .venv from setup.bat.
cd /d "%~dp0"
set "PY=%VOICE_REC_PYTHON%"
if "%PY%"=="" set "PY=.venv\Scripts\python.exe"
"%PY%" server.py %*
