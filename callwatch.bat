@echo off
rem Call watcher: icon next to the clock; asks to record when Teams/Zoom/Meet/... take the microphone.
rem It starts the web UI server by itself when needed. Quit from the icon menu.
cd /d "%~dp0"
start "" .venv\Scripts\pythonw.exe call_watcher.py
