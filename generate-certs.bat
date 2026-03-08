@echo off
echo Generating self-signed TLS certificate for HTTPS...
cd /d "%~dp0backend"
python ..\generate-certs.py
echo.
echo After this, set in backend\.env:
echo   SSL_CERTFILE=certs/server.crt
echo   SSL_KEYFILE=certs/server.key
echo.
pause
