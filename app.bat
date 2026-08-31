@echo off
title BIBLIOTECA UNDAC - SISTEMA ONLINE

cd /d E:\API

echo ============================================
echo      SISTEMA BIBLIOTECARIO UNDAC
echo ============================================
echo.
echo [1/3] Activando entorno virtual...
call .venv\Scripts\activate.bat

echo [2/3] Iniciando servidor Flask / Waitress...
start "Servidor Biblioteca UNDAC" cmd /k "cd /d E:\API && call .venv\Scripts\activate.bat && python app.py"

echo [3/3] Esperando servidor...
timeout /t 4 /nobreak >nul

echo.
echo Abriendo:
echo https://bibliotecarenzo.xyz
echo.

start "" "https://bibliotecarenzo.xyz"

echo ============================================
echo SISTEMA ONLINE
echo ============================================
echo Dominio:
echo https://bibliotecarenzo.xyz
echo.
echo Cloudflare Tunnel se ejecuta como
echo servicio de Windows automaticamente.
echo.
echo NO es necesario usar trycloudflare.com
echo ============================================

pause