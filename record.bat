@echo off
rem Console recording: record.bat ^<project^> [minutes] [--lang auto^|en^|es] [--speakers 2^|auto] [--save-audio]
cd /d "%~dp0"
set "PY=%VOICE_REC_PYTHON%"
if "%PY%"=="" set "PY=.venv\Scripts\python.exe"
"%PY%" main.py %*
