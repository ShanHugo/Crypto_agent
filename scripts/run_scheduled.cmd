@echo off
rem Scheduled task wrapper: run scripts\%1.py from project root and append output to logs\%1.log.
cd /d "%~dp0.."
if not exist logs mkdir logs
set PYTHONIOENCODING=utf-8
echo ===== %date% %time% >> "logs\%1.log"
".venv\Scripts\python.exe" "scripts\%1.py" >> "logs\%1.log" 2>&1
set RC=%ERRORLEVEL%
echo ----- exit %RC% >> "logs\%1.log"
exit /b %RC%
