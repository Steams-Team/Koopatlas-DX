@echo off
cd /d "%~dp0"
py -3 -m pip install --user -r requirements.txt
py -3 koopatlasdx.py %*
if errorlevel 1 pause
