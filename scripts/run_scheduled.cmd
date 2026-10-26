@echo off
rem Scheduled task wrapper: run_scheduled.cmd <log name> [script name] [arg1] [arg2]
rem runs scripts\<script>.py (default: <log name>) from project root and appends output to logs\<log name>.log.
cd /d "%~dp0.."
if not exist logs mkdir logs
set PYTHONIOENCODING=utf-8
set SCRIPT=%2
if "%SCRIPT%"=="" set SCRIPT=%1
echo ===== %date:~0,10% %time% >> "logs\%1.log"
".venv\Scripts\python.exe" "scripts\%SCRIPT%.py" %3 %4 >> "logs\%1.log" 2>&1
set RC=%ERRORLEVEL%
echo ----- exit %RC% >> "logs\%1.log"
exit /b %RC%
