@echo off
rem Modern UI - opens in an Edge app window. Local only, nothing is sent online.
cd /d "%~dp0app"
start "" pythonw dd_web.py
