@echo off
rem Starts Resident Access 0. The game starts it by itself, so you only need this
rem if you want to start it yourself. Starting it again closes the old copy.
title Resident Access 0
cd /d "%~dp0"
set PY=
if exist "python\python.exe" set PY="python\python.exe"
if not defined PY where py >nul 2>nul && set PY=py -3
if not defined PY where python >nul 2>nul && set PY=python
if not defined PY (
  echo Python was not found. Please download Resident Access 0 again from its GitHub page.
  pause
  exit /b 1
)
if not exist "python\python.exe" (
  %PY% -c "import numpy" >nul 2>nul || %PY% -m pip install numpy
)
:run
%PY% -m re0access
rem Exit code 4: a newer copy of the mod has taken over, so close this window.
if errorlevel 4 if not errorlevel 5 exit
rem Exit code 3: the mod has just updated itself, so start the new version.
if errorlevel 3 if not errorlevel 4 goto run
pause
