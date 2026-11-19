@echo off
title PostPilot - DEMO MODE
cd /d "%~dp0"
set PP_FAKE=1
set PP_PORT=5089
set PP_DATA=%~dp0demo-data
python server.py
pause
