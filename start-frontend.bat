@echo off
echo Starting CryptoBot Pro Frontend...
cd /d "%~dp0frontend"

if not exist "node_modules" (
    echo Installing npm dependencies...
    npm install
)

if not exist ".env" (
    echo Creating .env from example...
    copy .env.example .env
)

echo.
echo Frontend starting on http://localhost:5173
echo Press Ctrl+C to stop.
echo.
npm run dev
