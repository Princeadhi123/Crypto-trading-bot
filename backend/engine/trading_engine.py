import asyncio
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Optional
import ccxt.async_support as ccxt
import pandas as pd
from sqlalchemy import update, select

from models.database import TradeRecord, AsyncSessionLocal, BotSettings
from engine.risk_manager import RiskManager
from engine.regime_detector import MarketRegimeDetector, MarketRegime
from engine.signal_ensemble import SignalEnsemble, EnsembleSignal
from engine.strategy_performance_tracker import StrategyPerformanceTracker
from engine.var_calculator import VaRCalculator
from engine.sentiment_filter import SentimentFilter
from engine.funding_rate_signal import FundingRateSignal, close_public_futures_exchange
from engine.twap_executor import TwapExecutor
from engine.strategies.rsi_strategy import RsiStrategy
from engine.strategies.macd_strategy import MacdStrategy
from engine.strategies.bollinger_strategy import BollingerBandsStrategy
from engine.strategies.scalping_strategy import ScalpingStrategy
from engine.strategies.pairs_strategy import StatisticalArbitrageStrategy

logger = logging.getLogger(__name__)

TRADE_FEE_RATE = 0.001
TWAP_THRESHOLD_PCT = 0.02
BTC_USDT = "BTC/USDT"
ETH_USDT = "ETH/USDT"

STRATEGY_REGISTRY = {
    "rsi": RsiStrategy(),
    "macd": MacdStrategy(),
    "bollinger": BollingerBandsStrategy(),
    "scalping": ScalpingStrategy(),
    "pairs": StatisticalArbitrageStrategy(),
}

# HFT-tuned registry: EMA 3/8, ATR stop 0.8x, target 1.5x — ultra-fast crossovers on 1m bars
HFT_STRATEGY_REGISTRY = {
    # HFT mode: 1-minute candles — strict 70% WR filters, numerically tuned for 1m behaviour.
    # Thresholds differ from standard (5m) only where 1m data requires calibration:
    # RSI moves faster, EMA gaps are smaller, volume spikes are less pronounced on 1m.
    # Quality bar is identical — only the numbers are adjusted for 1m scale.
    "rsi": RsiStrategy(
        rsi_period=9, oversold_threshold=25.0, overbought_threshold=75.0,
        stop_loss_percent=0.5, take_profit_percent=1.0,
        volume_multiplier=1.3,        # strict but slightly below 1.5 — 1m volume is noisier
        rsi_acceleration_min=1.5,     # strict but below 2.0 — RSI accelerates faster on 1m
        trend_filter_pct=1.5,         # wider trend band — 1m price swings more vs EMA
    ),
    "macd": MacdStrategy(
        fast_period=6, slow_period=13, signal_period=5,
        stop_loss_percent=0.6, take_profit_percent=1.2,
        trend_ema_period=10,          # shorter trend EMA — 1m trends are shorter-lived
    ),
    "bollinger": BollingerBandsStrategy(
        period=14, std_deviation=1.8,
        stop_loss_percent=0.5, take_profit_percent=1.0,
        min_rr_ratio=1.3,             # strict but below 1.5 — tighter 1m bands make 1.5 hard
        rsi_oversold=35.0,            # same as standard — extreme oversold required
        rsi_overbought=65.0,          # same as standard — extreme overbought required
    ),
    "scalping": ScalpingStrategy(
        fast_ema_period=3, slow_ema_period=8, momentum_period=5,
        atr_period=7, atr_stop_multiplier=0.8, atr_target_multiplier=1.5,
        volume_multiplier=1.3,        # strict but below 1.5 — 1m volume spikes are smaller
        min_momentum=0.0007,          # strict but below 0.001 — 1m momentum magnitude smaller
        min_ema_gap_pct=0.0004,       # strict but below 0.0005 — shorter EMA periods = smaller gap
        ema_trend_lookback=2,         # 2 candles on 1m ≈ 3 candles on 5m in trend confirmation
    ),
    "pairs": StatisticalArbitrageStrategy(
        spread_lookback=30, zscore_entry_threshold=1.8,
        stop_loss_percent=1.0, take_profit_percent=2.0,
        min_correlation=0.65,         # same strict correlation gate as standard mode
    ),
}

# Standard mode: 5m candles, 30s loop  |  HFT mode: 1m candles, 5s loop
TIMEFRAME_STANDARD = "5m"
TIMEFRAME_HFT = "1m"
OHLCV_LIMIT = 300
LOOP_INTERVAL_STANDARD = 1  # 1 second for real-time dashboard updates
LOOP_INTERVAL_HFT = 1        # 1 second for maximum responsiveness
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
        self.opened_at = datetime.now(timezone.utc)

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
        self.active_symbols: list[str] = [BTC_USDT, ETH_USDT, "BNB/USDT", "SOL/USDT"]
        self.active_strategy_names: list[str] = ["rsi", "macd", "bollinger"]
        self.total_realized_pnl = 0.0
        self.total_trades_today = 0
        self.total_signals_today = 0
        self.start_time = time.time()
        self.last_tick: Optional[datetime] = None
        self.ohlcv_cache: dict[str, pd.DataFrame] = {}
        self._ohlcv_cache_time: dict[str, float] = {}
        self._sim_last_candle_time: dict[str, float] = {}
        self._cached_live_balance: float = 0.0
        self.recent_signals: list[dict] = []
        self.market_prices: dict[str, float] = {}
        self.current_regimes: dict[str, str] = {}
        self._main_loop_task: Optional[asyncio.Task] = None
        self._price_stream_task: Optional[asyncio.Task] = None
        self._broadcast_callback = None
        # Thread pool for CPU-bound work (strategy computations, regime analysis, ML inference)
        # to prevent blocking the main event loop during trading iterations
        self._cpu_executor: Optional[ThreadPoolExecutor] = None
        # Public (keyless) exchange client for market data — lets paper trading run
        # on REAL candles instead of simulated random walks (which produced
        # meaningless training data and unrealistic signals)
        self._public_data_exchange: Optional[ccxt.Exchange] = None
        # ML adaptive signal filter — scores every entry with a win-probability
        # model trained on this bot's own closed-trade history
        from engine.ml_filter import MLSignalFilter
        self.ml_filter = MLSignalFilter()

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

    async def load_initial_balance(self):
        """Load paper balance and realized PnL from database on startup so dashboard shows correct values before bot starts."""
        try:
            async with AsyncSessionLocal() as session:
                result = await session.execute(select(BotSettings).where(BotSettings.id == 1))
                settings = result.scalar_one_or_none()
                if settings and settings.paper_trading_enabled:
                    self.paper_balance = settings.paper_balance
                    logger.info("Loaded initial paper balance from DB: $%.2f", self.paper_balance)
        except Exception as exc:
            logger.warning("Failed to load initial balance from DB: %s — using default $10,000", exc)
        
        # Load all-time realized PnL (cumulative from all closed trades)
        try:
            from sqlalchemy import func as _sa_func
            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    select(_sa_func.sum(TradeRecord.profit_loss)).where(
                        TradeRecord.status == "closed",
                        TradeRecord.is_paper_trade == self.paper_trading,
                    )
                )
                self.total_realized_pnl = float(result.scalar() or 0.0)
                logger.info("Loaded all-time realized PnL from DB: $%.2f", self.total_realized_pnl)
        except Exception as exc:
            logger.warning("Failed to load realized PnL from DB: %s — using default $0.00", exc)

    async def initialize_exchange(self, exchange_name: str, api_key: str = "", api_secret: str = ""):
        await asyncio.sleep(0)
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
        except Exception:
            logger.exception("Exchange init failed")
            return False

    def get_var_report(self) -> dict:
        if self.last_var_report and not self.active_positions:
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

    def _get_data_exchange(self) -> Optional[ccxt.Exchange]:
        """Returns an exchange usable for market data. Prefers the authenticated
        exchange; falls back to a public keyless Binance client so paper trading
        analyses REAL candles instead of simulated random walks."""
        if self.exchange is not None:
            return self.exchange
        if self._public_data_exchange is None:
            try:
                self._public_data_exchange = ccxt.binance({
                    "enableRateLimit": True,
                    "options": {"defaultType": "spot"},
                })
                logger.info("Public market-data exchange initialized (paper mode uses real candles)")
            except Exception as exc:
                logger.warning("Could not create public data exchange: %s", exc)
                return None
        return self._public_data_exchange

    async def _fetch_ohlcv(self, symbol: str) -> Optional[pd.DataFrame]:
        cache_key = f"{symbol}_{self._active_timeframe}"
        try:
            data_exchange = self._get_data_exchange()
            if data_exchange is None:
                return self._generate_simulated_ohlcv(symbol)
            cached = self.ohlcv_cache.get(cache_key)
            last_fetch = self._ohlcv_cache_time.get(cache_key, 0)
            if cached is not None and (time.time() - last_fetch) < self._loop_interval:
                return cached
            raw_data = await data_exchange.fetch_ohlcv(
                symbol, timeframe=self._active_timeframe, limit=OHLCV_LIMIT)
            if not raw_data:
                return None
            dataframe = pd.DataFrame(raw_data, columns=["timestamp", "open", "high", "low", "close", "volume"])
            dataframe["timestamp"] = pd.to_datetime(dataframe["timestamp"], unit="ms")
            self.ohlcv_cache[cache_key] = dataframe
            self._ohlcv_cache_time[cache_key] = time.time()
            return dataframe
        except Exception as exc:
            logger.warning("OHLCV fetch failed for %s: %s", symbol, exc)
            return self._generate_simulated_ohlcv(symbol)

    def _generate_simulated_ohlcv(self, symbol: str) -> pd.DataFrame:
        import numpy as np
        fallback_prices = {
            BTC_USDT: 65000.0, ETH_USDT: 3500.0, "BNB/USDT": 600.0,
            "SOL/USDT": 180.0, "DOGE/USDT": 0.15, "XRP/USDT": 0.60,
            "MATIC/USDT": 0.85, "AVAX/USDT": 38.0,
        }
        # HFT mode: higher volatility on 1m bars to generate realistic signal frequency
        volatility = 0.003 if self.hft_mode else 0.008
        candle_freq = "1min" if self.hft_mode else "5min"
        # Prefer live price from background refresh; fall back to hardcoded defaults
        base = self.market_prices.get(symbol, fallback_prices.get(symbol, 100.0))
        cache_key = f"{symbol}_{self._active_timeframe}"
        cached = self.ohlcv_cache.get(cache_key)

        candle_seconds = 60 if self.hft_mode else 300
        if cached is not None and len(cached) >= OHLCV_LIMIT:
            now = time.time()
            if now - self._sim_last_candle_time.get(cache_key, 0) < candle_seconds:
                return cached
            last_close = cached["close"].iloc[-1]
            new_row = self._simulate_next_candle(last_close)
            updated = pd.concat([cached.iloc[1:], pd.DataFrame([new_row])], ignore_index=True)
            self.ohlcv_cache[cache_key] = updated
            self._sim_last_candle_time[cache_key] = now
            return updated

        rng = np.random.default_rng(hash(symbol) % (2**31))
        closes = [base]
        for _ in range(OHLCV_LIMIT - 1):
            change = rng.normal(0, volatility)
            closes.append(closes[-1] * (1 + change))

        timestamps = pd.date_range(end=datetime.now(timezone.utc), periods=OHLCV_LIMIT, freq=candle_freq)
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
        rng = np.random.default_rng(42)
        volatility = 0.002 if self.hft_mode else 0.006
        change = rng.normal(0, volatility)
        new_close = last_close * (1 + change)
        spread = new_close * (0.0005 if self.hft_mode else 0.002)
        return {
            "timestamp": datetime.now(timezone.utc),
            "open": last_close,
            "high": new_close + abs(rng.normal(0, spread)),
            "low": new_close - abs(rng.normal(0, spread)),
            "close": new_close,
            "volume": abs(rng.normal(800 if self.hft_mode else 1000, 200)),
        }

    async def _fetch_current_price(self, symbol: str) -> Optional[float]:
        # Prefer live market price from the background refresh loop — the OHLCV
        # cache close can be stale or (offline fallback) simulated, which caused
        # paper TWAP fills to execute at prices desynced from the real market
        live_price = self.market_prices.get(symbol)
        if live_price and live_price > 0:
            return float(live_price)
        cache_key = f"{symbol}_{self._active_timeframe}"
        cached_df = self.ohlcv_cache.get(cache_key)
        if cached_df is not None:
            return float(cached_df["close"].iloc[-1])
        try:
            data_exchange = self._get_data_exchange()
            if data_exchange:
                ticker = await data_exchange.fetch_ticker(symbol)
                return ticker["last"]
        except Exception:
            pass
        return None

    def _compute_signals_cpu_bound(self, symbol: str, ohlcv_data: pd.DataFrame) -> tuple:
        """CPU-bound signal computation: regime analysis, strategy signals, ensemble aggregation.
        Runs in thread pool to avoid blocking the main event loop."""
        regime_analysis = self.regime_detector.analyze(ohlcv_data)
        regime_weights = self.regime_detector.get_strategy_weights(regime_analysis)
        combined_weights = self.performance_tracker.get_combined_weights(regime_weights)
        raw_signals = []
        for strategy_name in self.active_strategy_names:
            if strategy_name == "pairs":
                continue  # Pairs needs two dataframes — handled by _run_pairs_signal
            strategy = self._strategy_registry.get(strategy_name)
            if strategy and strategy.enabled:
                try:
                    sig = strategy.compute_signal(symbol, ohlcv_data)
                    if sig:
                        raw_signals.append(sig)
                except Exception:
                    logger.exception("Strategy %s error on %s", strategy_name, symbol)
        if not raw_signals:
            return None, None, None
        ensemble_signal = self.signal_ensemble.aggregate(
            symbol=symbol, raw_signals=raw_signals,
            strategy_weights=combined_weights, regime_analysis=regime_analysis,
        )
        return ensemble_signal, regime_analysis, len(raw_signals)

    async def _run_ensemble_for_symbol(self, symbol: str):
        ohlcv_data = await self._fetch_ohlcv(symbol)
        if ohlcv_data is None:
            return None
        # The background _live_price_refresh_loop (main.py) already keeps
        # market_prices fresh every 3s from live tickers, regardless of
        # paper/live mode — only fall back to the (up to _loop_interval-old)
        # OHLCV candle close if no live ticker price has arrived yet, so we
        # never regress a fresh price back to a stale one.
        existing_price = self.market_prices.get(symbol)
        if not existing_price or existing_price <= 0:
            self.market_prices[symbol] = float(ohlcv_data["close"].iloc[-1])
        # Offload CPU-bound work (regime analysis, strategy computations, ensemble aggregation)
        # to thread pool to prevent blocking the main event loop
        loop = asyncio.get_running_loop()
        ensemble_signal, regime_analysis, signal_count = await loop.run_in_executor(
            self._cpu_executor, self._compute_signals_cpu_bound, symbol, ohlcv_data
        )
        if ensemble_signal is None:
            return None
        self.current_regimes[symbol] = regime_analysis.regime.value
        self.total_signals_today += signal_count
        if ensemble_signal:
            logger.info("%s Ensemble %s %s conf=%.3f regime=%s agreed=%s",
                "[HFT]" if self.hft_mode else "",
                ensemble_signal.direction, symbol, ensemble_signal.final_confidence,
                regime_analysis.regime.value, ensemble_signal.agreeing_strategies)
        return ensemble_signal

    async def _fetch_and_apply_sentiment(self, es: EnsembleSignal) -> float | None:
        """Fetch sentiment and return confidence adjustment, or None if blocked."""
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
                return None
            return self.sentiment_filter.get_confidence_adjustment(sentiment, es.direction)
        except Exception:
            return 1.0

    async def _fetch_and_apply_funding(
        self, es: EnsembleSignal
    ) -> tuple[float | None, Optional[object]]:
        """Fetch funding rate and return (confidence adjustment, reading), or (None, None) if blocked."""
        try:
            funding_reading = await self.funding_rate_signal.get_funding_rate(es.symbol)
            if not self.funding_rate_signal.is_signal_aligned_with_funding(es.direction, funding_reading):
                logger.info("Funding rate blocked %s %s: rate=%.4f%% bias=%s",
                            es.direction, es.symbol, funding_reading.funding_rate * 100, funding_reading.signal_bias)
                return None, None
            adj = self.funding_rate_signal.get_confidence_adjustment(es.direction, funding_reading)
            return adj, funding_reading
        except Exception:
            return 1.0, None

    async def _prepare_entry_signal(self, es: EnsembleSignal) -> Optional[EnsembleSignal]:
        """Handle reversals, side checks and paper price snapping. Returns None if entry should abort."""
        existing_pos = self.active_positions.get(es.symbol)
        if existing_pos is not None:
            if existing_pos.side != es.direction:
                logger.info("Reversal %s: closing existing %s before opening %s",
                            es.symbol, existing_pos.side, es.direction)
                await self._close_position(es.symbol, es.weighted_entry_price, "reversal")
                if es.symbol in self.active_positions:
                    logger.warning("Reversal aborted for %s: close failed, original position still active", es.symbol)
                    return None
            else:
                return None
        if not self.paper_trading and self.exchange is not None and es.direction == "SELL":
            logger.warning("Blocked SELL %s: spot exchange cannot open short positions", es.symbol)
            return None
        if self.paper_trading:
            live_px = self.market_prices.get(es.symbol)
            if live_px and live_px > 0 and es.weighted_entry_price > 0:
                scale = live_px / es.weighted_entry_price
                return EnsembleSignal(
                    symbol=es.symbol, direction=es.direction,
                    composite_confidence=es.composite_confidence,
                    agreeing_strategies=es.agreeing_strategies,
                    disagreeing_strategies=es.disagreeing_strategies,
                    weighted_entry_price=live_px,
                    suggested_stop_loss=round(es.suggested_stop_loss * scale, 8),
                    suggested_take_profit=round(es.suggested_take_profit * scale, 8),
                    regime=es.regime, regime_boost=es.regime_boost,
                    raw_signals=es.raw_signals,
                )
        return es

    def _build_entry_features(
        self,
        es: EnsembleSignal,
        adjusted_confidence: float,
        funding_reading: Optional[object],
        portfolio_value: float,
        best_kelly: float,
        open_count: int,
    ) -> dict:
        return {
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
            "open_positions_at_entry": open_count,
            "hft_mode": self.hft_mode,
        }

    def _apply_ml_entry_filter(
        self,
        ml_features: dict,
        es: EnsembleSignal,
        adjusted_confidence: float,
        forced_quantity: float | None,
    ) -> float | None:
        """Apply ML win-probability gate. Returns new confidence or None if blocked."""
        if forced_quantity is not None:
            return adjusted_confidence
        ml_probability = self.ml_filter.score(ml_features)
        if ml_probability is None:
            return adjusted_confidence
        ml_features["ml_win_probability"] = round(ml_probability, 4)
        if ml_probability < self.ml_filter.min_win_probability:
            logger.info("ML filter blocked %s %s: win probability %.3f < %.3f",
                        es.direction, es.symbol, ml_probability, self.ml_filter.min_win_probability)
            return None
        return min(adjusted_confidence * (0.75 + ml_probability * 0.5), 1.0)

    def _size_entry(
        self,
        es: EnsembleSignal,
        adjusted_confidence: float,
        forced_quantity: float | None,
        best_kelly: float,
        portfolio_value: float,
    ) -> Optional[object]:
        """Run risk sizing and exchange precision checks. Returns sizing result or None if rejected."""
        open_symbols = self.active_positions.keys()
        if forced_quantity is not None:
            sizing = self.risk_manager.calculate_position_size(
                portfolio_value=portfolio_value, entry_price=es.weighted_entry_price,
                stop_loss_price=es.suggested_stop_loss, signal_confidence=adjusted_confidence,
                open_positions_count=len(self.active_positions), open_symbols=open_symbols,
                symbol=es.symbol, side=es.direction, kelly_fraction=best_kelly,
            )
            sizing.quantity = forced_quantity
            sizing.position_value = forced_quantity * es.weighted_entry_price
            sizing.allowed = True
        else:
            sizing = self.risk_manager.calculate_position_size(
                portfolio_value=portfolio_value, entry_price=es.weighted_entry_price,
                stop_loss_price=es.suggested_stop_loss, signal_confidence=adjusted_confidence,
                open_positions_count=len(self.active_positions), open_symbols=open_symbols,
                symbol=es.symbol, side=es.direction, kelly_fraction=best_kelly,
                take_profit_price=es.suggested_take_profit,
            )
        if not sizing.allowed:
            logger.info("Rejected %s: %s", es.symbol, sizing.rejection_reason)
            return None
        if not self.paper_trading and self.exchange is not None:
            try:
                sizing.quantity = float(self.exchange.amount_to_precision(es.symbol, sizing.quantity))
                if sizing.quantity <= 0:
                    logger.warning("Rejected %s: quantity rounds to zero after exchange precision", es.symbol)
                    return None
            except Exception:
                pass
            post_precision_value = sizing.quantity * es.weighted_entry_price
            if post_precision_value < 5.0:
                logger.warning("Rejected %s: post-precision value $%.2f below MIN_NOTIONAL $5.00",
                               es.symbol, post_precision_value)
                return None
        return sizing

    async def _execute_twap_entry(
        self, es: EnsembleSignal, sizing: object, order_side: str
    ) -> Optional[tuple[float, float]]:
        twap_order = self.twap_executor.create_order(
            es.symbol, order_side, sizing.quantity,
            exchange=self.exchange, price=es.weighted_entry_price
        )
        twap_order = await self.twap_executor.execute_order(
            twap_order, self._fetch_current_price, exchange=self.exchange
        )
        actual_price = twap_order.avg_fill_price if twap_order.avg_fill_price is not None else es.weighted_entry_price
        fill_quantity = twap_order.total_filled if twap_order.total_filled is not None else sizing.quantity
        if fill_quantity == 0:
            logger.error("TWAP entry zero-filled for %s — aborting position open", es.symbol)
            return None
        return fill_quantity, actual_price

    async def _execute_market_entry(
        self, es: EnsembleSignal, sizing: object, order_side: str
    ) -> Optional[tuple[float, float]]:
        order = await self.exchange.create_market_order(es.symbol, order_side, sizing.quantity)
        actual_price = float(order.get("average") or order.get("price") or es.weighted_entry_price)
        _raw = order.get("filled")
        _filled = float(_raw) if _raw is not None else sizing.quantity
        if _filled == 0:
            logger.error("Market entry zero-filled for %s — aborting position open", es.symbol)
            return None
        fill_quantity = _filled * (1 - TRADE_FEE_RATE) if es.direction == "BUY" else _filled
        return fill_quantity, actual_price

    async def _execute_live_entry_order(
        self, es: EnsembleSignal, sizing: object
    ) -> Optional[tuple[float, float]]:
        order_side = "buy" if es.direction == "BUY" else "sell"
        portfolio_value_now = self._compute_portfolio_value()
        if sizing.position_value >= portfolio_value_now * TWAP_THRESHOLD_PCT:
            return await self._execute_twap_entry(es, sizing, order_side)
        return await self._execute_market_entry(es, sizing, order_side)

    async def _execute_entry_order(
        self, es: EnsembleSignal, sizing: object
    ) -> Optional[tuple[float, float, EnsembleSignal]]:
        """Execute paper or live entry order. Returns (fill_quantity, actual_price, updated_es)."""
        fill_quantity = sizing.quantity
        if self.paper_trading:
            if self.paper_balance < sizing.position_value:
                return None
            self.paper_balance -= sizing.position_value
            return fill_quantity, es.weighted_entry_price, es
        if self.exchange is None:
            return None
        try:
            fill_quantity, actual_price = await self._execute_live_entry_order(es, sizing)
        except Exception:
            logger.exception("Live order submission failed for %s", es.symbol)
            return None
        logger.info("Live order filled: %s %s qty=%.6f @ %.4f", es.direction, es.symbol, fill_quantity, actual_price)
        updated_es = EnsembleSignal(
            symbol=es.symbol, direction=es.direction,
            composite_confidence=es.composite_confidence,
            agreeing_strategies=es.agreeing_strategies,
            disagreeing_strategies=es.disagreeing_strategies,
            weighted_entry_price=actual_price,
            suggested_stop_loss=es.suggested_stop_loss,
            suggested_take_profit=es.suggested_take_profit,
            regime=es.regime, regime_boost=es.regime_boost,
            raw_signals=es.raw_signals,
        )
        return fill_quantity, actual_price, updated_es

    async def _execute_ensemble_signal(self, es, forced_quantity: float | None = None):
        sentiment_adj = await self._fetch_and_apply_sentiment(es)
        if sentiment_adj is None:
            return
        funding_adj, funding_reading = await self._fetch_and_apply_funding(es)
        if funding_adj is None:
            return
        adjusted_confidence = min(es.final_confidence * sentiment_adj * funding_adj, 1.0)

        es = await self._prepare_entry_signal(es)
        if es is None:
            return

        portfolio_value = self._compute_portfolio_value()
        self.risk_manager.update_peak_portfolio_value(portfolio_value)
        best_kelly = max((self.performance_tracker.get_kelly_fraction(s.strategy_name)
                         for s in es.raw_signals), default=0.02)
        ml_features = self._build_entry_features(
            es, adjusted_confidence, funding_reading, portfolio_value, best_kelly, len(self.active_positions)
        )
        adjusted_confidence = self._apply_ml_entry_filter(ml_features, es, adjusted_confidence, forced_quantity)
        if adjusted_confidence is None:
            return

        sizing = self._size_entry(es, adjusted_confidence, forced_quantity, best_kelly, portfolio_value)
        if sizing is None:
            return

        result = await self._execute_entry_order(es, sizing)
        if result is None:
            return
        fill_quantity, _, es = result

        ml_features["raw_signal_details"] = [s.details for s in es.raw_signals]
        label = " + ".join(es.agreeing_strategies)
        position = ActivePosition(
            trade_id=0, symbol=es.symbol, side=es.direction, strategy=label,
            entry_price=es.weighted_entry_price, quantity=fill_quantity,
            stop_loss=es.suggested_stop_loss, take_profit=es.suggested_take_profit,
        )
        self.active_positions[es.symbol] = position
        self.total_trades_today += 1
        try:
            async with AsyncSessionLocal() as session:
                trade = TradeRecord(
                    symbol=es.symbol, side=es.direction, strategy=label,
                    entry_price=es.weighted_entry_price, quantity=fill_quantity,
                    stop_loss_price=es.suggested_stop_loss, take_profit_price=es.suggested_take_profit,
                    status="open", is_paper_trade=self.paper_trading,
                    notes=f"conf={es.final_confidence:.3f} regime={es.regime.value} boost={es.regime_boost}",
                    signal_features=json.dumps(ml_features),
                )
                session.add(trade)
                await session.commit()
                await session.refresh(trade)
                position.trade_id = trade.id
        except Exception:
            logger.exception("DB write failed for %s entry — position tracked in memory without DB record", es.symbol)
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

    def _get_long_exit_decision(self, position: ActivePosition, current_price: float) -> tuple[bool, str]:
        if current_price <= position.stop_loss:
            return True, "trailing_stop" if position.trailing_stop_activated else "stop_loss"
        if current_price >= position.take_profit:
            return True, "take_profit"
        return False, ""

    def _get_short_exit_decision(self, position: ActivePosition, current_price: float) -> tuple[bool, str]:
        if current_price >= position.stop_loss:
            return True, "trailing_stop" if position.trailing_stop_activated else "stop_loss"
        if current_price <= position.take_profit:
            return True, "take_profit"
        return False, ""

    def _get_exit_decision(self, position: ActivePosition, current_price: float) -> tuple[bool, str]:
        """Return (should_close, reason) for a single position."""
        if position.side == "BUY":
            return self._get_long_exit_decision(position, current_price)
        return self._get_short_exit_decision(position, current_price)

    async def _persist_stop_updates(self, stop_updates: list[tuple[int, float]]):
        """Batch-persist trailing stop changes so restarts load the tight stop."""
        try:
            async with AsyncSessionLocal() as _s:
                for _tid, _new_stop in stop_updates:
                    await _s.execute(
                        update(TradeRecord)
                        .where(TradeRecord.id == _tid)
                        .values(stop_loss_price=_new_stop)
                    )
                await _s.commit()
        except Exception as _exc:
            logger.warning("Failed to persist trailing stop updates: %s", _exc)

    async def _check_exit_conditions(self):
        symbols_to_close = []
        stop_updates: list[tuple[int, float]] = []  # (trade_id, new_stop_loss)
        # Bug #1: snapshot keys to prevent RuntimeError if main loop mutates dict while awaiting
        for symbol, position in tuple(self.active_positions.items()):
            if symbol not in self.active_symbols:
                fetched = await self._fetch_current_price(symbol)
                if fetched:
                    self.market_prices[symbol] = fetched
            current_price = self.market_prices.get(symbol, position.current_price)
            position.current_price = current_price
            _old_stop = position.stop_loss
            self._update_trailing_stop(position, current_price)
            # Bug #4: capture changed stops for batch DB persist
            if position.stop_loss != _old_stop and position.trade_id:
                stop_updates.append((position.trade_id, position.stop_loss))
            should_close, close_reason = self._get_exit_decision(position, current_price)
            if should_close:
                symbols_to_close.append((symbol, current_price, close_reason))
        await self._persist_stop_updates(stop_updates)
        for symbol, exit_price, reason in symbols_to_close:
            await self._close_position(symbol, exit_price, reason)

    def _calculate_close_pnl(self, position: ActivePosition, exit_price: float) -> tuple[float, float, float, float]:
        """Return (pnl, fee, pnl_percent, cost_basis) for a position close at exit_price."""
        if position.side == "BUY":
            pnl = (exit_price - position.entry_price) * position.quantity
        else:
            pnl = (position.entry_price - exit_price) * position.quantity
        fee = (position.entry_price + exit_price) * position.quantity * TRADE_FEE_RATE
        pnl -= fee
        cost_basis = position.entry_price * position.quantity
        pnl_percent = (pnl / cost_basis * 100) if cost_basis > 0 else 0.0
        return pnl, fee, pnl_percent, cost_basis

    def _apply_paper_close(self, position: ActivePosition, exit_price: float, fee: float, pnl: float):
        if position.side == "BUY":
            self.paper_balance += exit_price * position.quantity - fee
        else:
            self.paper_balance += (position.entry_price * position.quantity) + pnl
        self.active_positions.pop(position.symbol)

    async def _sync_partial_fill_qty(self, position: ActivePosition):
        if position.trade_id:
            try:
                async with AsyncSessionLocal() as _s:
                    await _s.execute(
                        update(TradeRecord)
                        .where(TradeRecord.id == position.trade_id)
                        .values(quantity=position.quantity)
                    )
                    await _s.commit()
            except Exception:
                logger.exception("Failed to sync partial fill qty for %s", position.symbol)

    async def _execute_twap_close(
        self, position: ActivePosition, symbol: str, exit_price: float, close_side: str
    ) -> Optional[tuple[float, float]]:
        twap_order = self.twap_executor.create_order(
            symbol, close_side, position.quantity,
            exchange=self.exchange, price=exit_price
        )
        twap_order = await self.twap_executor.execute_order(
            twap_order, self._fetch_current_price, exchange=self.exchange
        )
        exit_price = twap_order.avg_fill_price if twap_order.avg_fill_price is not None else exit_price
        closed_qty = twap_order.total_filled if twap_order.total_filled is not None else position.quantity
        if closed_qty == 0:
            logger.error("TWAP exit zero-filled for %s — position left active", symbol)
            return None
        return exit_price, closed_qty

    async def _execute_market_close(
        self, position: ActivePosition, symbol: str, exit_price: float, close_side: str
    ) -> Optional[tuple[float, float]]:
        order = await self.exchange.create_market_order(symbol, close_side, position.quantity)
        exit_price = float(order.get("average") or order.get("price") or exit_price)
        closed_qty = float(order.get("filled") or position.quantity)
        return exit_price, closed_qty

    def _compute_live_close_pnl(
        self, position: ActivePosition, exit_price: float, closed_qty: float, cost_basis: float
    ) -> tuple[float, float]:
        if position.side == "BUY":
            pnl = (exit_price - position.entry_price) * closed_qty
        else:
            pnl = (position.entry_price - exit_price) * closed_qty
        fee = (position.entry_price + exit_price) * closed_qty * TRADE_FEE_RATE
        pnl -= fee
        pnl_percent = (pnl / cost_basis * 100) if cost_basis > 0 else 0.0
        return pnl, pnl_percent

    async def _execute_live_close(
        self, position: ActivePosition, symbol: str, exit_price: float, cost_basis: float
    ) -> Optional[tuple[float, float, float]]:
        """Execute a live close via TWAP or market order. Returns final (exit_price, pnl, pnl_percent) or None."""
        try:
            close_side = "sell" if position.side == "BUY" else "buy"
            close_value = exit_price * position.quantity
            if close_value >= self._compute_portfolio_value() * TWAP_THRESHOLD_PCT:
                result = await self._execute_twap_close(position, symbol, exit_price, close_side)
            else:
                result = await self._execute_market_close(position, symbol, exit_price, close_side)
            if result is None:
                return None
            exit_price, closed_qty = result

            pnl, pnl_percent = self._compute_live_close_pnl(position, exit_price, closed_qty, cost_basis)
            logger.info("Live close filled: %s %s qty=%.6f @ %.4f | PnL=%.2f",
                        close_side, symbol, closed_qty, exit_price, pnl)

            remaining_qty = round(position.quantity - closed_qty, 10)
            if remaining_qty > 0:
                position.quantity = remaining_qty
                logger.warning("Partial fill %s: %.6f closed, %.6f remaining — position stays active",
                               symbol, closed_qty, remaining_qty)
                await self._sync_partial_fill_qty(position)
                return None
            self.active_positions.pop(symbol)
            return exit_price, pnl, pnl_percent
        except Exception:
            logger.exception("Live close order failed for %s — position remains active", symbol)
            return None

    def _update_close_metrics(self, position: ActivePosition, pnl: float, pnl_percent: float):
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

    async def _persist_closed_trade(
        self, position: ActivePosition, symbol: str, exit_price: float,
        pnl: float, pnl_percent: float, reason: str
    ):
        async with AsyncSessionLocal() as session:
            await session.execute(
                update(TradeRecord)
                .where(TradeRecord.id == position.trade_id)
                .values(
                    exit_price=exit_price,
                    profit_loss=round(pnl, 4),
                    profit_loss_percent=round(pnl_percent, 4),
                    status="closed",
                    closed_at=datetime.now(timezone.utc),
                    exit_reason=reason,
                )
            )
            if self.paper_trading:
                await session.execute(
                    update(BotSettings)
                    .where(BotSettings.id == 1)
                    .values(paper_balance=round(self.paper_balance, 8))
                )
            await session.commit()
        logger.info("Closed %s %s at %.4f | PnL: %.2f (%.2f%%) | Reason: %s | Trailing: %s",
                    position.side, symbol, exit_price, pnl, pnl_percent, reason, position.trailing_stop_activated)

    async def _close_position(self, symbol: str, exit_price: float, reason: str):
        position = self.active_positions.get(symbol)
        if position is None:
            return

        pnl, fee, pnl_percent, cost_basis = self._calculate_close_pnl(position, exit_price)

        if self.paper_trading:
            self._apply_paper_close(position, exit_price, fee, pnl)
        elif self.exchange is not None:
            live_result = await self._execute_live_close(position, symbol, exit_price, cost_basis)
            if live_result is None:
                return
            exit_price, pnl, pnl_percent = live_result
        else:
            self.active_positions.pop(symbol)

        self._update_close_metrics(position, pnl, pnl_percent)
        await self._persist_closed_trade(position, symbol, exit_price, pnl, pnl_percent, reason)

        self.ml_filter.record_trade_closed()
        if self.ml_filter.retrain_due():
            self.ml_filter._retrain_task = asyncio.create_task(self.ml_filter.retrain_async())

        await self._broadcast("trade_closed", {
            "symbol": symbol, "exit_price": exit_price,
            "pnl": round(pnl, 4), "pnl_percent": round(pnl_percent, 4),
            "reason": reason,
        })

    def _compute_pairs_signal_cpu_bound(self, btc_df: pd.DataFrame, eth_df: pd.DataFrame, pairs_strategy) -> tuple:
        """CPU-bound pairs signal computation: strategy signal, regime analysis, hedge leg construction.
        Runs in thread pool to avoid blocking the main event loop."""
        try:
            signal = pairs_strategy.compute_signal_from_pair(BTC_USDT, btc_df, eth_df)
        except Exception:
            logger.exception("Pairs strategy error")
            return None, None, None
        if signal is None:
            return None, None, None
        regime_analysis = self.regime_detector.analyze(btc_df)
        btc_signal = EnsembleSignal(
            symbol=BTC_USDT,
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
        # Build the ETH hedge leg (opposite direction = delta-neutral)
        hedge_dir = "SELL" if signal.signal_type == "BUY" else "BUY"
        eth_price = float(eth_df["close"].iloc[-1])
        sl_pct = abs(signal.price - signal.suggested_stop_loss) / signal.price if signal.price > 0 else 0.02
        tp_pct = abs(signal.suggested_take_profit - signal.price) / signal.price if signal.price > 0 else 0.04
        if hedge_dir == "BUY":
            eth_sl = eth_price * (1 - sl_pct)
            eth_tp = eth_price * (1 + tp_pct)
        else:
            eth_sl = eth_price * (1 + sl_pct)
            eth_tp = eth_price * (1 - tp_pct)
        eth_signal = EnsembleSignal(
            symbol=ETH_USDT,
            direction=hedge_dir,
            composite_confidence=signal.strength,
            agreeing_strategies=["Statistical Arbitrage"],
            disagreeing_strategies=[],
            weighted_entry_price=round(eth_price, 8),
            suggested_stop_loss=round(eth_sl, 8),
            suggested_take_profit=round(eth_tp, 8),
            regime=regime_analysis.regime,
            regime_boost=1.0,
            raw_signals=[signal],
        )
        return btc_signal, eth_signal, regime_analysis

    async def _run_pairs_signal(self) -> list["EnsembleSignal"]:
        """Runs Statistical Arbitrage on the BTC/ETH spread.
        Returns [btc_signal, eth_hedge_signal] for a delta-neutral pairs trade.
        The hedge leg direction is opposite to the primary leg."""
        if "pairs" not in self.active_strategy_names:
            return []
        # Bug #3: delta-neutral pairs trading requires short-selling the hedge leg.
        # Spot exchanges cannot short — disable pairs entirely in live spot mode.
        if not self.paper_trading and self.exchange is not None:
            logger.warning("Pairs trading skipped: short selling not supported on live spot exchange")
            return []
        pairs_strategy = self._strategy_registry.get("pairs")
        if not pairs_strategy or not pairs_strategy.enabled:
            return []
        btc_sym, eth_sym = BTC_USDT, ETH_USDT
        if btc_sym not in self.active_symbols or eth_sym not in self.active_symbols:
            return []
        _btc_cached = self.ohlcv_cache.get(f"{btc_sym}_{self._active_timeframe}")
        btc_df = _btc_cached if _btc_cached is not None else await self._fetch_ohlcv(btc_sym)
        _eth_cached = self.ohlcv_cache.get(f"{eth_sym}_{self._active_timeframe}")
        eth_df = _eth_cached if _eth_cached is not None else await self._fetch_ohlcv(eth_sym)
        if btc_df is None or eth_df is None:
            return []
        # Only update market_prices from OHLCV in live mode
        # In paper mode, preserve live prices from background refresh loop
        if not self.paper_trading:
            self.market_prices[btc_sym] = float(btc_df["close"].iloc[-1])
            self.market_prices[eth_sym] = float(eth_df["close"].iloc[-1])
        # Offload CPU-bound work (strategy signal, regime analysis, hedge leg construction)
        # to thread pool to prevent blocking the main event loop
        loop = asyncio.get_running_loop()
        btc_signal, eth_signal, regime_analysis = await loop.run_in_executor(
            self._cpu_executor, self._compute_pairs_signal_cpu_bound, btc_df, eth_df, pairs_strategy
        )
        if btc_signal is None:
            return []
        self.total_signals_today += 1
        self.current_regimes[btc_sym] = regime_analysis.regime.value
        return [btc_signal, eth_signal]

    def _compute_portfolio_value(self) -> float:
        position_value = 0.0
        for pos in self.active_positions.values():
            if pos.side == "BUY":
                # Long: asset is worth current market price
                position_value += pos.current_price * pos.quantity
            else:
                # Short: margin (entry_price*qty) locked, plus unrealized PnL
                position_value += pos.entry_price * pos.quantity + pos.unrealized_pnl
        balance = self.paper_balance if self.paper_trading else self._cached_live_balance
        return balance + position_value

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
                raise
            except Exception:
                logger.exception("Price stream error")
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

    async def _refresh_live_balance(self):
        """Fetch real USDT free balance from exchange for accurate live portfolio valuation."""
        try:
            account = await self.exchange.fetch_balance()
            usdt_free = account.get("USDT", {}).get("free")
            # Bug #4: accept 0.0 (fully invested) — only skip None/missing values
            if usdt_free is not None:
                self._cached_live_balance = float(usdt_free)
        except Exception as exc:
            logger.warning("Failed to refresh live balance: %s", exc)

    async def _process_ensemble_results(self, results: list):
        for result in results:
            if result and not isinstance(result, Exception):
                await self._execute_ensemble_signal(result)

    def _pairs_hedges_sizable(self, sizing, hedges: list) -> bool:
        return all(
            h.weighted_entry_price > 0
            and (sizing.position_value / h.weighted_entry_price) * h.weighted_entry_price >= 5.0
            for h in hedges
        )

    async def _execute_pairs_hedges(self, primary_pos: ActivePosition, hedges: list):
        for hedge in hedges:
            forced_qty = None
            if hedge.weighted_entry_price > 0:
                forced_qty = round(
                    primary_pos.quantity * primary_pos.entry_price
                    / hedge.weighted_entry_price, 8
                )
            await self._execute_ensemble_signal(hedge, forced_quantity=forced_qty)

    async def _execute_pairs_trade(self, pairs_legs: list):
        available = self.risk_manager.max_concurrent_positions - len(self.active_positions)
        if available < len(pairs_legs):
            logger.info("Pairs trade skipped: need %d free slots, only %d available",
                        len(pairs_legs), available)
            return
        primary, *hedges = pairs_legs
        portfolio_value = self._compute_portfolio_value()
        self.risk_manager.update_peak_portfolio_value(portfolio_value)
        best_kelly = max((self.performance_tracker.get_kelly_fraction(s.strategy_name)
                          for s in primary.raw_signals), default=0.02)
        pre_sizing = self.risk_manager.calculate_position_size(
            portfolio_value=portfolio_value, entry_price=primary.weighted_entry_price,
            stop_loss_price=primary.suggested_stop_loss,
            signal_confidence=primary.final_confidence,
            open_positions_count=len(self.active_positions),
            open_symbols=self.active_positions.keys(),
            symbol=primary.symbol, side=primary.direction, kelly_fraction=best_kelly,
        )
        if not pre_sizing.allowed or not self._pairs_hedges_sizable(pre_sizing, hedges):
            logger.debug("Pairs aborted: pre-validation failed — no legs executed")
            return
        await self._execute_ensemble_signal(primary)
        primary_pos = self.active_positions.get(primary.symbol)
        if primary_pos is None:
            logger.info("Pairs: primary leg %s not opened — all hedge legs skipped", primary.symbol)
            return
        await self._execute_pairs_hedges(primary_pos, hedges)

    async def _broadcast_portfolio_update(self):
        portfolio_value = self._compute_portfolio_value()
        await self._broadcast("price_update", {
            "prices": self.market_prices,
            "portfolio_value": portfolio_value,
            "available_balance": self.paper_balance,
            "unrealized_pnl": sum(p.unrealized_pnl for p in self.active_positions.values()),
            "active_positions_count": len(self.active_positions),
            "regimes": self.current_regimes,
            "hft_mode": self.hft_mode,
        })

    async def _maybe_refresh_live_balance(self):
        if not self.paper_trading and self.exchange is not None:
            await self._refresh_live_balance()

    async def _trading_loop(self):
        mode_label = "HFT 1m" if self.hft_mode else "Standard 5m"
        logger.info("Trading loop started [%s | %ds interval]", mode_label, self._loop_interval)
        while self.is_running:
            try:
                self.last_tick = datetime.now(timezone.utc)
                await self._maybe_refresh_live_balance()

                # PARALLEL: process all symbols simultaneously — not sequentially
                results = await asyncio.gather(
                    *[self._run_ensemble_for_symbol(sym) for sym in self.active_symbols],
                    return_exceptions=True
                )
                await self._process_ensemble_results(results)

                pairs_legs = await self._run_pairs_signal()
                if pairs_legs:
                    await self._execute_pairs_trade(pairs_legs)

                # In standard mode, also check exits in this loop (HFT has dedicated loop)
                if not self.hft_mode:
                    await self._check_exit_conditions()

                await self._broadcast_portfolio_update()
                await asyncio.sleep(self._loop_interval)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Trading loop error")
                await asyncio.sleep(2 if self.hft_mode else 5)

        logger.info("Trading loop stopped")

    async def start(self, settings: dict):
        if self.is_running:
            return
        # Clear all in-memory state from any previous run to prevent stale data
        self.active_positions.clear()
        self.recent_signals.clear()
        self.total_signals_today = 0
        self.total_trades_today = 0
        self.total_realized_pnl = 0.0
        self.current_regimes.clear()
        self.paper_trading = settings.get("paper_trading_enabled", True)
        self.paper_balance = settings.get("paper_balance", 10000.0)
        # Bug #5: restore all-time realized PnL from DB so dashboard doesn't wipe on restart
        try:
            from sqlalchemy import func as _sa_func
            async with AsyncSessionLocal() as _pnl_s:
                _r = await _pnl_s.execute(
                    select(_sa_func.sum(TradeRecord.profit_loss)).where(
                        TradeRecord.status == "closed",
                        TradeRecord.is_paper_trade == self.paper_trading,
                    )
                )
                self.total_realized_pnl = float(_r.scalar() or 0.0)
                logger.info("Restored all-time realized PnL: %.2f", self.total_realized_pnl)
        except Exception as _exc:
            logger.warning("Could not restore realized PnL on start: %s", _exc)
        # Reload any open positions left from a previous session (crash/restart recovery)
        try:
            async with AsyncSessionLocal() as _s:
                # Bug #3: filter by is_paper_trade to prevent paper positions
                # from loading into a live session and triggering real orders
                _r = await _s.execute(
                    select(TradeRecord).where(
                        TradeRecord.status == "open",
                        TradeRecord.is_paper_trade == self.paper_trading,
                    )
                )
                for _t in _r.scalars().all():
                    _pos = ActivePosition(
                        trade_id=_t.id, symbol=_t.symbol, side=_t.side,
                        strategy=_t.strategy, entry_price=_t.entry_price,
                        quantity=_t.quantity, stop_loss=_t.stop_loss_price,
                        take_profit=_t.take_profit_price,
                    )
                    self.active_positions[_t.symbol] = _pos
            if self.active_positions:
                logger.info("Reloaded %d open position(s) from previous session", len(self.active_positions))
        except Exception as _exc:
            logger.warning("Could not reload open positions on start: %s", _exc)
        self.active_symbols = settings.get("active_symbols", [BTC_USDT, ETH_USDT])
        self.active_strategy_names = settings.get("active_strategies", ["rsi", "macd"])
        self.hft_mode = settings.get("hft_mode", False)
        # Clear OHLCV cache when mode changes, but preserve live market prices
        # so simulated OHLCV uses current prices as base instead of outdated fallbacks
        # Note: market_prices is kept current by the background _live_price_refresh_loop in main.py
        self.ohlcv_cache.clear()
        self.risk_manager = RiskManager(
            max_portfolio_risk_percent=settings.get("max_portfolio_risk_percent", 2.0),
            max_drawdown_percent=settings.get("max_drawdown_percent", 10.0),
            max_concurrent_positions=settings.get("max_concurrent_positions", 5),
        )
        self.signal_ensemble = SignalEnsemble(
            minimum_agreement_count=1 if self.hft_mode else 2,
            minimum_composite_confidence=0.45 if self.hft_mode else 0.55,
        )
        # Initialize thread pool for CPU-bound work (strategy computations, regime analysis, ML inference)
        # This prevents blocking the main event loop during trading iterations
        self._cpu_executor = ThreadPoolExecutor(max_workers=4)
        self.is_running = True
        self.start_time = time.time()
        self._main_loop_task = asyncio.create_task(self._trading_loop())
        if self.hft_mode:
            self._price_stream_task = asyncio.create_task(self._price_stream_loop())
        logger.info("Engine started [%s | paper=%s | symbols=%s | strategies=%s]",
                    "HFT" if self.hft_mode else "Standard",
                    self.paper_trading, self.active_symbols, self.active_strategy_names)

    async def _cancel_engine_tasks(self):
        if self._price_stream_task:
            self._price_stream_task.cancel()
            await self._price_stream_task
        if self._main_loop_task:
            self._main_loop_task.cancel()
            await self._main_loop_task

    async def _close_live_position_on_stop(self, pos: ActivePosition, exchange_closed: set[str]):
        try:
            side = "sell" if pos.side == "BUY" else "buy"
            close_price = self.market_prices.get(pos.symbol, pos.entry_price)
            close_value = pos.quantity * close_price
            if close_value >= self._compute_portfolio_value() * TWAP_THRESHOLD_PCT:
                twap = self.twap_executor.create_order(
                    pos.symbol, side, pos.quantity,
                    exchange=self.exchange, price=close_price
                )
                twap = await self.twap_executor.execute_order(
                    twap, self._fetch_current_price, exchange=self.exchange
                )
                fill_price = twap.avg_fill_price or self.market_prices.get(pos.symbol, pos.current_price)
            else:
                order = await self.exchange.create_market_order(pos.symbol, side, pos.quantity)
                fill_price = float(order.get("average") or order.get("price")
                                or self.market_prices.get(pos.symbol, pos.current_price))
            self.market_prices[pos.symbol] = fill_price
            exchange_closed.add(pos.symbol)
            logger.info("Stop: live close filled: %s %s @ %.4f", side, pos.symbol, fill_price)
        except Exception:
            logger.exception("Stop: failed to close live position %s — left open in DB", pos.symbol)

    async def _close_all_positions_on_stop(self) -> set[str]:
        exchange_closed: set[str] = set()
        if not self.paper_trading and self.exchange is not None:
            for pos in tuple(self.active_positions.values()):
                await self._close_live_position_on_stop(pos, exchange_closed)
        return exchange_closed

    def _compute_stop_position_pnl(self, pos: ActivePosition, exit_px: float) -> tuple[float, float, float]:
        if pos.side == "BUY":
            pnl = (exit_px - pos.entry_price) * pos.quantity
        else:
            pnl = (pos.entry_price - exit_px) * pos.quantity
        fee = (pos.entry_price + exit_px) * pos.quantity * TRADE_FEE_RATE
        pnl -= fee
        cost = pos.entry_price * pos.quantity
        pnl_pct = (pnl / cost * 100) if cost > 0 else 0.0
        return pnl, pnl_pct, fee

    def _refund_paper_balance_for_stop(self, pos: ActivePosition, exit_px: float, fee: float, pnl: float):
        if pos.side == "BUY":
            self.paper_balance += exit_px * pos.quantity - fee
        else:
            self.paper_balance += (pos.entry_price * pos.quantity) + pnl

    async def _persist_stopped_positions(self, exchange_closed: set[str]):
        try:
            async with AsyncSessionLocal() as session:
                for pos in self.active_positions.values():
                    if not self.paper_trading and pos.symbol not in exchange_closed:
                        continue
                    exit_px = self.market_prices.get(pos.symbol, pos.current_price)
                    pnl, pnl_pct, fee = self._compute_stop_position_pnl(pos, exit_px)
                    if self.paper_trading:
                        self._refund_paper_balance_for_stop(pos, exit_px, fee, pnl)
                    await session.execute(
                        update(TradeRecord)
                        .where(TradeRecord.id == pos.trade_id)
                        .values(
                            exit_price=round(exit_px, 8),
                            profit_loss=round(pnl, 4),
                            profit_loss_percent=round(pnl_pct, 4),
                            status="closed",
                            closed_at=datetime.now(timezone.utc),
                            exit_reason="bot_stopped",
                        )
                    )
                if self.paper_trading:
                    await session.execute(
                        update(BotSettings)
                        .where(BotSettings.id == 1)
                        .values(paper_balance=round(self.paper_balance, 8))
                    )
                await session.commit()
            logger.info("Closed %d open position(s) on engine stop", len(self.active_positions))
        except Exception:
            logger.exception("Failed to close open positions on stop")

    async def _cleanup_exchanges(self):
        if self.exchange:
            await self.exchange.close()
        if self._public_data_exchange is not None:
            try:
                await self._public_data_exchange.close()
            except Exception:
                pass
            self._public_data_exchange = None
        if self._cpu_executor is not None:
            self._cpu_executor.shutdown(wait=True)
            self._cpu_executor = None
        await close_public_futures_exchange()

    async def stop(self):
        self.is_running = False
        await self._cancel_engine_tasks()
        if self.active_positions:
            exchange_closed = await self._close_all_positions_on_stop()
            await self._persist_stopped_positions(exchange_closed)
            self.active_positions.clear()
        self.recent_signals.clear()
        await self._cleanup_exchanges()
        logger.info("Trading engine stopped")

    def apply_settings(self, settings: dict) -> dict:
        """Hot-reload settings that are safe to change while the bot is running.
        Returns a dict indicating which settings were applied and which need a restart."""
        applied = []
        needs_restart = []

        # --- Safe to hot-reload: risk parameters ---
        new_risk_pct = settings.get("max_portfolio_risk_percent", self.risk_manager.max_portfolio_risk_percent)
        new_dd_pct = settings.get("max_drawdown_percent", self.risk_manager.max_drawdown_percent)
        new_max_pos = settings.get("max_concurrent_positions", self.risk_manager.max_concurrent_positions)
        if (new_risk_pct != self.risk_manager.max_portfolio_risk_percent
                or new_dd_pct != self.risk_manager.max_drawdown_percent
                or new_max_pos != self.risk_manager.max_concurrent_positions):
            self.risk_manager.max_portfolio_risk_percent = new_risk_pct
            self.risk_manager.max_drawdown_percent = new_dd_pct
            self.risk_manager.max_concurrent_positions = new_max_pos
            applied.append("risk_parameters")

        # --- Safe to hot-reload: active strategies ---
        new_strategies = settings.get("active_strategies", self.active_strategy_names)
        if set(new_strategies) != set(self.active_strategy_names):
            self.active_strategy_names = new_strategies
            applied.append("active_strategies")

        # --- Safe to hot-reload: active symbols (only add new ones; keep existing positions) ---
        new_symbols = settings.get("active_symbols", self.active_symbols)
        if set(new_symbols) != set(self.active_symbols):
            # Bug #4: clear OHLCV cache for removed symbols so _fetch_current_price
            # falls back to live fetch_ticker instead of returning stale cached price
            removed = set(self.active_symbols) - set(new_symbols)
            for sym in removed:
                for cache_key in [k for k in self.ohlcv_cache if k.startswith(f"{sym}_")]:
                    self.ohlcv_cache.pop(cache_key, None)
                    self._ohlcv_cache_time.pop(cache_key, None)
            self.active_symbols = new_symbols
            applied.append("active_symbols")

        # --- NOT safe while running: mode changes ---
        if settings.get("paper_trading_enabled", self.paper_trading) != self.paper_trading:
            needs_restart.append("paper_trading_enabled")
        if settings.get("hft_mode", self.hft_mode) != self.hft_mode:
            needs_restart.append("hft_mode")
        if settings.get("paper_balance", self.paper_balance) != self.paper_balance and self.paper_trading:
            needs_restart.append("paper_balance")

        if applied:
            logger.info("Hot-reloaded settings: %s", ", ".join(applied))

        return {"applied": applied, "needs_restart": needs_restart}

    def get_status(self) -> dict:
        return {
            "is_running": self.is_running,
            "paper_trading": self.paper_trading,
            "active_positions": len(self.active_positions),
            "total_signals_today": self.total_signals_today,
            "trades_today": self.total_trades_today,
            "uptime_seconds": time.time() - self.start_time if self.is_running else 0,
            "last_tick": self.last_tick.isoformat() if self.last_tick else None,
            "circuit_breaker_active": self.risk_manager.circuit_breaker_active if self.risk_manager else False,
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

    async def close_position_by_symbol(self, symbol: str, reason: str = "manual_close"):
        """Manually close a position at current market price"""
        position = self.active_positions.get(symbol)
        if position is None:
            logger.warning("Cannot close position %s - not found in active positions", symbol)
            return
        exit_price = self.market_prices.get(symbol, position.current_price)
        await self._close_position(symbol, exit_price, reason)
        logger.info("Manually closed position %s at $%.4f (reason: %s)", symbol, exit_price, reason)


trading_engine = TradingEngine()
