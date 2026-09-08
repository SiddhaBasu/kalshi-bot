@echo off
title Kalshi Weather Bot - LIVE
echo ================================================================
echo  Kalshi Weather Bot
echo  Logs: bot.log  (tail it with: type bot.log)
echo  Keep this window open — closing it stops the bot.
echo  Frontend: open a second terminal and run: npm run dev (in /frontend)
echo ================================================================
echo.

cd /d "%~dp0"

python -m uvicorn backend.api.main:app --port 8000 --log-level info
