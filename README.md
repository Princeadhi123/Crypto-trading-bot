# CryptoBot Pro — Automated Crypto Trading Bot

A full end-to-end automated cryptocurrency trading bot with a modern React dashboard.

> ⚠️ **RISK DISCLAIMER**: Cryptocurrency trading involves significant financial risk. This bot operates in **Paper Trading mode by default** (no real money). Past performance does not guarantee future results. Never trade more than you can afford to lose.

---

## Features

- **4 Trading Strategies**: RSI Mean Reversion, MACD Momentum, Bollinger Bands, EMA Scalping
- **Automatic Trade Execution**: Signals → Risk Check → Execute → Monitor → Close
- **Full Risk Management**: Per-trade risk sizing, max drawdown circuit breaker, stop-loss/take-profit
- **Paper Trading Mode**: Test safely with virtual $10,000 before going live
- **Real-time Dashboard**: Live prices, open positions, P&L charts via WebSocket
- **100+ Exchange Support**: Binance, Coinbase, Kraken, OKX, Bybit and more (via CCXT)
- **Trade History**: Full paginated log with filtering
- **Strategy Analytics**: Per-strategy win rate, P&L, trade distribution

---

## Project Structure

```
├── backend/               # Python FastAPI trading engine
│   ├── main.py            # FastAPI app + WebSocket server
│   ├── engine/
│   │   ├── trading_engine.py    # Core trading loop
│   │   ├── risk_manager.py      # Position sizing & risk rules
│   │   └── strategies/
│   │       ├── rsi_strategy.py
│   │       ├── macd_strategy.py
│   │       ├── bollinger_strategy.py
│   │       └── scalping_strategy.py
│   ├── api/routes.py      # REST API endpoints
│   ├── models/            # Database models & schemas
│   ├── requirements.txt
│   └── .env.example       # Copy to .env and configure
└── frontend/              # React + TailwindCSS dashboard
    ├── src/
    │   ├── pages/         # Dashboard, Strategies, Portfolio, Trades, Settings
    │   └── hooks/         # WebSocket hook
    └── package.json
```

---

## Quick Start

### 1. Backend Setup

```bash
cd backend

# Create virtual environment
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # macOS/Linux

# Install dependencies
pip install -r requirements.txt

# Configure environment
copy .env.example .env       # Windows
# cp .env.example .env       # macOS/Linux

# Start the backend (runs on http://localhost:8000)
python main.py
```

### 2. Frontend Setup

```bash
cd frontend

# Install dependencies
npm install

# Start the dev server (runs on http://localhost:5173)
npm run dev
```

### 3. Open the Dashboard

Navigate to **http://localhost:5173** in your browser.

---

## Configuration

### Paper Trading (Default — Safe)
The bot starts in paper trading mode with a virtual **$10,000** balance. Configure in `.env`:
```env
PAPER_TRADING=true
PAPER_BALANCE=10000.0
```

### Live Trading (Real Money)
**Only enable after thorough paper trading testing.**

1. Get API keys from your exchange (Binance, Coinbase, etc.)
2. Edit `.env`:
```env
PAPER_TRADING=false
EXCHANGE_NAME=binance
API_KEY=your_api_key_here
API_SECRET=your_api_secret_here
```
3. Restart the backend
4. Switch to Live mode in the Settings page

### Risk Settings (`.env`)
```env
MAX_PORTFOLIO_RISK_PERCENT=2.0    # Max % of portfolio risked per trade
MAX_DRAWDOWN_PERCENT=10.0         # Bot stops if drawdown exceeds this
DEFAULT_STOP_LOSS_PERCENT=2.0     # Default stop loss
DEFAULT_TAKE_PROFIT_PERCENT=4.0   # Default take profit
MAX_CONCURRENT_POSITIONS=5        # Max open trades at once
```

---

## Strategies

| Strategy | Type | Best Market | Stop Loss | Take Profit |
|---|---|---|---|---|
| RSI Mean Reversion | Mean Reversion | Ranging | 2.0% | 4.0% |
| MACD Momentum | Momentum | Trending | 2.5% | 5.0% |
| Bollinger Bands | Volatility | Both | 1.5% | 3.0% |
| EMA Scalping | Scalping | Both | ATR×1.5 | ATR×2.5 |

---

## Risk Management

The bot enforces multiple layers of protection:
1. **Signal Strength Filter** — Only trades signals above minimum confidence threshold
2. **Risk-Based Position Sizing** — Position size calculated so max loss = X% of portfolio
3. **Max Concurrent Positions** — Never opens more than N trades simultaneously
4. **Duplicate Symbol Prevention** — Never opens two positions on the same asset
5. **Max Drawdown Circuit Breaker** — Stops all trading if portfolio drops by X%
6. **Stop Loss** — Every trade has an automatic stop loss
7. **Take Profit** — Every trade has an automatic profit target

---

## API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/status` | Bot status |
| POST | `/api/bot/start` | Start the bot |
| POST | `/api/bot/stop` | Stop the bot |
| GET | `/api/portfolio` | Portfolio stats |
| GET | `/api/positions` | Active positions |
| GET | `/api/trades` | Trade history |
| GET | `/api/market/prices` | Live prices |
| GET | `/api/signals/recent` | Recent signals |
| GET/PUT | `/api/settings` | Bot settings |
| WS | `/ws` | Real-time events |
