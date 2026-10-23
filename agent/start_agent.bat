@echo off
rem MinRigs agent launcher (run on each mining rig)
cd /d %~dp0
python agent.py
pause
