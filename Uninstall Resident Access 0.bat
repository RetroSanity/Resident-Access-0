@echo off
rem Takes Resident Access 0 out of the game folder (puts back anything it replaced there).
title Resident Access 0 uninstall
cd /d "%~dp0"
"python\python.exe" -m re0access.install --uninstall
