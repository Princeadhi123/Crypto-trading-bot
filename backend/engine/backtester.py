"""
Institutional-grade backtesting framework.

Simulates the full portfolio lifecycle over real Binance history:
- Per-strategy solo runs (isolate each alpha source)
- Ensemble mode (multi-strategy voting, same as live engine)
- Adaptive mode (regime-routed weights — strategy allocation changes with
  market conditions, exactly like the live engine's regime detector)

Realism controls: taker fees, slippage, risk-based position sizing,
max concurrent positions, conservative same-candle SL/TP resolution.

Outputs: equity curve, trade list, and a full metrics report
(Sharpe, Sortino, max drawdown, profit factor, exposure, regime breakdown).
"""
import asyncio
import logging
import math
import multiprocessing
import time
import uuid
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

import pandas as pd

from engine.regime_detector import MarketRegimeDetector
from engine.signal_ensemble import SignalEnsemble
from engine.sentiment_filter import classify_value, is_direction_allowed, SentimentFilter, SentimentReading
from engine.risk_manager import RiskManager, PositionSizeResult
from engine.strategy_performance_tracker import StrategyPerformanceTracker
from engine.ml_filter import MLSignalFilter
from engine.funding_rate_signal import (
    FundingRateSignal, FundingRateReading, PERPETUAL_SYMBOL_MAP, SIMULATED_FUNDING_RATES,
)

logger = logging.getLogger(__name__)

WINDOW_CANDLES = 150          # lookback fed to strategies each step
CANDLES_PER_YEAR = {"1m": 525600, "5m": 105120, "15m": 35040, "1h": 8760, "4h": 2190, "1d": 365}
PAIRS_PRIMARY_SYMBOL = "BTC/USDT"
PAIRS_HEDGE_SYMBOL = "ETH/USDT"


@dataclass
class BacktestConfig:
    symbols: list[str]
    days: int = 30
    timeframe: str = "5m"
    strategies: list[str] = field(default_factory=lambda: ["rsi", "macd", "bollinger", "scalping"])
    mode: str = "adaptive"            # "solo" | "ensemble" | "adaptive"
    initial_balance: float = 10000.0
    risk_per_trade_pct: float = 1.0   # % of equity risked per trade
    fee_rate: float = 0.001           # 0.1% taker per leg
    slippage_bps: float = 5.0         # 5 basis points
    max_positions: int = 5
    max_hold_candles: int = 96        # time-based exit horizon
    min_confidence: float = 0.45      # ensemble confidence floor


@dataclass
class SimPosition:
    symbol: str
    side: str
    entry_price: float
    quantity: float
    stop_loss: float
    take_profit: float
    entry_index: int
    strategy: str
    regime: str


@dataclass
class SignalResult:
    direction: str
    entry: float
    stop: float
    tp: float
    confidence: float       # final confidence, post regime boost
    label: str
    regime: str
    regime_boost: float
    agreeing_count: int
    disagreeing_count: int


class Backtester:
    """Event-driven candle-by-candle portfolio simulator."""

    def __init__(self, strategy_registry: dict):
        self._registry = strategy_registry
        self.regime_detector = MarketRegimeDetector()

    # ── Data ────────────────────────────────────────────────────────────
    @staticmethod
    def fetch_history_sync(symbol: str, timeframe: str, days: int) -> pd.DataFrame:
        import ccxt
        exchange = ccxt.binance({"enableRateLimit": True, "options": {"defaultType": "spot"}})
        since = exchange.milliseconds() - days * 24 * 60 * 60 * 1000
        rows: list = []
        while True:
            batch = exchange.fetch_ohlcv(symbol, timeframe=timeframe, since=since, limit=1000)
            if not batch:
                break
            rows.extend(batch)
            since = batch[-1][0] + 1
            if len(batch) < 1000:
                break
            time.sleep(exchange.rateLimit / 1000)
        frame = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], unit="ms")
        return frame

    # ── Data: historical Fear & Greed Index (mirrors live sentiment filter) ──
    @staticmethod
    def fetch_sentiment_history_sync(days: int) -> dict[str, int]:
        """Fetches the daily Fear & Greed Index history so the backtest can
        apply the exact same macro filter the live engine uses."""
        import httpx
        try:
            limit = max(days + 5, 10)
            resp = httpx.get(f"https://api.alternative.me/fng/?limit={limit}&format=json", timeout=10.0)
            entries = resp.json().get("data", [])
            result: dict[str, int] = {}
            for entry in entries:
                ts = datetime.fromtimestamp(int(entry["timestamp"]), tz=timezone.utc)
                result[ts.strftime("%Y-%m-%d")] = int(entry["value"])
            return result
        except Exception as exc:
            logger.warning("Could not fetch Fear & Greed history for backtest: %s", exc)
            return {}

    # ── Data: historical funding rates (mirrors live funding-rate filter) ───
    @staticmethod
    def fetch_funding_history_sync(symbols: list[str], days: int) -> dict[str, dict[str, float]]:
        """Fetches daily perpetual funding-rate history per symbol. Falls back to
        the same constant simulated rate the live engine uses when unavailable,
        so the filter always applies exactly like production (fail-open parity)."""
        import ccxt
        result: dict[str, dict[str, float]] = {}
        exchange = None
        try:
            exchange = ccxt.binance({"options": {"defaultType": "swap"}, "enableRateLimit": True})
        except Exception as exc:
            logger.warning("Could not init futures exchange for funding history: %s", exc)

        since = exchange.milliseconds() - days * 24 * 60 * 60 * 1000 if exchange else None
        for sym in symbols:
            perp_symbol = PERPETUAL_SYMBOL_MAP.get(sym)
            history: dict[str, float] = {}
            if exchange is not None and perp_symbol:
                try:
                    rows = exchange.fetch_funding_rate_history(perp_symbol, since=since, limit=1000)
                    for row in rows:
                        ts = datetime.fromtimestamp(row["timestamp"] / 1000, tz=timezone.utc)
                        history[ts.strftime("%Y-%m-%d")] = float(row["fundingRate"])
                except Exception as exc:
                    logger.debug("Funding history fetch failed for %s: %s", sym, exc)
            if not history:
                fallback_rate = SIMULATED_FUNDING_RATES.get(sym)
                if fallback_rate is not None:
                    history = {"__constant__": fallback_rate}
            result[sym] = history
        return result

    # ── Signal generation per mode ──────────────────────────────────────
    def _signal_for_window(self, symbol: str, data: dict[str, pd.DataFrame], i: int, config: BacktestConfig,
                           strategy_names: list[str], ensemble: Optional[SignalEnsemble],
                           performance_tracker: StrategyPerformanceTracker) -> Optional[SignalResult]:
        window = data[symbol].iloc[i - WINDOW_CANDLES:i + 1].reset_index(drop=True)
        regime_analysis = self.regime_detector.analyze(window)
        raw_signals = []
        for name in strategy_names:
            strategy = self._registry.get(name)
            if not strategy:
                continue
            try:
                sig = strategy.compute_signal(symbol, window)
            except Exception:
                continue
            if sig:
                raw_signals.append(sig)
        if not raw_signals:
            return None

        if config.mode == "solo":
            sig = max(raw_signals, key=lambda s: s.strength)
            return SignalResult(
                direction=sig.signal_type, entry=sig.price, stop=sig.suggested_stop_loss,
                tp=sig.suggested_take_profit, confidence=sig.strength, label=sig.strategy_name,
                regime=regime_analysis.regime.value, regime_boost=1.0, agreeing_count=1, disagreeing_count=0,
            )

        if config.mode == "adaptive":
            # Same blend the live engine uses: regime-suitability weights combined
            # with each strategy's rolling live performance (Sharpe-derived).
            regime_weights = self.regime_detector.get_strategy_weights(regime_analysis)
            weights = performance_tracker.get_combined_weights(regime_weights)
        else:  # plain ensemble: flat weights (backtest-only comparison mode)
            weights = dict.fromkeys(strategy_names, 1.0)

        es = ensemble.aggregate(symbol=symbol, raw_signals=raw_signals,
                                strategy_weights=weights, regime_analysis=regime_analysis)
        if es is None or es.final_confidence < config.min_confidence:
            return None
        label = " + ".join(es.agreeing_strategies)
        return SignalResult(
            direction=es.direction, entry=es.weighted_entry_price, stop=es.suggested_stop_loss,
            tp=es.suggested_take_profit, confidence=es.final_confidence, label=label,
            regime=regime_analysis.regime.value, regime_boost=es.regime_boost,
            agreeing_count=len(es.agreeing_strategies), disagreeing_count=len(es.disagreeing_strategies),
        )

    # ── Exit check ──────────────────────────────────────────────────────
    @staticmethod
    def _candle_exit(position: SimPosition, high: float, low: float) -> Optional[tuple[float, str]]:
        # Conservative: stop always assumed to trigger before target within same candle
        if position.side == "BUY":
            if low <= position.stop_loss:
                return position.stop_loss, "stop_loss"
            if high >= position.take_profit:
                return position.take_profit, "take_profit"
        else:
            if high >= position.stop_loss:
                return position.stop_loss, "stop_loss"
            if low <= position.take_profit:
                return position.take_profit, "take_profit"
        return None

    # ── Core simulation ─────────────────────────────────────────────────
    def run(self, config: BacktestConfig, data: dict[str, pd.DataFrame],
            strategy_names: list[str], progress_callback=None,
            sentiment_history: Optional[dict[str, int]] = None,
            risk_manager: Optional[RiskManager] = None,
            ml_filter: Optional[MLSignalFilter] = None,
            funding_history: Optional[dict[str, dict[str, float]]] = None) -> dict:
        ensemble = SignalEnsemble(
            minimum_agreement_count=1 if config.mode == "solo" else 2,
            minimum_composite_confidence=config.min_confidence,
        )
        risk_manager = risk_manager or RiskManager()
        performance_tracker = StrategyPerformanceTracker(rolling_window=30)
        sentiment_helper = SentimentFilter()
        funding_helper = FundingRateSignal()
        balance = config.initial_balance
        equity_curve: list[dict] = []
        trades: list[dict] = []
        open_positions: dict[str, SimPosition] = {}
        slip = config.slippage_bps / 10000.0

        effective_names = [n for n in strategy_names if n != "pairs"]
        pairs_strategy = self._registry.get("pairs") if "pairs" in strategy_names else None
        pairs_enabled = (
            pairs_strategy is not None
            and PAIRS_PRIMARY_SYMBOL in data
            and PAIRS_HEDGE_SYMBOL in data
        )

        # Align all symbols to shortest history
        min_len = min(len(df) for df in data.values())
        total_steps = max(min_len - WINDOW_CANDLES - 1, 1)

        def _portfolio_value(i: int) -> float:
            unrealized = 0.0
            for sym, pos in open_positions.items():
                price = float(data[sym]["close"].iloc[i])
                if pos.side == "BUY":
                    unrealized += (price - pos.entry_price) * pos.quantity
                else:
                    unrealized += (pos.entry_price - price) * pos.quantity
            return balance + unrealized

        def _close(position: SimPosition, exit_price: float, reason: str, index: int, sym: str):
            nonlocal balance
            exit_fill = exit_price * (1 - slip) if position.side == "BUY" else exit_price * (1 + slip)
            if position.side == "BUY":
                pnl = (exit_fill - position.entry_price) * position.quantity
            else:
                pnl = (position.entry_price - exit_fill) * position.quantity
            fees = (position.entry_price + exit_fill) * position.quantity * config.fee_rate
            pnl -= fees
            balance += pnl
            cost = position.entry_price * position.quantity
            pnl_percent = (pnl / cost * 100) if cost > 0 else 0.0
            for name in position.strategy.split(" + "):
                performance_tracker.record_trade_outcome(name.strip(), round(pnl_percent, 4))
            trades.append({
                "symbol": sym, "side": position.side, "strategy": position.strategy,
                "regime": position.regime,
                "entry_price": round(position.entry_price, 8), "exit_price": round(exit_fill, 8),
                "quantity": round(position.quantity, 8), "pnl": round(pnl, 4),
                "fees": round(fees, 4), "exit_reason": reason,
                "held_candles": index - position.entry_index,
                "opened_at": str(data[sym]["timestamp"].iloc[position.entry_index]),
                "closed_at": str(data[sym]["timestamp"].iloc[index]),
            })

        def _sentiment_at(sym: str, i: int) -> tuple[Optional[int], str]:
            if not sentiment_history:
                return None, "BOTH"
            date_str = pd.Timestamp(data[sym]["timestamp"].iloc[i]).strftime("%Y-%m-%d")
            value = sentiment_history.get(date_str)
            if value is None:
                return None, "BOTH"
            _, bias = classify_value(value)
            return value, bias

        def _funding_at(sym: str, i: int) -> Optional[tuple[float, str, float]]:
            hist = funding_history.get(sym) if funding_history else None
            if not hist:
                return None
            if "__constant__" in hist:
                rate = hist["__constant__"]
            else:
                date_str = pd.Timestamp(data[sym]["timestamp"].iloc[i]).strftime("%Y-%m-%d")
                rate = hist.get(date_str)
                if rate is None:
                    return None
            bias, strength = funding_helper._interpret_funding(rate)
            return rate, bias, strength

        def _try_open(sym: str, direction: str, entry: float, stop: float, tp: float,
                      confidence: float, label: str, regime: str, regime_boost: float,
                      agreeing_count: int, disagreeing_count: int, i: int,
                      forced_quantity: Optional[float] = None) -> Optional[SimPosition]:
            nonlocal balance
            # --- Sentiment macro filter (identical to live's macro gate) ---
            sentiment_value, sentiment_bias = _sentiment_at(sym, i)
            if sentiment_value is not None and not is_direction_allowed(sentiment_bias, direction):
                return None
            sentiment_conf_adj = 1.0
            if sentiment_value is not None:
                reading = SentimentReading(
                    value=sentiment_value, classification="", timestamp=datetime.now(timezone.utc),
                    trading_allowed=True, trading_bias=sentiment_bias, reason="",
                )
                sentiment_conf_adj = sentiment_helper.get_confidence_adjustment(reading, direction)

            # --- Funding-rate filter ---
            funding_tuple = _funding_at(sym, i)
            funding_conf_adj = 1.0
            if funding_tuple is not None:
                rate, funding_bias, funding_strength = funding_tuple
                freading = FundingRateReading(
                    symbol=sym, funding_rate=rate, annualized_rate=0.0, signal_bias=funding_bias,
                    signal_strength=funding_strength, timestamp=datetime.now(timezone.utc), is_simulated=False,
                )
                if not funding_helper.is_signal_aligned_with_funding(direction, freading):
                    return None
                funding_conf_adj = funding_helper.get_confidence_adjustment(direction, freading)

            adjusted_confidence = min(confidence * sentiment_conf_adj * funding_conf_adj, 1.0)

            # --- Reversal handling: close opposite position, no-op if same side already open ---
            existing = open_positions.get(sym)
            if existing is not None:
                if existing.side != direction:
                    _close(existing, entry, "reversal", i, sym)
                    del open_positions[sym]
                else:
                    return None

            if len(open_positions) >= config.max_positions:
                return None

            entry_fill = entry * (1 + slip) if direction == "BUY" else entry * (1 - slip)
            portfolio_value = _portfolio_value(i)
            risk_manager.update_peak_portfolio_value(portfolio_value)

            best_kelly = max(
                (performance_tracker.get_kelly_fraction(n.strip()) for n in label.split(" + ")),
                default=0.02,
            )
            if forced_quantity is not None:
                # Hedge legs bypass the risk gate to match the primary leg's USD
                # value exactly — same as trading_engine._execute_ensemble_signal.
                sizing = PositionSizeResult(
                    allowed=True, quantity=forced_quantity,
                    position_value=forced_quantity * entry_fill, risk_amount=0.0,
                )
            else:
                sizing = risk_manager.calculate_position_size(
                    portfolio_value=portfolio_value, entry_price=entry_fill, stop_loss_price=stop,
                    signal_confidence=adjusted_confidence, open_positions_count=len(open_positions),
                    open_symbols=list(open_positions.keys()), symbol=sym, side=direction,
                    kelly_fraction=best_kelly, take_profit_price=tp,
                )
                if not sizing.allowed:
                    return None

            # --- ML adaptive filter (hedge legs bypass it, same as live) ---
            if forced_quantity is None and ml_filter is not None:
                stop_distance_pct = abs(entry_fill - stop) / entry_fill * 100 if entry_fill else 0.0
                rr_ratio = abs(tp - entry_fill) / max(abs(entry_fill - stop), 1e-10)
                features = {
                    "symbol": sym, "direction": direction, "regime": regime,
                    "ensemble_confidence": round(adjusted_confidence, 4),
                    "regime_boost": regime_boost,
                    "agreeing_strategies_count": agreeing_count,
                    "disagreeing_strategies_count": disagreeing_count,
                    "sentiment_value": sentiment_value if sentiment_value is not None else 50,
                    "sentiment_bias": sentiment_bias,
                    "funding_rate": funding_tuple[0] if funding_tuple else 0.0,
                    "funding_bias": funding_tuple[1] if funding_tuple else "NEUTRAL",
                    "kelly_fraction": round(best_kelly, 4),
                    "stop_distance_pct": round(stop_distance_pct, 4),
                    "risk_reward_ratio": round(rr_ratio, 3),
                    "portfolio_drawdown_at_entry": round(
                        risk_manager.compute_current_drawdown_percent(portfolio_value), 2),
                    "open_positions_at_entry": len(open_positions),
                    "hft_mode": False,
                }
                ml_probability = ml_filter.score(features)
                if ml_probability is not None and ml_probability < ml_filter.min_win_probability:
                    return None

            position = SimPosition(
                symbol=sym, side=direction, entry_price=entry_fill, quantity=sizing.quantity,
                stop_loss=stop, take_profit=tp, entry_index=i, strategy=label, regime=regime,
            )
            open_positions[sym] = position
            return position

        for step, i in enumerate(range(WINDOW_CANDLES, min_len - 1)):
            # 1) Check exits on all open positions
            for sym in list(open_positions.keys()):
                pos = open_positions[sym]
                row = data[sym].iloc[i]
                exit_hit = self._candle_exit(pos, float(row["high"]), float(row["low"]))
                if exit_hit:
                    _close(pos, exit_hit[0], exit_hit[1], i, sym)
                    del open_positions[sym]
                elif i - pos.entry_index >= config.max_hold_candles:
                    _close(pos, float(row["close"]), "time_exit", i, sym)
                    del open_positions[sym]

            # 2) Look for entries — generic per-symbol ensemble/solo signals
            if effective_names and len(open_positions) < config.max_positions:
                for sym in data:
                    if len(open_positions) >= config.max_positions:
                        break
                    result = self._signal_for_window(sym, data, i, config, effective_names, ensemble, performance_tracker)
                    if result is None:
                        continue
                    _try_open(
                        sym, result.direction, result.entry, result.stop, result.tp, result.confidence,
                        result.label, result.regime, result.regime_boost,
                        result.agreeing_count, result.disagreeing_count, i,
                    )

            # 2b) Statistical Arbitrage — dedicated delta-neutral BTC/ETH pairs trade,
            # executed outside the ensemble exactly like the live engine's separate
            # _run_pairs_signal() path (both legs must be attempted together).
            if pairs_enabled and (config.max_positions - len(open_positions)) >= 2:
                btc_window = data[PAIRS_PRIMARY_SYMBOL].iloc[i - WINDOW_CANDLES:i + 1].reset_index(drop=True)
                eth_window = data[PAIRS_HEDGE_SYMBOL].iloc[i - WINDOW_CANDLES:i + 1].reset_index(drop=True)
                try:
                    sig = pairs_strategy.compute_signal_from_pair(PAIRS_PRIMARY_SYMBOL, btc_window, eth_window)
                except Exception:
                    sig = None
                if sig:
                    regime_value = self.regime_detector.analyze(btc_window).regime.value
                    hedge_dir = "SELL" if sig.signal_type == "BUY" else "BUY"
                    eth_price = float(eth_window["close"].iloc[-1])
                    sl_pct = abs(sig.price - sig.suggested_stop_loss) / sig.price if sig.price > 0 else 0.02
                    tp_pct = abs(sig.suggested_take_profit - sig.price) / sig.price if sig.price > 0 else 0.04
                    if hedge_dir == "BUY":
                        eth_sl, eth_tp = eth_price * (1 - sl_pct), eth_price * (1 + tp_pct)
                    else:
                        eth_sl, eth_tp = eth_price * (1 + sl_pct), eth_price * (1 - tp_pct)

                    primary_pos = _try_open(
                        PAIRS_PRIMARY_SYMBOL, sig.signal_type, sig.price, sig.suggested_stop_loss,
                        sig.suggested_take_profit, sig.strength, "Statistical Arbitrage", regime_value,
                        1.0, 1, 0, i,
                    )
                    if primary_pos is not None and eth_price > 0:
                        forced_qty = round(primary_pos.quantity * primary_pos.entry_price / eth_price, 8)
                        if forced_qty > 0:
                            _try_open(
                                PAIRS_HEDGE_SYMBOL, hedge_dir, eth_price, eth_sl, eth_tp,
                                sig.strength, "Statistical Arbitrage", regime_value, 1.0, 1, 0, i,
                                forced_quantity=forced_qty,
                            )

            # 3) Mark-to-market equity
            equity_curve.append({
                "timestamp": str(data[next(iter(data))]["timestamp"].iloc[i]),
                "equity": round(_portfolio_value(i), 2),
            })

            if progress_callback and step % 200 == 0:
                progress_callback(step / total_steps)
            # Yield the GIL periodically so this CPU-heavy loop (running in a
            # background thread) doesn't starve the main asyncio event loop —
            # without this, other API requests (prices, status) stall while a
            # backtest is running.
            if step % 25 == 0:
                time.sleep(0)

        # Force-close leftovers at final close
        last_i = min_len - 2
        for sym in list(open_positions.keys()):
            pos = open_positions[sym]
            _close(pos, float(data[sym]["close"].iloc[last_i]), "end_of_backtest", last_i, sym)
            del open_positions[sym]

        return self._build_report(config, trades, equity_curve, strategy_names)

    # ── Metrics ─────────────────────────────────────────────────────────
    def _build_report(self, config: BacktestConfig, trades: list[dict],
                      equity_curve: list[dict], strategy_names: list[str]) -> dict:
        final_equity = equity_curve[-1]["equity"] if equity_curve else config.initial_balance
        total_return_pct = (final_equity / config.initial_balance - 1) * 100

        wins = [t for t in trades if t["pnl"] > 0]
        losses = [t for t in trades if t["pnl"] <= 0]
        gross_profit = sum(t["pnl"] for t in wins)
        gross_loss = abs(sum(t["pnl"] for t in losses))
        if gross_loss > 0:
            profit_factor = gross_profit / gross_loss
        elif gross_profit > 0:
            profit_factor = math.inf
        else:
            profit_factor = 0.0

        # Sharpe / Sortino on per-candle equity returns, annualized by timeframe
        equity_series = pd.Series([p["equity"] for p in equity_curve])
        returns = equity_series.pct_change().dropna()
        periods_per_year = CANDLES_PER_YEAR.get(config.timeframe, 105120)
        sharpe = sortino = 0.0
        if len(returns) > 2 and returns.std() > 0:
            sharpe = float(returns.mean() / returns.std() * math.sqrt(periods_per_year))
            downside = returns[returns < 0]
            if len(downside) > 1 and downside.std() > 0:
                sortino = float(returns.mean() / downside.std() * math.sqrt(periods_per_year))

        # Max drawdown
        running_peak = equity_series.cummax()
        drawdowns = (equity_series - running_peak) / running_peak
        max_drawdown_pct = float(drawdowns.min() * 100) if len(drawdowns) else 0.0

        # Per-strategy breakdown (split joined ensemble labels)
        per_strategy: dict[str, dict] = {}
        for t in trades:
            for base in t["strategy"].split(" + "):
                base = base.strip()
                s = per_strategy.setdefault(base, {"trades": 0, "wins": 0, "pnl": 0.0})
                s["trades"] += 1
                s["pnl"] += t["pnl"]
                if t["pnl"] > 0:
                    s["wins"] += 1
        strategy_breakdown = [
            {"strategy": k, "trades": v["trades"], "wins": v["wins"],
             "win_rate": round(v["wins"] / v["trades"] * 100, 1) if v["trades"] else 0,
             "total_pnl": round(v["pnl"], 2)}
            for k, v in sorted(per_strategy.items(), key=lambda kv: -kv[1]["pnl"])
        ]

        # Regime breakdown
        per_regime: dict[str, dict] = {}
        for t in trades:
            r = per_regime.setdefault(t["regime"], {"trades": 0, "wins": 0, "pnl": 0.0})
            r["trades"] += 1
            r["pnl"] += t["pnl"]
            if t["pnl"] > 0:
                r["wins"] += 1
        regime_breakdown = [
            {"regime": k, "trades": v["trades"],
             "win_rate": round(v["wins"] / v["trades"] * 100, 1) if v["trades"] else 0,
             "total_pnl": round(v["pnl"], 2)}
            for k, v in per_regime.items()
        ]

        total_fees = sum(t["fees"] for t in trades)
        # Thin the equity curve for the frontend chart (max ~500 points)
        stride = max(len(equity_curve) // 500, 1)
        thin_curve = equity_curve[::stride]

        return {
            "config": {
                "symbols": config.symbols, "days": config.days, "timeframe": config.timeframe,
                "strategies": strategy_names, "mode": config.mode,
                "initial_balance": config.initial_balance,
                "risk_per_trade_pct": config.risk_per_trade_pct,
                "fee_rate": config.fee_rate, "slippage_bps": config.slippage_bps,
            },
            "metrics": {
                "final_equity": round(final_equity, 2),
                "total_return_pct": round(total_return_pct, 2),
                "total_trades": len(trades),
                "wins": len(wins),
                "losses": len(losses),
                "win_rate": round(len(wins) / len(trades) * 100, 1) if trades else 0,
                "profit_factor": round(profit_factor, 2) if math.isfinite(profit_factor) else None,
                "sharpe_ratio": round(sharpe, 2),
                "sortino_ratio": round(sortino, 2),
                "max_drawdown_pct": round(max_drawdown_pct, 2),
                "avg_trade_pnl": round(sum(t["pnl"] for t in trades) / len(trades), 4) if trades else 0,
                "total_fees": round(total_fees, 2),
                "avg_hold_candles": round(sum(t["held_candles"] for t in trades) / len(trades), 1) if trades else 0,
            },
            "strategy_breakdown": strategy_breakdown,
            "regime_breakdown": regime_breakdown,
            "equity_curve": thin_curve,
            "trades": trades[-200:],  # last 200 for UI
        }


# ── Worker-process entry point ───────────────────────────────────────────
# Must be a plain module-level function (not a bound method) so it can be
# pickled and sent to a fresh worker process on Windows (spawn start method).
def _execute_backtest_job(job: Any, registry: dict, config: BacktestConfig, risk_kwargs: Optional[dict]):
    """Runs an entire backtest job (data fetch + simulation) inside an isolated
    worker process. This is what actually fixes other pages stalling while a
    backtest runs: the CPU-heavy simulation loop (now much heavier with the
    full risk/ML/sentiment/funding pipeline wired in) can no longer contend
    for the GIL with the API server's asyncio event loop — they're different
    OS processes entirely. `job` is a multiprocessing.Manager dict proxy so
    writes here are immediately visible to the parent process."""
    try:
        backtester = Backtester(registry)
        job["status"] = "fetching_data"
        # Statistical Arbitrage needs both legs of the BTC/ETH spread even if
        # the user only selected one of them as a symbol to trade.
        symbols_to_fetch = list(config.symbols)
        if "pairs" in config.strategies:
            for required in (PAIRS_PRIMARY_SYMBOL, PAIRS_HEDGE_SYMBOL):
                if required not in symbols_to_fetch:
                    symbols_to_fetch.append(required)
        data: dict[str, pd.DataFrame] = {}
        for sym in symbols_to_fetch:
            df = Backtester.fetch_history_sync(sym, config.timeframe, config.days)
            if len(df) > WINDOW_CANDLES + 10:
                data[sym] = df
        if not data:
            job["status"] = "failed"
            job["error"] = "No historical data fetched"
            return

        sentiment_history = Backtester.fetch_sentiment_history_sync(config.days)
        funding_history = Backtester.fetch_funding_history_sync(list(data.keys()), config.days)
        # One shared ML filter instance: it loads the SAME trained model.joblib
        # the live engine scores entries with. Scoring is stateless/read-only,
        # so it's safe to reuse across every sub-run in compare mode.
        ml_filter = MLSignalFilter()

        def _make_risk_manager() -> RiskManager:
            # Fresh instance per run — RiskManager carries mutable state
            # (peak portfolio value, circuit breaker) that must not leak
            # between compare mode's independent sub-runs.
            rm = RiskManager(**(risk_kwargs or {}))
            config.max_positions = rm.max_concurrent_positions
            return rm

        job["status"] = "running"
        if config.mode == "compare":
            # Run every strategy solo + ensemble + adaptive, collect summary.
            # Progress must be monotonic across ALL sub-runs (not reset to 0
            # for each one) so the UI progress bar doesn't visibly loop.
            results = {}
            solo_config_base = config
            total_runs = len(config.strategies) + 2

            def _progress_for(run_index: int):
                def _cb(fraction: float):
                    job["progress"] = round((run_index + fraction) / total_runs, 3)
                return _cb

            run_index = 0
            for name in config.strategies:
                cfg = BacktestConfig(**{**vars(solo_config_base), "mode": "solo", "strategies": [name]})
                results[name] = backtester.run(
                    cfg, data, [name], _progress_for(run_index), sentiment_history,
                    risk_manager=_make_risk_manager(), ml_filter=ml_filter, funding_history=funding_history,
                )
                run_index += 1
            for combo_mode in ("ensemble", "adaptive"):
                cfg = BacktestConfig(**{**vars(solo_config_base), "mode": combo_mode})
                results[combo_mode] = backtester.run(
                    cfg, data, config.strategies, _progress_for(run_index), sentiment_history,
                    risk_manager=_make_risk_manager(), ml_filter=ml_filter, funding_history=funding_history,
                )
                run_index += 1
            comparison = [
                {"name": k, **v["metrics"]} for k, v in results.items()
            ]
            comparison.sort(key=lambda r: r["total_return_pct"], reverse=True)
            job["result"] = {
                "mode": "compare",
                "comparison": comparison,
                "runs": results,
                "best": comparison[0]["name"] if comparison else None,
            }
        else:
            def _progress(fraction: float):
                job["progress"] = round(fraction, 3)
            job["result"] = backtester.run(
                config, data, config.strategies, _progress, sentiment_history,
                risk_manager=_make_risk_manager(), ml_filter=ml_filter, funding_history=funding_history,
            )
        job["status"] = "completed"
        job["progress"] = 1.0
        job["finished_at"] = datetime.now(timezone.utc).isoformat()
    except Exception as exc:
        logger.exception("Backtest job failed")
        job["status"] = "failed"
        job["error"] = str(exc)


# ── Async job manager ───────────────────────────────────────────────────
class BacktestJobManager:
    """Dispatches backtests to a small pool of worker processes and tracks
    progress/results via a shared multiprocessing.Manager dict per job."""

    _executor: Optional[ProcessPoolExecutor] = None
    _manager: Optional[Any] = None

    def __init__(self, strategy_registry: dict):
        self._registry = strategy_registry
        self.jobs: dict[str, Any] = {}   # job_id -> manager DictProxy

    @classmethod
    def _ensure_pool(cls):
        if cls._executor is None:
            cls._manager = multiprocessing.Manager()
            cls._executor = ProcessPoolExecutor(max_workers=2)

    async def start(self, config: BacktestConfig) -> str:
        self._ensure_pool()
        job_id = uuid.uuid4().hex[:12]
        job_proxy = BacktestJobManager._manager.dict({
            "id": job_id, "status": "queued", "progress": 0.0,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "config": vars(config), "result": None, "error": None,
        })
        self.jobs[job_id] = job_proxy
        # Mirror the exact risk parameters currently configured on the Settings
        # page so the backtest's risk engine matches live/paper trading 1:1,
        # instead of relying on RiskManager's hardcoded defaults.
        risk_kwargs: dict = {}
        try:
            from models.database import AsyncSessionLocal, BotSettings
            from sqlalchemy import select as _select
            async with AsyncSessionLocal() as session:
                result = await session.execute(_select(BotSettings).where(BotSettings.id == 1))
                row = result.scalar_one_or_none()
                if row:
                    risk_kwargs = {
                        "max_portfolio_risk_percent": row.max_portfolio_risk_percent,
                        "max_drawdown_percent": row.max_drawdown_percent,
                        "max_concurrent_positions": row.max_concurrent_positions,
                    }
        except Exception as exc:
            logger.warning("Could not load live risk settings for backtest, using defaults: %s", exc)
        loop = asyncio.get_running_loop()
        loop.run_in_executor(
            BacktestJobManager._executor, _execute_backtest_job, job_proxy, self._registry, config, risk_kwargs,
        )
        return job_id

    def get(self, job_id: str) -> Optional[dict]:
        proxy = self.jobs.get(job_id)
        return dict(proxy) if proxy is not None else None

    def list_jobs(self) -> list[dict]:
        plain_jobs = [dict(job) for job in self.jobs.values()]
        return [
            {k: v for k, v in job.items() if k != "result"}
            for job in sorted(plain_jobs, key=lambda j: j["started_at"], reverse=True)
        ]
