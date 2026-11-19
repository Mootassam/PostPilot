@echo off
title PostPilot - Social Media Scheduler
cd /d "%~dp0"
python -c "import flask, requests, PIL, cryptography, anthropic" 2>nul || (
  echo Installing required packages, one moment...
  python -m pip install -r requirements.txt
)
python server.py
pause
