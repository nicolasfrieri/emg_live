@echo off
setlocal
title EMG + mouse - Arduino UNO R4 Minima
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo No se encontro el entorno Python del proyecto.
    echo Ejecuta setup.ps1 antes de abrir la aplicacion.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" "app.py" --mouse-events %*
if errorlevel 1 (
    echo.
    echo La aplicacion termino con un error. Revisa el mensaje anterior.
    pause
    exit /b 1
)
endlocal
