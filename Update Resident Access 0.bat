@echo off
rem Looks for a new version of Resident Access 0 on GitHub and installs it.
rem (The mod also does this by itself each time it starts.)
title Resident Access 0 update
cd /d "%~dp0"
"python\python.exe" -m re0access.update
