@echo off
set PYTHON=%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe
cd /d "%~dp0"
start "" "%PYTHON%" main.py
