@echo off
rem Sets up Resident Access 0: finds Resident Evil 0 HD Remaster and adds the mod to it.
title Resident Access 0 setup
cd /d "%~dp0"
if not exist "python\python.exe" (
  echo The python folder is missing. Please unzip all of Resident Access 0 before running this.
  pause
  exit /b 1
)
"python\python.exe" -m re0access.install
