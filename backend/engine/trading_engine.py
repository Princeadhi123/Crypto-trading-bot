import asyncio
import json
import logging
import time
from datetime import datetime
from typing import Optional
import ccxt.async_support as ccxt
import pandas as pd
from sqlalchemy import update

from models.database import TradeRecord, AsyncSessionLocal
from engine.risk_manager import RiskManager
from engine.regime_detector import MarketRegimeDetector, MarketRegime
from engine.signal_ensemble import SignalEnsemble, EnsembleSignal
from engine.strategy_performance_tracker import StrategyPerformanceTracker
from engine.var_calculator import VaRCalculator
from engine.sentiment_filter import SentimentFilter
from engine.funding_rate_signal import FundingRateSignal
from engine.twap_executor import TwapExecutor
from engine.strategies.rsi_strategy import RsiStrategy
from engine.strategies.macd_strategy import MacdStrategy
from engine.strategies.bollinger_strategy import BollingerBandsStrategy
from engine.strategies.scalping_strategy import ScalpingStrategy
from engine.strategies.pairs_strategy import StatisticalArbitrageStrategy

logger = logging.getLogger(__name__)

STRATEGY_REGISTRY = {
    "rsi": RsiStrategy(),
    "macd": MacdStrategy(),
    "bollinger": BollingerBandsStrategy(),
    "scalping": ScalpingStrategy(),
    "pairs": StatisticalArbitrageStrategy(),
}

# HFT-tuned registry: EMA 3/8, ATR stop 0.8x, target 1.5x — ultra-fast crossovers on 1m bars
HFT_STRATEGY_REGISTRY = {
    "rsi": RsiStrategy(rsi_period=9, oversold_threshold=25.0, overbought_threshold=75.0,
                       stop_loss_percent=0.5, take_profit_percent=1.0),
    "macd": MacdStrategy(fast_period=6, slow_period=13, signal_period=5,
                         stop_loss_percent=0.6, take_profit_percent=1.2),
    "bollinger": BollingerBandsStrategy(period=14, std_deviation=1.8,
                                        stop_loss_percent=0.5, take_profit_percent=1.0),
    "scalping": ScalpingStrategy(fast_ema_period=3, slow_ema_period=8, momentum_period=5,
                                  atr_period=7, atr_stop_multiplier=0.8, atr_target_multiplier=1.5),
    "pairs": StatisticalArbitrageStrategy(spread_lookback=30, zscore_entry_threshold=1.8,
                                           stop_loss_percent=1.5, take_profit_percent=1.2),
}

# Standard mode: 5m candles, 30s loop  |  HFT mode: 1m candles, 5s loop
TIMEFRAME_STANDARD = "5m"
TIMEFRAME_HFT = "1m"
OHLCV_LIMIT = 300
LOOP_INTERVAL_STANDARD = 30
LOOP_INTERVAL_HFT = 5
TRAILING_STOP_ACTIVATION_PERCENT = 1.5
TRAILING_STOP_ACTIVATION_PERCENT_HFT = 0.5   # Activate trailing much sooner on 1m
TRAILING_STOP_TRAIL_PERCENT = 1.0
TRAILING_STOP_TRAIL_PERCENT_HFT = 0.3        # Tighter trail on HFT
EXIT_CHECK_INTERVAL_HFT = 2                   # Check exits every 2s in HFT mode


class ActivePosition:
    def __init__(self, trade_id: int, symbol: str, side: str, strategy: str,
                 entry_price: float, quantity: float, stop_loss: float, take_profit: float):
        self.trade_id = trade_id
        self.symbol = symbol
        self.side = side
        self.strategy = strategy
        self.entry_price = entry_price
        self.quantity = quantity
        self.stop_loss = stop_loss
        self.initial_stop_loss = stop_loss
        self.take_profit = take_profit
        self.current_price = entry_price
        self.highest_price = entry_price
        self.lowest_price = entry_price
        self.trailing_stop_activated = False
        self.opened_at = datetime.utcnow()

    @property
    def unrealized_pnl(self) -> float:
        if self.side == "BUY":
            return (self.current_price - self.entry_price) * self.quantity
        return (self.entry_price - self.current_price) * self.quantity

    @property
    def unrealized_pnl_percent(self) -> float:
        cost = self.entry_price * self.quantity
        if cost == 0:
            return 0.0
        return (self.unrealized_pnl / cost) * 100


class TradingEngine:
    def __init__(self):
        self.is_running = False
        self.paper_trading = True
        self.paper_balance = 10000.0
        self.active_positions: dict[str, ActivePosition] = {}
        self.risk_manager = RiskManager()
        self.regime_detector = MarketRegimeDetector()
        self.signal_ensemble = SignalEnsemble(minimum_agreement_count=2, minimum_composite_confidence=0.45)
        self.performance_tracker = StrategyPerformanceTracker(rolling_window=30)
        self.var_calculator = VaRCalculator(rolling_window_days=252)
        self.sentiment_filter = SentimentFilter(cache_ttl_minutes=30)
        self.funding_rate_signal = FundingRateSignal(exchange=None)
        self.twap_executor = TwapExecutor(default_slices=5, default_interval_seconds=10.0)
        self.exchange: Optional[ccxt.Exchange] = None
        self.current_sentiment: Optional[dict] = None
        self.last_var_report: Optional[dict] = None
        self.hft_mode: bool = False
        self.active_symbols: list[str] = ["BTC/USDT", "ETH/USDT", "BNB/USDT", "SOL/USDT"]
        self.active_strategy_names: list[str] = ["rsi", "macd", "bollinger"]
        self.total_realized_pnl = 0.0
        self.total_trades_today = 0
        self.total_signals_today = 0
        self.start_time = time.time()
        self.last_tick: Optional[datetime] = None
        self.ohlcv_cache: dict[str, pd.DataFrame] = {}
        self.recent_signals: list[dict] = []
        self.market_prices: dict[str, float] = {}
        self.current_regimes: dict[str, str] = {}
        self._main_loop_task: Optional[asyncio.Task] = None
        self._price_stream_task: Optional[asyncio.Task] = None
        self._broadcast_callback = None

    @property
    def _active_timeframe(self) -> str:
        return TIMEFRAME_HFT if self.hft_mode else TIMEFRAME_STANDARD

    @property
    def _loop_interval(self) -> int:
        return LOOP_INTERVAL_HFT if self.hft_mode else LOOP_INTERVAL_STANDARD

    @property
    def _strategy_registry(self) -> dict:
        return HFT_STRATEGY_REGISTRY if self.hft_mode else STRATEGY_REGISTRY

    @property
    def _trailing_activation(self) -> float:
        return TRAILING_STOP_ACTIVATION_PERCENT_HFT if self.hft_mode else TRAILING_STOP_ACTIVATION_PERCENT

    @property
    def _trailing_percent(self) -> float:
        return TRAILING_STOP_TRAIL_PERCENT_HFT if self.hft_mode else TRAILING_STOP_TRAIL_PERCENT

    def set_broadcast_callback(self, callback):
        self._broadcast_callback = callback

    async def _broadcast(self, event_type: str, data: dict):
        if self._broadcast_callback:
            await self._broadcast_callback(event_type, data)

    async def initialize_exchange(self, exchange_name: str, api_key: str = "", api_secret: str = ""):
        try:
            exchange_class = getattr(ccxt, exchange_name.lower(), None)
            if exchange_class is None:
                logger.error("Unknown exchange: %s", exchange_name)
                return False
            self.exchange = exchange_class({
                "apiKey": api_key,
                "secret": api_secret,
                "enableRateLimit": True,
                "options": {"defaultType": "spot"},
            })
            self.funding_rate_signal.set_exchange(self.exchange)
            logger.info("Exchange %s initialized", exchange_name)
            return True
        except Exception as exc:
            logger.error("Exchange init failed: %s", exc)
            return False

    def get_var_report(self) -> dict:
        if self.last_var_report:
            return self.last_var_report
        portfolio_value = self._compute_portfolio_value()
        report = self.var_calculator.compute(portfolio_value)
        return {
            "var_95": report.var_95,
            "var_99": report.var_99,
            "cvar_95": report.cvar_95,
            "cvar_99": report.cvar_99,
            "daily_volatility": report.daily_volatility,
            "annualized_volatility": report.annualized_volatility,
            "sharpe_ratio": report.sharpe_ratio,
            "sortino_ratio": report.sortino_ratio,
            "max_observed_loss": report.max_observed_loss,
            "observations": report.observations,
        }

    def get_sentiment(self) -> dict:
        if self.current_sentiment:
            return self.current_sentiment
        return {
            "value": 50,
            "classification": "Neutral (not fetched yet)",
            "trading_bias": "BOTH",
            "reason": "Start bot to fetch live sentiment",
        }

    def get_funding_rates(self) -> list[dict]:
        return self.funding_rate_signal.get_all_cached()

    async def _fetch_ohlcv(self, symbol: str) -> Optional[pd.DataFrame]:
        try:
            if self.exchange is None:
                return self._generate_simulated_ohlcv(symbol)
            raw_data = await self.exchange.fetch_ohlcv(
                symbol, timeframe=self._active_timeframe, limit=OHLCV_LIMIT)
            if not raw_data:
                return None
            dataframe = pd.DataFrame(raw_data, columns=["timestamp", "open", "high", "low", "close", "volume"])
            dataframe["timestamp"] = pd.to_datetime(dataframe["timestamp"], unit="ms")
            return dataframe
        except Exception as exc:
            logger.warning("OHLCV fetch failed for %s: %s", symbol, exc)
            return self._generate_simulated_ohlcv(symbol)

    def _generate_simulated_ohlcv(self, symbol: str) -> pd.DataFrame:
        import numpy as np
        base_prices = {
            "BTC/USDT": 65000.0, "ETH/USDT": 3500.0, "BNB/USDT": 600.0,
            "SOL/USDT": 180.0, "ADA/USDT": 0.55, "DOT/USDT": 8.5,
            "MATIC/USDT": 0.85, "AVAX/USDT": 38.0,
        }
        # HFT mode: higher volatility on 1m bars to generate realistic signal frequency
        volatility = 0.003 if self.hft_mode else 0.008
        candle_freq = "1min" if self.hft_mode else "5min"
        base = base_prices.get(symbol, 100.0)
        cache_key = f"{symbol}_{self._active_timeframe}"
        cached = self.ohlcv_cache.get(cache_key)

        if cached is not None and len(cached) >= OHLCV_LIMIT:
            last_close = cached["close"].iloc[-1]
            new_row = self._simulate_next_candle(last_close)
            updated = pd.concat([cached.iloc[1:], pd.DataFrame([new_row])], ignore_index=True)
            self.ohlcv_cache[cache_key] = updated
            return updated

        rng = np.random.default_rng(hash(symbol) % (2**31))
        closes = [base]
        for _ in range(OHLCV_LIMIT - 1):
            change = rng.normal(0, volatility)
            closes.append(closes[-1] * (1 + change))

        timestamps = pd.date_range(end=datetime.utcnow(), periods=OHLCV_LIMIT, freq=candle_freq)
        rows = []
        for i, close_price in enumerate(closes):
            spread = close_price * (0.001 if self.hft_mode else 0.002)
            rows.append({
                "timestamp": timestamps[i],
                "open": close_price * (1 + rng.uniform(-0.0005, 0.0005)),
                "high": close_price + abs(rng.normal(0, spread)),
                "low": close_price - abs(rng.normal(0, spread)),
                "close": close_price,
                "volume": abs(rng.normal(800 if self.hft_mode else 1000, 200)),
            })
        dataframe = pd.DataFrame(rows)
        self.ohlcv_cache[cache_key] = dataframe
        return dataframe

    def _simulate_next_candle(self, last_close: float) -> dict:
        import numpy as np
        rng = np.random.default_rng()
        volatility = 0.002 if self.hft_mode else 0.006
        change = rng.normal(0, volatility)
        new_close = last_close * (1 + change)
        spread = new_close * (0.0005 if self.hft_mode else 0.002)
        return {
            "timestamp": datetime.utcnow(),
            "open": last_close,
            "high": new_close + abs(rng.normal(0, spread)),
            "low": new_close - abs(rng.normal(0, spread)),
            "close": new_close,
            "volume": abs(rng.normal(800 if self.hft_mode else 1000, 200)),
        }

    async def _fetch_current_price(self, symbol: str) -> Optional[float]:
        cache_key = f"{symbol}_{self._active_timeframe}"
        cached_df = self.ohlcv_cache.get(cache_key)
        if cached_df is not None:
            return float(cached_df["close"].iloc[-1])
        try:
            if self.exchange:
                ticker = await self.exchange.fetch_ticker(symbol)
                return ticker["last"]
        except Exception:
            pass
        return None

    async def _run_ensemble_for_symbol(self, symbol: str):
        ohlcv_data = await self._fetch_ohlcv(symbol)
        if ohlcv_data is None:
            return None
        self.market_prices[symbol] = float(ohlcv_data["close"].iloc[-1])
        regime_analysis = self.regime_detector.analyze(ohlcv_data)
        self.current_regimes[symbol] = regime_analysis.regime.value
        regime_weights = self.regime_detector.get_strategy_weights(regime_analysis)
        combined_weights = self.performance_tracker.get_combined_weights(regime_weights)
        raw_signals = []
        for strategy_name in self.active_strategy_names:
            strategy = self._strategy_registry.get(strategy_name)
            if strategy and strategy.enabled:
                try:
                    sig = strategy.compute_signal(symbol, ohlcv_data)
                    if sig:
                        raw_signals.append(sig)
                        self.total_signals_today += 1
                except Exception as exc:
                    logger.error("Strategy %s error on %s: %s", strategy_name, symbol, exc)
        if not raw_signals:
            return None

        # Reuse self.signal_ensemble which is already configured with HFT-aware
        # parameters in start() — avoids creating a new object on every tick
        ensemble_signal = self.signal_ensemble.aggregate(
            symbol=symbol, raw_signals=raw_signals,
            strategy_weights=combined_weights, regime_analysis=regime_analysis,
        )
        if ensemble_signal:
            logger.info("%s Ensemble %s %s conf=%.3f regime=%s agreed=%s",
                "[HFT]" if self.hft_mode else "",
                ensemble_signal.direction, symbol, ensemble_signal.final_confidence,
                regime_analysis.regime.value, ensemble_signal.agreeing_strategies)
        return ensemble_signal

    async def _execute_ensemble_signal(self, es):
        # --- Sentiment macro filter ---
        try:
            sentiment = await self.sentiment_filter.fetch_current_sentiment()
            self.current_sentiment = {
                "value": sentiment.value,
                "classification": sentiment.classification,
                "trading_bias": sentiment.trading_bias,
                "reason": sentiment.reason,
            }
            if not self.sentiment_filter.is_signal_allowed(es.direction, sentiment):
                logger.info("Sentiment filter blocked %s %s: %s", es.direction, es.symbol, sentiment.reason)
                return
            sentiment_confidence_adj = self.sentiment_filter.get_confidence_adjustment(sentiment)
        except Exception:
            sentiment_confidence_adj = 1.0

        # --- Funding rate filter ---
        funding_reading = None
        try:
            funding_reading = await self.funding_rate_signal.get_funding_rate(es.symbol)
            if not self.funding_rate_signal.is_signal_aligned_with_funding(es.direction, funding_reading):
                logger.info("Funding rate blocked %s %s: rate=%.4f%% bias=%s",
                            es.direction, es.symbol, funding_reading.funding_rate * 100, funding_reading.signal_bias)
                return
            funding_confidence_adj = self.funding_rate_signal.get_confidence_adjustment(es.direction, funding_reading)
        except Exception:
            funding_confidence_adj = 1.0

        adjusted_confidence = min(es.final_confidence * sentiment_confidence_adj * funding_confidence_adj, 1.0)

        open_symbols = list(self.active_positions.keys())
        portfolio_value = self._compute_portfolio_value()
        self.risk_manager.update_peak_portfolio_value(portfolio_value)
        best_kelly = max((self.performance_tracker.get_kelly_fraction(s.strategy_name)
                         for s in es.raw_signals), default=0.02)
        sizing = self.risk_manager.calculate_position_size(
            portfolio_value=portfolio_value, entry_price=es.weighted_entry_price,
            stop_loss_price=es.suggested_stop_loss, signal_confidence=adjusted_confidence,
            open_positions_count=len(self.active_positions), open_symbols=open_symbols,
            symbol=es.symbol, side=es.direction, kelly_fraction=best_kelly,
        )
        if not sizing.allowed:
            logger.info("Rejected %s: %s", es.symbol, sizing.rejection_reason)
            return
        if self.paper_trading:
            if self.paper_balance < sizing.position_value:
                return
            self.paper_balance -= sizing.position_value
        label = " + ".join(es.agreeing_strategies)

        # Collect all ML training features at entry time.
        # These will be used in Phase 3 to train an XGBoost classifier:
        # "given these market conditions at entry, did this trade win or lose?"
        ml_features = {
            "symbol": es.symbol,
            "direction": es.direction,
            "regime": es.regime.value,
            "ensemble_confidence": round(adjusted_confidence, 4),
            "regime_boost": es.regime_boost,
            "agreeing_strategies_count": len(es.agreeing_strategies),
            "disagreeing_strategies_count": len(es.disagreeing_strategies),
            "sentiment_value": self.current_sentiment.get("value", 50) if self.current_sentiment else 50,
            "sentiment_bias": self.current_sentiment.get("trading_bias", "BOTH") if self.current_sentiment else "BOTH",
            "funding_rate": round(funding_reading.funding_rate, 8) if funding_reading else 0.0,
            "funding_bias": funding_reading.signal_bias if funding_reading else "NEUTRAL",
            "kelly_fraction": round(best_kelly, 4),
            "stop_distance_pct": round(abs(es.weighted_entry_price - es.suggested_stop_loss) / es.weighted_entry_price * 100, 4),
            "risk_reward_ratio": round(abs(es.suggested_take_profit - es.weighted_entry_price) / max(abs(es.weighted_entry_price - es.suggested_stop_loss), 1e-10), 3),
            "portfolio_drawdown_at_entry": round(self.risk_manager.compute_current_drawdown_percent(portfolio_value), 2),
            "open_positions_at_entry": len(open_symbols),
            "hft_mode": self.hft_mode,
            "raw_signal_details": [s.details for s in es.raw_signals],
        }

        async with AsyncSessionLocal() as session:
            trade = TradeRecord(
                symbol=es.symbol, side=es.direction, strategy=label,
                entry_price=es.weighted_entry_price, quantity=sizing.quantity,
                stop_loss_price=es.suggested_stop_loss, take_profit_price=es.suggested_take_profit,
                status="open", is_paper_trade=self.paper_trading,
                notes=f"conf={es.final_confidence:.3f} regime={es.regime.value} boost={es.regime_boost}",
                signal_features=json.dumps(ml_features),
            )
            session.add(trade)
            await session.commit()
            await session.refresh(trade)
            position = ActivePosition(
                trade_id=trade.id, symbol=es.symbol, side=es.direction, strategy=label,
                entry_price=es.weighted_entry_price, quantity=sizing.quantity,
                stop_loss=es.suggested_stop_loss, take_profit=es.suggested_take_profit,
            )
            self.active_positions[es.symbol] = position
            self.total_trades_today += 1
        self.recent_signals.insert(0, {"symbol": es.symbol, "strategy": label,
            "signal_type": es.direction, "strength": es.final_confidence,
            "price": es.weighted_entry_price, "timestamp": es.timestamp.isoformat(),
            "details": {"regime": es.regime.value, "agreed": es.agreeing_strategies}})
        self.recent_signals = self.recent_signals[:50]
        await self._broadcast("new_trade", {"symbol": es.symbol, "side": es.direction,
            "strategy": label, "entry_price": es.weighted_entry_price,
            "quantity": sizing.quantity, "confidence": es.final_confidence,
            "regime": es.regime.value})

    def _update_trailing_stop(self, position: ActivePosition, current_price: float):
        activation = self._trailing_activation
        trail_pct = self._trailing_percent
        if position.side == "BUY":
            position.highest_price = max(position.highest_price, current_price)
            profit_pct = (position.highest_price - position.entry_price) / position.entry_price * 100
            if profit_pct >= activation:
                position.trailing_stop_activated = True
                new_trail = position.highest_price * (1 - trail_pct / 100)
                if new_trail > position.stop_loss:
                    position.stop_loss = round(new_trail, 8)
        else:
            position.lowest_price = min(position.lowest_price, current_price)
            profit_pct = (position.entry_price - position.lowest_price) / position.entry_price * 100
            if profit_pct >= activation:
                position.trailing_stop_activated = True
                new_trail = position.lowest_price * (1 + trail_pct / 100)
                if new_trail < position.stop_loss:
                    position.stop_loss = round(new_trail, 8)

    async def _check_exit_conditions(self):
        symbols_to_close = []
        for symbol, position in self.active_positions.items():
            current_price = self.market_prices.get(symbol, position.current_price)
            position.current_price = current_price
            self._update_trailing_stop(position, current_price)
            should_close = False
            close_reason = ""
            if position.side == "BUY":
                if current_price <= position.stop_loss:
                    should_close = True
                    close_reason = "trailing_stop" if position.trailing_stop_activated else "stop_loss"
                elif current_price >= position.take_profit:
                    should_close = True
                    close_reason = "take_profit"
            else:
                if current_price >= position.stop_loss:
                    should_close = True
                    close_reason = "trailing_stop" if position.trailing_stop_activated else "stop_loss"
                elif current_price <= position.take_profit:
                    should_close = True
                    close_reason = "take_profit"
            if should_close:
                symbols_to_close.append((symbol, current_price, close_reason))
        for symbol, exit_price, reason in symbols_to_close:
            await self._close_position(symbol, exit_price, reason)

    async def _close_position(self, symbol: str, exit_price: float, reason: str):
        position = self.active_positions.pop(symbol, None)
        if position is None:
            return

        pnl = position.unrealized_pnl
        cost_basis = position.entry_price * position.quantity
        pnl_percent = (pnl / cost_basis * 100) if cost_basis > 0 else 0.0

        if self.paper_trading:
            if position.side == "BUY":
                # Long: receive sale proceeds
                self.paper_balance += exit_price * position.quantity
            else:
                # Short: return margin deposit (entry_price * qty) plus any PnL
                # PnL = (entry_price - exit_price) * qty, so net = (2*entry - exit) * qty
                self.paper_balance += (position.entry_price * position.quantity) + pnl

        self.total_realized_pnl += pnl

        portfolio_value = self._compute_portfolio_value()
        self.var_calculator.record_trade_pnl(pnl_dollar=pnl, portfolio_value=portfolio_value)
        var_report = self.var_calculator.compute(portfolio_value)
        self.last_var_report = {
            "var_95": var_report.var_95,
            "var_99": var_report.var_99,
            "cvar_95": var_report.cvar_95,
            "cvar_99": var_report.cvar_99,
            "daily_volatility": var_report.daily_volatility,
            "annualized_volatility": var_report.annualized_volatility,
            "sharpe_ratio": var_report.sharpe_ratio,
            "sortino_ratio": var_report.sortino_ratio,
            "max_observed_loss": var_report.max_observed_loss,
            "observations": var_report.observations,
        }

        for strategy_name in position.strategy.split(" + "):
            self.performance_tracker.record_trade_outcome(
                strategy_name=strategy_name.strip(), pnl_percent=round(pnl_percent, 4))

        async with AsyncSessionLocal() as session:
            await session.execute(
                update(TradeRecord)
                .where(TradeRecord.id == position.trade_id)
                .values(
                    exit_price=exit_price,
                    profit_loss=round(pnl, 4),
                    profit_loss_percent=round(pnl_percent, 4),
                    status="closed",
                    closed_at=datetime.utcnow(),
                    notes=reason,
                    exit_reason=reason,
                )
            )
            await session.commit()

        logger.info("Closed %s %s at %.4f | PnL: %.2f (%.2f%%) | Reason: %s | Trailing: %s",
                    position.side, symbol, exit_price, pnl, pnl_percent, reason, position.trailing_stop_activated)

        await self._broadcast("trade_closed", {
            "symbol": symbol, "exit_price": exit_price,
            "pnl": round(pnl, 4), "pnl_percent": round(pnl_percent, 4),
            "reason": reason,
        })

    async def _run_pairs_signal(self) -> Optional["EnsembleSignal"]:
        """Runs Statistical Arbitrage on the BTC/ETH spread.
        Requires both BTC/USDT and ETH/USDT in active_symbols and 'pairs' in active_strategy_names."""
        if "pairs" not in self.active_strategy_names:
            return None
        pairs_strategy = self._strategy_registry.get("pairs")
        if not pairs_strategy or not pairs_strategy.enabled:
            return None
        btc_sym, eth_sym = "BTC/USDT", "ETH/USDT"
        if btc_sym not in self.active_symbols or eth_sym not in self.active_symbols:
            return None
        btc_df = await self._fetch_ohlcv(btc_sym)
        eth_df = await self._fetch_ohlcv(eth_sym)
        if btc_df is None or eth_df is None:
            return None
        self.market_prices[btc_sym] = float(btc_df["close"].iloc[-1])
        self.market_prices[eth_sym] = float(eth_df["close"].iloc[-1])
        try:
            signal = pairs_strategy.compute_signal_from_pair(btc_sym, btc_df, eth_df)
        except Exception as exc:
            logger.error("Pairs strategy error: %s", exc)
            return None
        if signal is None:
            return None
        self.total_signals_today += 1
        regime_analysis = self.regime_detector.analyze(btc_df)
        self.current_regimes[btc_sym] = regime_analysis.regime.value
        return EnsembleSignal(
            symbol=btc_sym,
            direction=signal.signal_type,
            composite_confidence=signal.strength,
            agreeing_strategies=["Statistical Arbitrage"],
            disagreeing_strategies=[],
            weighted_entry_price=signal.price,
            suggested_stop_loss=signal.suggested_stop_loss,
            suggested_take_profit=signal.suggested_take_profit,
            regime=regime_analysis.regime,
            regime_boost=1.0,
            raw_signals=[signal],
        )

    def _compute_portfolio_value(self) -> float:
        position_value = sum(
            pos.current_price * pos.quantity for pos in self.active_positions.values()
        )
        return self.paper_balance + position_value

    async def _price_stream_loop(self):
        """Lightweight fast loop: updates prices and checks exits every 2s in HFT mode.
        Decoupled from the heavier signal-generation loop so exit checks aren't throttled."""
        logger.info("Price stream loop started (HFT)")
        while self.is_running and self.hft_mode:
            try:
                if self.exchange is not None:
                    tasks = [self._fetch_price_from_exchange(sym) for sym in self.active_symbols]
                    await asyncio.gather(*tasks, return_exceptions=True)
                await self._check_exit_conditions()
                await asyncio.sleep(EXIT_CHECK_INTERVAL_HFT)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Price stream error: %s", exc)
                await asyncio.sleep(1)
        logger.info("Price stream loop stopped")

    async def _fetch_price_from_exchange(self, symbol: str):
        try:
            if self.exchange:
                ticker = await self.exchange.fetch_ticker(symbol)
                if ticker.get("last"):
                    self.market_prices[symbol] = float(ticker["last"])
        except Exception:
            pass

    async def _trading_loop(self):
        mode_label = "HFT 1m" if self.hft_mode else "Standard 5m"
        logger.info("Trading loop started [%s | %ds interval]", mode_label, self._loop_interval)
        while self.is_running:
            try:
                self.last_tick = datetime.utcnow()

                # PARALLEL: process all symbols simultaneously — not sequentially
                results = await asyncio.gather(
                    *[self._run_ensemble_for_symbol(sym) for sym in self.active_symbols],
                    return_exceptions=True
                )
                for result in results:
                    if result and not isinstance(result, Exception):
                        await self._execute_ensemble_signal(result)

                # Run Statistical Arbitrage separately — it needs two symbols (BTC + ETH)
                pairs_result = await self._run_pairs_signal()
                if pairs_result and not isinstance(pairs_result, Exception):
                    await self._execute_ensemble_signal(pairs_result)

                # In standard mode, also check exits in this loop (HFT has dedicated loop)
                if not self.hft_mode:
                    await self._check_exit_conditions()

                portfolio_value = self._compute_portfolio_value()
                await self._broadcast("price_update", {
                    "prices": self.market_prices,
                    "portfolio_value": portfolio_value,
                    "available_balance": self.paper_balance,
                    "unrealized_pnl": sum(p.unrealized_pnl for p in self.active_positions.values()),
                    "regimes": self.current_regimes,
                    "hft_mode": self.hft_mode,
                })

                await asyncio.sleep(self._loop_interval)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Trading loop error: %s", exc)
                await asyncio.sleep(2 if self.hft_mode else 5)

        logger.info("Trading loop stopped")

    async def start(self, settings: dict):
        if self.is_running:
            return
        self.paper_trading = settings.get("paper_trading_enabled", True)
        self.paper_balance = settings.get("paper_balance", 10000.0)
        self.active_symbols = settings.get("active_symbols", ["BTC/USDT", "ETH/USDT"])
        self.active_strategy_names = settings.get("active_strategies", ["rsi", "macd"])
        self.hft_mode = settings.get("hft_mode", False)
        self.ohlcv_cache.clear()  # Clear cache when mode changes to avoid timeframe mismatch
        self.risk_manager = RiskManager(
            max_portfolio_risk_percent=settings.get("max_portfolio_risk_percent", 2.0),
            max_drawdown_percent=settings.get("max_drawdown_percent", 10.0),
            max_concurrent_positions=settings.get("max_concurrent_positions", 5),
        )
        self.signal_ensemble = SignalEnsemble(
            minimum_agreement_count=1 if self.hft_mode else 2,
            minimum_composite_confidence=0.35 if self.hft_mode else 0.45,
        )
        self.is_running = True
        self.start_time = time.time()
        self._main_loop_task = asyncio.create_task(self._trading_loop())
        if self.hft_mode:
            self._price_stream_task = asyncio.create_task(self._price_stream_loop())
        logger.info("Engine started [%s | paper=%s | symbols=%s | strategies=%s]",
                    "HFT" if self.hft_mode else "Standard",
                    self.paper_trading, self.active_symbols, self.active_strategy_names)

    async def stop(self):
        self.is_running = False
        if self._price_stream_task:
            self._price_stream_task.cancel()
            try:
                await self._price_stream_task
            except asyncio.CancelledError:
                pass
        if self._main_loop_task:
            self._main_loop_task.cancel()
            try:
                await self._main_loop_task
            except asyncio.CancelledError:
                pass
        if self.exchange:
            await self.exchange.close()
        logger.info("Trading engine stopped")

    def get_status(self) -> dict:
        return {
            "is_running": self.is_running,
            "paper_trading": self.paper_trading,
            "active_positions": len(self.active_positions),
            "total_signals_today": self.total_signals_today,
            "trades_today": self.total_trades_today,
            "uptime_seconds": time.time() - self.start_time if self.is_running else 0,
            "last_tick": self.last_tick.isoformat() if self.last_tick else None,
        }

    def get_portfolio_stats(self) -> dict:
        portfolio_value = self._compute_portfolio_value()
        unrealized_pnl = sum(p.unrealized_pnl for p in self.active_positions.values())
        drawdown = self.risk_manager.compute_current_drawdown_percent(portfolio_value)
        return {
            "total_balance": round(portfolio_value, 2),
            "available_balance": round(self.paper_balance, 2),
            "total_equity": round(portfolio_value, 2),
            "unrealized_pnl": round(unrealized_pnl, 2),
            "realized_pnl": round(self.total_realized_pnl, 2),
            "current_drawdown": round(drawdown, 2),
        }

    def get_performance_summary(self) -> list[dict]:
        return self.performance_tracker.get_summary()

    def get_regime_info(self) -> dict:
        return {
            "current_regimes": self.current_regimes,
            "strategy_weights": self.performance_tracker.get_all_dynamic_weights(),
        }

    def get_active_positions(self) -> list[dict]:
        result = []
        for symbol, pos in self.active_positions.items():
            result.append({
                "symbol": symbol,
                "side": pos.side,
                "strategy": pos.strategy,
                "entry_price": pos.entry_price,
                "current_price": pos.current_price,
                "quantity": pos.quantity,
                "unrealized_pnl": round(pos.unrealized_pnl, 4),
                "unrealized_pnl_percent": round(pos.unrealized_pnl_percent, 2),
                "stop_loss_price": pos.stop_loss,
                "take_profit_price": pos.take_profit,
                "trailing_stop_activated": pos.trailing_stop_activated,
                "opened_at": pos.opened_at.isoformat(),
                "trade_id": pos.trade_id,
            })
        return result


trading_engine = TradingEngine()
