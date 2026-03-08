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
│   ├── models/                     # Database models & schemas
│   ├── requirements.txt
│   └── .env.example                # Copy to .env and configure
├── frontend/                       # React + TailwindCSS dashboard
│   ├── src/
│   │   ├── pages/                  # Dashboard, Strategies, Portfolio, Trades, Analytics, Settings
│   │   └── hooks/                  # WebSocket hook
│   └── package.json
├── start-backend.bat               # Windows one-click backend launcher
└── start-frontend.bat              # Windows one-click frontend launcher
```

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

# Start the backend (runs on http://localhost:8000)
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

> API docs (Swagger UI) available at **http://localhost:8000/docs**

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
