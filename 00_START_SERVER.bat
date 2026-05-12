@echo off
chcp 65001 >nul
title ScoutFlow V59-K12 Public Domain Ready PORT 8058
cd /d "%~dp0"
echo ========================================================
echo ScoutFlow V59-K12 Public Domain Ready
echo PORT 8058
echo ========================================================
python LAUNCH_SCOUTFLOW_SERVER.py
pause
