@echo off
chcp 65001 >nul
cd /d "%~dp0.."
set PY=python
where python >nul 2>nul || set PY=D:\bapiminer-build-venv313\Scripts\python.exe
"%PY%" tools\auto_sign.py --server http://bapimine.click:8099 --token adm_7dbfc1a47ae080b7 %*
if errorlevel 1 pause
