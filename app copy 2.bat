@echo off
setlocal
title SISTEMA BIBLIOTECARIO UNDAC - ONLINE

cd /d "%~dp0"

echo ============================================================
echo       SISTEMA BIBLIOTECARIO UNDAC - MODO ONLINE
echo ============================================================
echo.

set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"

:: ------------------------------------------------------------
:: 1. Verificar entorno virtual
:: ------------------------------------------------------------
echo [1/5] Verificando Python...

if not exist "%PYTHON_EXE%" (
    echo.
    echo [ERROR] No se encontro Python dentro de .venv.
    echo.
    echo Ejecuta:
    echo py -3.11 -m venv .venv
    echo .venv\Scripts\python.exe -m pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

"%PYTHON_EXE%" --version

:: ------------------------------------------------------------
:: 2. Verificar Cloudflare
:: ------------------------------------------------------------
echo.
echo [2/5] Verificando Cloudflare Tunnel...

where cloudflared >nul 2>&1

if errorlevel 1 (
    echo.
    echo [ERROR] cloudflared no esta instalado o no esta en PATH.
    echo.
    pause
    exit /b 1
)

cloudflared --version

:: ------------------------------------------------------------
:: 3. Esperar brevemente a SQL Server
:: ------------------------------------------------------------
echo.
echo [3/5] Esperando que SQL Server este disponible...
timeout /t 5 /nobreak >nul

:: ------------------------------------------------------------
:: 4. Iniciar Flask + Waitress
:: ------------------------------------------------------------
echo.
echo [4/5] Iniciando Flask + Waitress...

start "Biblioteca UNDAC - Servidor" cmd /k ""%PYTHON_EXE%" "%~dp0app.py""

echo Esperando que el servidor inicie...
timeout /t 5 /nobreak >nul

:: Abrir sistema localmente
start "" "http://127.0.0.1:5000"

:: ------------------------------------------------------------
:: 5. Iniciar Cloudflare Quick Tunnel
:: ------------------------------------------------------------
echo.
echo [5/5] Iniciando Cloudflare Tunnel...

start "Biblioteca UNDAC - Cloudflare" cmd /k "cloudflared tunnel --url http://localhost:5000"

echo.
echo ============================================================
echo                SISTEMA INICIADO
echo ============================================================
echo.
echo Se abrieron dos ventanas:
echo.
echo   1. Flask / Waitress
echo   2. Cloudflare Tunnel
echo.
echo En la ventana de Cloudflare aparecera una URL:
echo.
echo   https://xxxxxxxx.trycloudflare.com
echo.
echo Esa es la direccion publica del sistema.
echo.
echo IMPORTANTE:
echo No cierres Flask ni Cloudflare mientras el sistema este online.
echo.
pause