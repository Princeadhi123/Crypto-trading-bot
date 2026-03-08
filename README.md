# CryptoBot Pro — Automated Crypto Trading Bot

A full end-to-end automated cryptocurrency trading bot with a modern React dashboard, institutional-grade signal logic, and advanced risk management.

> ⚠️ **RISK DISCLAIMER**: Cryptocurrency trading involves significant financial risk. This bot operates in **Paper Trading mode by default** (no real money). Past performance does not guarantee future results. Never trade more than you can afford to lose.

---

## Features

- **5 Trading Strategies**: RSI Mean Reversion, MACD Momentum, Bollinger Bands, EMA Scalping, Statistical Arbitrage (Pairs Trading)
- **Signal Ensemble**: Multi-strategy majority-vote aggregation — only fires when independent strategies agree
- **Market Regime Detection**: ADX + ATR-based classifier routes capital to the correct strategy type for current conditions (Trending / Ranging / High-Vol / Low-Vol)
- **Sentiment Filter**: Crypto Fear & Greed Index overlay — blocks BUY signals during Extreme Greed and SELL signals during Extreme Fear
- **Funding Rate Signal**: Perpetual futures crowding detector — blocks trades fighting extreme funding and boosts aligned ones
- **TWAP Execution**: Splits large orders into time-sliced tranches to minimize market impact
- **VaR / CVaR Risk Reporting**: Historical simulation Value at Risk (95% & 99%), Expected Shortfall, Sharpe & Sortino ratios
- **Strategy Performance Tracker**: Rolling Sharpe, Kelly Criterion position sizing, and dynamic capital weight allocation per strategy
- **Automatic Trade Execution**: Signals → Ensemble → Sentiment/Funding filters → Risk Check → TWAP Execute → Monitor → Close
- **Full Risk Management**: Per-trade risk sizing, max drawdown circuit breaker, stop-loss/take-profit
- **Paper Trading Mode**: Test safely with virtual $10,000 before going live
- **Real-time Dashboard**: Live prices, open positions, P&L charts via WebSocket
- **100+ Exchange Support**: Binance, Coinbase, Kraken, OKX, Bybit and more (via CCXT)
- **Trade History**: Full paginated log with filtering
- **Analytics Page**: Per-strategy win rate, P&L, rolling Sharpe, Kelly fraction, dynamic weights
- **JWT Authentication**: Username/password login with signed Bearer tokens for API and WebSocket access
- **Field Encryption**: Optional Fernet encryption for sensitive SQLite fields such as `notes` and `signal_features`
- **HTTPS Support**: Optional direct Uvicorn TLS for local testing and Nginx reverse proxy support for Linux deployment

---

## Project Structure

```
├── backend/                        # Python FastAPI trading engine
│   ├── main.py                     # FastAPI app + WebSocket server
│   ├── engine/
│   │   ├── trading_engine.py       # Core trading loop
│   │   ├── risk_manager.py         # Position sizing & risk rules
│   │   ├── signal_ensemble.py      # Multi-strategy consensus aggregator
│   │   ├── regime_detector.py      # ADX/ATR market regime classifier
│   │   ├── sentiment_filter.py     # Fear & Greed Index macro filter
│   │   ├── funding_rate_signal.py  # Perpetual futures funding rate signal
│   │   ├── twap_executor.py        # TWAP order execution algorithm
│   │   ├── var_calculator.py       # VaR / CVaR / Sharpe / Sortino
│   │   ├── strategy_performance_tracker.py  # Rolling Sharpe + Kelly + dynamic weights
│   │   └── strategies/
│   │       ├── base_strategy.py
│   │       ├── rsi_strategy.py
│   │       ├── macd_strategy.py
│   │       ├── bollinger_strategy.py
│   │       ├── scalping_strategy.py
│   │       └── pairs_strategy.py   # Statistical Arbitrage (BTC/ETH spread)
│   ├── api/routes.py               # REST API endpoints
│   ├── api/auth.py                 # JWT auth, validation, rate limiting
│   ├── api/auth_routes.py          # Login + auth status endpoints
│   ├── models/                     # Database models & schemas
│   ├── utils/encryption.py         # Fernet helpers for encrypted DB fields
│   ├── requirements.txt
│   └── .env.example                # Copy to .env and configure
├── frontend/                       # React + TailwindCSS dashboard
│   ├── src/
│   │   ├── pages/                  # Login, Dashboard, Strategies, Portfolio, Trades, Analytics, Settings
│   │   └── hooks/                  # WebSocket hook
│   └── package.json
├── generate-password-hash.py       # Generate bcrypt hashes for admin password
├── generate-certs.py               # Generate self-signed local TLS certificates
├── nginx.conf                      # Example Linux reverse-proxy config
├── start-backend.bat               # Windows one-click backend launcher
└── start-frontend.bat              # Windows one-click frontend launcher
```

---

## Security Model

- **Frontend uses no `.env` file**. The frontend is JWT-only and stores the login token in `sessionStorage`.
- **Backend uses `.env`** for exchange keys, JWT auth, encryption keys, TLS paths, and runtime config.
- **All `/api/*` routes require a valid Bearer token** when `ADMIN_PASSWORD_HASH` is configured.
- **WebSocket `/ws` also requires a valid JWT** when login is enabled.
- **If `ADMIN_PASSWORD_HASH` is empty**, authentication is disabled for local development only.

---

## Quick Start

### Windows (One-Click)

Double-click `start-backend.bat` in one terminal and `start-frontend.bat` in another. The backend script handles venv creation, dependency installation, and backend `.env` setup automatically. The frontend script installs dependencies and starts the Vite dev server.

### Manual Setup

#### 1. Backend

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

# Optional: generate a bcrypt password hash for ADMIN_PASSWORD_HASH
python ..\generate-password-hash.py   # Windows
# python3 ../generate-password-hash.py # macOS/Linux

# Optional: generate local self-signed certs for HTTPS dev
python ..\generate-certs.py           # Windows
# python3 ../generate-certs.py         # macOS/Linux

# Start the backend (HTTP or HTTPS depending on SSL_CERTFILE / SSL_KEYFILE)
python main.py
```

#### 2. Frontend

```bash
cd frontend

# Install dependencies
npm install

# Start the dev server (runs on http://localhost:5173)
npm run dev
```

#### 3. Open the Dashboard

Navigate to **http://localhost:5173** in your browser.

If `ADMIN_PASSWORD_HASH` is configured, the app will show the **login page first**. After successful login, the frontend stores a JWT in `sessionStorage` and uses it for all API and WebSocket requests.

> API docs (Swagger UI) are available only when `ENABLE_DOCS=true`.

---

## Configuration

### Frontend Configuration

The frontend currently uses **no environment variables**. You do **not** need `frontend/.env` or `frontend/.env.example`.

### Backend Authentication

Configure JWT login in `backend/.env`:

```env
ADMIN_USERNAME=admin
ADMIN_PASSWORD_HASH=<bcrypt hash from generate-password-hash.py>
JWT_SECRET=<random 64-char hex secret>
```

Notes:

- `ADMIN_PASSWORD_HASH` enables authentication.
- `JWT_SECRET` should be stable and private.
- Changing `JWT_SECRET` invalidates all existing sessions.

### Local HTTPS

For local HTTPS testing, generate self-signed certificates and set:

```env
SSL_CERTFILE=certs/server.crt
SSL_KEYFILE=certs/server.key
```

Leave both empty to run plain HTTP locally.

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

## Linux Deployment

Recommended production architecture:

- **FastAPI/Uvicorn bound to `127.0.0.1:8000`**
- **Nginx in front** for HTTPS termination and reverse proxying
- **Real TLS certificates** from Let's Encrypt
- **Frontend built and served by Nginx** or proxied appropriately

### Backend `.env` on Linux

Use a server-specific `backend/.env`. Do not blindly copy local secrets.

- **Generate a new `JWT_SECRET`** for the server
- **Reuse `ADMIN_PASSWORD_HASH`** only if you want the same admin password
- **Reuse `FIELD_ENCRYPTION_KEY`** only when migrating the same encrypted database
- **Leave `SSL_CERTFILE` / `SSL_KEYFILE` empty** when Nginx handles TLS

Example production-oriented values:

```env
HOST=127.0.0.1
PORT=8000
SSL_CERTFILE=
SSL_KEYFILE=
ENABLE_DOCS=false
RELOAD=false
```

### Suggested Linux setup steps

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip nginx

cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python3 main.py
```

Then configure Nginx using `nginx.conf` as a template, update the domain / certificate paths, and obtain a real certificate with Certbot.

### Production auth troubleshooting

If the deployed frontend gets repeated `403 Forbidden` responses from `/api/*` routes:

- confirm the frontend build is up to date
- confirm the site shows the login page first
- sign in again so a fresh JWT is stored in `sessionStorage`
- if you changed `JWT_SECRET`, all previous tokens become invalid and users must log in again

---

## Strategies

| Strategy | Type | Best Market | Stop Loss | Take Profit |
|---|---|---|---|---|
| RSI Mean Reversion | Mean Reversion | Ranging | 2.0% | 4.0% |
| MACD Momentum | Momentum | Trending | 2.5% | 5.0% |
| Bollinger Bands | Volatility | Both | 1.5% | 3.0% |
| EMA Scalping | Scalping | Both | ATR×1.5 | ATR×2.5 |
| Statistical Arbitrage | Pairs / Market-Neutral | Both | 3.0% | 2.5% |

### Signal Ensemble
No single strategy trades alone. The `SignalEnsemble` requires at minimum 2 strategies to agree before a trade is placed. It applies:
- **Majority voting** with configurable quorum
- **Weighted confidence** based on each strategy's recent Sharpe ratio
- **Regime alignment boost** — signals matching the current market regime receive up to +30% confidence
- **Conflict cancellation** — conflicting buy/sell signals with balanced weight cancel each other

### Market Regime Detection
The `MarketRegimeDetector` classifies conditions using ADX and ATR volatility percentile:

| Regime | Condition | Preferred Strategies |
|---|---|---|
| Trending Up/Down | ADX ≥ 25 | MACD, EMA Scalping |
| Ranging | ADX < 25, normal vol | RSI, Bollinger Bands |
| High Volatility | ATR percentile ≥ 80 | Bollinger Bands only (reduced size) |
| Low Volatility | ATR percentile ≤ 20 | EMA Scalping, RSI |

---

## Risk Management

The bot enforces multiple layers of protection:
1. **Signal Strength Filter** — Only trades signals above minimum confidence threshold
2. **Sentiment Macro Filter** — Blocks trade directions conflicting with Fear & Greed Index
3. **Funding Rate Filter** — Blocks trades opposing extreme perpetual funding rates
4. **Risk-Based Position Sizing** — Position size calculated so max loss = X% of portfolio
5. **Kelly Criterion Sizing** — Half-Kelly fraction per strategy based on recent win rate and avg win/loss
6. **Max Concurrent Positions** — Never opens more than N trades simultaneously
7. **Duplicate Symbol Prevention** — Never opens two positions on the same asset
8. **Max Drawdown Circuit Breaker** — Stops all trading if portfolio drops by X%
9. **VaR Budget Enforcement** — Tracks daily Value at Risk; reduces activity when budget is consumed
10. **Stop Loss** — Every trade has an automatic stop loss
11. **Take Profit** — Every trade has an automatic profit target
12. **TWAP Execution** — Large orders split into 5 slices over ~1 minute to minimize market impact

---

## Recent Bug Fixes & Improvements

### Session: Feb 24, 2026

**Critical Production Bugs Fixed:**

1. **DB Commit Crash After Live Order Orphans Trade**
   - Fixed: ActivePosition now tracked in memory before DB commit
   - Impact: Prevents orphaned live trades if database write fails

2. **Pairs Naked Exposure Pre-validation**
   - Fixed: Pre-validates sizing for both legs before executing any API calls
   - Impact: Prevents naked hedge exposure if primary leg fails minimum notional

3. **Partial TWAP Fill Skips DB Update**
   - Fixed: Persists partial fill reduced quantity to DB before returning
   - Impact: Prevents PnL desync and position amnesia on restart

4. **Trailing Stop Lost on Restart**
   - Fixed: Batches trailing stop loss updates to DB after exit condition checks
   - Impact: Preserves trailing stops across bot restarts

5. **Total Realized PnL Wiped on Restart**
   - Fixed: Aggregates today's closed PnL from DB on startup
   - Impact: Dashboard realized PnL persists across sessions

6. **Price Jump on Bot Start (Simulated OHLCV Overwriting Live Prices)**
   - Fixed: Don't overwrite `market_prices` with simulated OHLCV close in paper mode
   - Impact: Paper trading now tracks real market prices from background refresh loop

7. **Stop-Loss Death Spiral (Paper Entry Price Mismatch)**
   - Fixed: Snap paper entry price to live market price before sizing
   - Impact: Prevents immediate stop-loss hits due to simulated vs live price discrepancy

8. **DataFrame Ambiguity Crash After Hot-Reload**
   - Fixed: Replace DataFrame `or` operator with explicit `is not None` checks
   - Impact: Prevents "truth value of DataFrame is ambiguous" crash in pairs trading

9. **Pairs Pre-validation Abort Log Spam**
   - Fixed: Downgraded log level from INFO to DEBUG
   - Impact: Reduces noise when portfolio is in drawdown and risk manager blocks trades

**All fixes committed and tested in production.**

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
| GET | `/api/strategies` | List strategies + enabled state |
| PATCH | `/api/strategies/{id}/toggle` | Enable/disable a strategy |
| GET/PUT | `/api/settings` | Bot settings |
| GET | `/api/analytics/pnl-chart` | Cumulative P&L chart data |
| GET | `/api/analytics/strategy-performance` | Per-strategy win rate & P&L |
| GET | `/api/analytics/live-performance` | Rolling Sharpe, Kelly, dynamic weights |
| GET | `/api/analytics/regime` | Current market regime per symbol |
| GET | `/api/analytics/var` | VaR 95/99, CVaR, Sharpe, Sortino |
| GET | `/api/analytics/sentiment` | Fear & Greed Index reading |
| GET | `/api/analytics/funding-rates` | Perpetual funding rates per symbol |
| GET | `/api/analytics/ml-training-data` | Export closed trades as ML dataset |
| WS | `/ws` | Real-time events |
