@echo off
setlocal
title SISTEMA INGRESO UNDAC

cd /d "%~dp0"

echo ============================================================
echo    SISTEMA BIBLIOTECARIO UNDAC - ENTORNO DE PRODUCCION
echo ============================================================
echo.
echo Iniciando servidor del sistema...
echo No cierres esta ventana mientras el sistema este en uso.
echo.

echo [1/3] Esperando a que los servicios de SQL Server inicien...
:: Esto da tiempo a que la base de datos "despierte"
timeout /t 115 /nobreak > nul

set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"

if not exist "%PYTHON_EXE%" (
    echo [ERROR] No se encontro Python dentro de .venv.
    echo.
    echo Ejecuta primero:
    echo py -m venv .venv
    echo .venv\Scripts\python.exe -m pip install -r requirements.txt
    echo.
    pause
    exit
)

echo Verificando Python...
"%PYTHON_EXE%" --version

echo.
echo Abriendo sistema en navegador...
start "" "C:\Program Files\Google\Chrome\Application\chrome.exe" --start-maximized --app="http://127.0.0.1:5000"

echo.
echo Iniciando Waitress...
"%PYTHON_EXE%" "%~dp0app.py"

pause