"""
Backtest data generator — multiplies ML training data for FREE using real
Binance historical candles (public API, no key required).

Why this instead of buying data: the model's features are specific to THIS
bot's signal stack (ensemble confidence, regime, stop distance, ...), so no
external dataset can be "outsourced" — but we can replay our own strategies
over months of real market history and label every signal with the outcome
its stop-loss / take-profit would have produced.

Output: backend/past data/backtest_<timeframe>_<date>.csv in the same schema
the trainer consumes. Just re-run the trainer afterwards.

Usage:
    python -m ml.backtest_generator --days 30
    python -m ml.backtest_generator --days 60 --symbols BTC/USDT,ETH/USDT --timeframe 5m
"""
import argparse
import logging
import os
import sys
import time
from datetime import datetime, timezone

import pandas as pd

logger = logging.getLogger(__name__)

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _BACKEND_DIR)

DATA_DIR = os.path.join(_BACKEND_DIR, "past data")

DEFAULT_SYMBOLS = ["BTC/USDT", "ETH/USDT", "BNB/USDT", "SOL/USDT", "DOGE/USDT", "XRP/USDT"]
WINDOW_CANDLES = 150          # lookback candles fed to each strategy
MAX_HOLD_CANDLES = 96         # exit horizon: 8h on 5m candles
NOTIONAL_PER_TRADE = 1000.0   # nominal $ size for PnL labeling
FEE_RATE = 0.001              # 0.1% per leg


def fetch_history(symbol: str, timeframe: str, days: int) -> pd.DataFrame:
    """Paginate through Binance public OHLCV history."""
    import ccxt  # sync client — this is an offline batch tool

    exchange = ccxt.binance({"enableRateLimit": True, "options": {"defaultType": "spot"}})
    since = exchange.milliseconds() - days * 24 * 60 * 60 * 1000
    all_rows: list = []
    while True:
        batch = exchange.fetch_ohlcv(symbol, timeframe=timeframe, since=since, limit=1000)
        if not batch:
            break
        all_rows.extend(batch)
        since = batch[-1][0] + 1
        if len(batch) < 1000:
            break
        time.sleep(exchange.rateLimit / 1000)
    frame = pd.DataFrame(all_rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], unit="ms")
    logger.info("%s: fetched %d candles (%s)", symbol, len(frame), timeframe)
    return frame


def _check_candle_exit(high: float, low: float, side: str, stop_loss: float, take_profit: float) -> tuple[bool, float, str]:
    """Helper to check if a single candle hits stop loss or take profit."""
    if side == "BUY":
        if low <= stop_loss:
            return True, stop_loss, "stop_loss"
        if high >= take_profit:
            return True, take_profit, "take_profit"
    else:
        if high >= stop_loss:
            return True, stop_loss, "stop_loss"
        if low <= take_profit:
            return True, take_profit, "take_profit"
    return False, 0.0, ""

def simulate_exit(frame: pd.DataFrame, entry_index: int, side: str,
                  stop_loss: float, take_profit: float) -> tuple[float, str]:
    """Walk candles forward until SL or TP is hit. Conservative: if both hit
    inside the same candle, assume the stop was hit first."""
    last_index = min(entry_index + MAX_HOLD_CANDLES, len(frame) - 1)
    for i in range(entry_index + 1, last_index + 1):
        high, low = frame["high"].iloc[i], frame["low"].iloc[i]
        exited, price, reason = _check_candle_exit(high, low, side, stop_loss, take_profit)
        if exited:
            return price, reason
    return float(frame["close"].iloc[last_index]), "time_exit"


def _process_signal(symbol: str, signal, ohlcv: pd.DataFrame, entry_index: int, 
                    window: pd.DataFrame, regime_detector) -> dict:
    """Helper to process a single generated signal into a backtest record."""
    entry_price = float(signal.price)
    exit_price, exit_reason = simulate_exit(
        ohlcv, entry_index, signal.signal_type,
        signal.suggested_stop_loss, signal.suggested_take_profit)
    quantity = NOTIONAL_PER_TRADE / entry_price
    
    if signal.signal_type == "BUY":
        pnl = (exit_price - entry_price) * quantity
    else:
        pnl = (entry_price - exit_price) * quantity
    pnl -= (entry_price + exit_price) * quantity * FEE_RATE
    
    try:
        regime = regime_detector.analyze(window).regime.value
    except Exception:
        regime = "ranging"
        
    stop_distance_pct = abs(entry_price - signal.suggested_stop_loss) / entry_price * 100
    risk_reward = (abs(signal.suggested_take_profit - entry_price)
                   / max(abs(entry_price - signal.suggested_stop_loss), 1e-10))
                   
    return {
        "symbol": symbol,
        "side": signal.signal_type,
        "strategy": signal.strategy_name,
        "entry_price": entry_price,
        "exit_price": round(exit_price, 8),
        "quantity": round(quantity, 8),
        "profit_loss": round(pnl, 4),
        "exit_reason": exit_reason,
        "opened_at": str(ohlcv["timestamp"].iloc[entry_index]),
        "regime": regime,
        "ensemble_confidence": signal.strength,
        "regime_boost": 1.0,
        "agreeing_strategies_count": 1,
        "disagreeing_strategies_count": 0,
        "sentiment_value": 50,
        "sentiment_bias": "BOTH",
        "funding_rate": 0.0,
        "funding_bias": "NEUTRAL",
        "kelly_fraction": 0.02,
        "stop_distance_pct": round(stop_distance_pct, 4),
        "risk_reward_ratio": round(risk_reward, 3),
        "portfolio_drawdown_at_entry": 0.0,
        "open_positions_at_entry": 0,
        "hft_mode": False,
    }


def generate_for_symbol(symbol: str, ohlcv: pd.DataFrame, strategies: dict,
                        regime_detector) -> list[dict]:
    rows: list[dict] = []
    for entry_index in range(WINDOW_CANDLES, len(ohlcv) - 1):
        window = ohlcv.iloc[entry_index - WINDOW_CANDLES:entry_index + 1].reset_index(drop=True)
        for strategy in strategies.values():
            try:
                signal = strategy.compute_signal(symbol, window)
            except Exception:
                continue
            if signal is None:
                continue
            row = _process_signal(symbol, signal, ohlcv, entry_index, window, regime_detector)
            rows.append(row)
    return rows


def main():
    parser = argparse.ArgumentParser(description="Generate ML training data from real Binance history")
    parser.add_argument("--days", type=int, default=30, help="Days of history to replay")
    parser.add_argument("--timeframe", default="5m", help="Candle timeframe (default 5m)")
    parser.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS),
                        help="Comma-separated symbol list")
    args = parser.parse_args()

    from engine.trading_engine import STRATEGY_REGISTRY
    from engine.regime_detector import MarketRegimeDetector

    strategies = {k: v for k, v in STRATEGY_REGISTRY.items() if k != "pairs"}
    regime_detector = MarketRegimeDetector()
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]

    all_rows: list[dict] = []
    for symbol in symbols:
        try:
            ohlcv = fetch_history(symbol, args.timeframe, args.days)
        except Exception as exc:
            logger.exception("%s: history fetch failed: %s", symbol, exc)
            continue
        if len(ohlcv) <= WINDOW_CANDLES + 1:
            continue
        rows = generate_for_symbol(symbol, ohlcv, strategies, regime_detector)
        logger.info("%s: generated %d labeled trades", symbol, len(rows))
        all_rows.extend(rows)

    if not all_rows:
        logger.error("No trades generated — nothing to save")
        return

    os.makedirs(DATA_DIR, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
    output_path = os.path.join(DATA_DIR, f"backtest_{args.timeframe}_{stamp}.csv")
    frame = pd.DataFrame(all_rows)
    frame.to_csv(output_path, index=False)
    win_rate = (frame["profit_loss"] > 0).mean() * 100
    logger.info("Saved %d trades to %s (win rate %.1f%%, total PnL $%.2f)",
                len(frame), output_path, win_rate, frame["profit_loss"].sum())
    logger.info("Now retrain the model:  python -m ml.trainer")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    main()
