@echo off
rem ContextLayer - start the backend (Windows). Double-click me.
rem Runs setup automatically the first time.
setlocal
cd /d "%~dp0"
title ContextLayer Backend
call :findpython || goto :nopython
%PYCMD% scripts\manage.py start %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" if not defined CI pause
exit /b %RC%

:findpython
set "PYCMD="
where py >nul 2>nul && py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul && set "PYCMD=py -3"
if not defined PYCMD (
  where python >nul 2>nul && python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul && set "PYCMD=python"
)
if defined PYCMD exit /b 0
exit /b 1

:nopython
echo Python 3.10+ was not found.
echo Install it from https://www.python.org/downloads/ (tick "Add python.exe to PATH")
echo or run:  winget install Python.Python.3.12
if not defined CI pause
exit /b 1
