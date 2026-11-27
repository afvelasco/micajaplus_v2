@echo off
chcp 65001 >nul
title Lanzador Backend - Mi Caja Plus
cd /d "%~dp0"
set PYTHONPATH=%~dp0

echo ========================================================
echo    ENCENDIENDO LOS 6 MICROSERVICIOS - MI CAJA PLUS
echo ========================================================

start "MS-01 Auth y AGW - 8000" cmd /k "set PYTHONPATH=%~dp0 && .venv\Scripts\python.exe ms-01-autenticacion-usuarios\servidor_agw.py"
timeout /t 2 >nul

start "MS-02 Clientes PHP - 8002" cmd /k ""C:\xampp001\php\php.exe" -S 127.0.0.1:8002 -t ms-02-clientes-php\public ms-02-clientes-php\public\index.php"
timeout /t 1 >nul

start "MS-03 Catalogo y PUC - 8003" cmd /k "set PYTHONPATH=%~dp0 && .venv\Scripts\python.exe ms-03-catalogo-cuentasPUC\main.py"
timeout /t 1 >nul

start "MS-04 Ventas - 8004" cmd /k "set PYTHONPATH=%~dp0 && .venv\Scripts\python.exe ms-04-ventas-facturacion\main.py"
timeout /t 1 >nul

start "MS-05 Reportes - 8005" cmd /k "set PYTHONPATH=%~dp0 && .venv\Scripts\python.exe ms-05-reportes-ventas\main.py"
timeout /t 1 >nul

start "MS-06 Gateway Movil - 8006" cmd /k "set PYTHONPATH=%~dp0 && .venv\Scripts\python.exe ms-06-gateway-movil\main.py"

echo.
echo ========================================================
echo    TODOS LOS SERVICIOS ENCENDIDOS
echo ========================================================
echo Tu IP local para configurar en RetrofitClient.kt:
ipconfig | findstr /i "IPv4"
echo.
pause