@echo off
title ContextLayer Backend
echo.
echo  ██████╗ ██████╗ ███╗   ██╗████████╗███████╗██╗  ██╗████████╗
echo  ██╔════╝██╔═══██╗████╗  ██║╚══██╔══╝██╔════╝╚██╗██╔╝╚══██╔══╝
echo  ██║     ██║   ██║██╔██╗ ██║   ██║   █████╗   ╚███╔╝    ██║
echo  ██║     ██║   ██║██║╚██╗██║   ██║   ██╔══╝   ██╔██╗    ██║
echo  ╚██████╗╚██████╔╝██║ ╚████║   ██║   ███████╗██╔╝ ██╗   ██║
echo   ╚═════╝ ╚═════╝ ╚═╝  ╚═══╝   ╚═╝   ╚══════╝╚═╝  ╚═╝   ╚═╝
echo  ContextLayer — Local AI Context Engine
echo  Backend starting on http://localhost:8000
echo  Press Ctrl+C to stop.
echo.
cd /d "C:\Users\user\context layer project\context-layer\backend"
py -3.11 -m uvicorn main:app --port 8000
pause