@echo off
echo Starting CryptoBot Pro Backend...
cd /d "%~dp0backend"

if not exist "venv" (
    echo Creating virtual environment...
    python -m venv venv
)

call venv\Scripts\activate

echo Installing dependencies...
pip install -r requirements.txt --quiet

if not exist ".env" (
    echo Creating .env from example...
    copy .env.example .env
)

echo.
echo Backend starting on http://127.0.0.1:8000
echo NOTE: API docs are disabled by default. Set ENABLE_DOCS=true in .env to enable.
echo NOTE: Set ADMIN_USERNAME, ADMIN_PASSWORD_HASH, and JWT_SECRET in .env to enable authentication.
echo Press Ctrl+C to stop.
echo.
python main.py
