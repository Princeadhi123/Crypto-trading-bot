import pandas as pd
import numpy as np
from typing import Optional
from .base_strategy import BaseStrategy, TradingSignal


class ScalpingStrategy(BaseStrategy):
    """
    High-frequency scalping strategy using EMA crossovers and momentum.
    Uses short EMAs (5/13) with volume confirmation and ATR-based dynamic
    stop-loss / take-profit levels for tight risk control on small moves.
    """

    def __init__(
        self,
        fast_ema_period: int = 5,
        slow_ema_period: int = 13,
        momentum_period: int = 10,
        atr_period: int = 14,
        atr_stop_multiplier: float = 1.5,
        atr_target_multiplier: float = 2.5,
    ):
        super().__init__("EMA Scalping")
        self.fast_ema_period = fast_ema_period
        self.slow_ema_period = slow_ema_period
        self.momentum_period = momentum_period
        self.atr_period = atr_period
        self.atr_stop_multiplier = atr_stop_multiplier
        self.atr_target_multiplier = atr_target_multiplier

    def _compute_atr(self, highs: pd.Series, lows: pd.Series, closes: pd.Series) -> pd.Series:
        previous_closes = closes.shift(1)
        true_range_high = highs - lows
        true_range_prev_close_high = (highs - previous_closes).abs()
        true_range_prev_close_low = (lows - previous_closes).abs()
        true_range = pd.concat([true_range_high, true_range_prev_close_high, true_range_prev_close_low], axis=1).max(axis=1)
        return true_range.ewm(span=self.atr_period, adjust=False).mean()

    def _compute_momentum(self, closes: pd.Series) -> pd.Series:
        return closes / closes.shift(self.momentum_period) - 1

    def compute_signal(self, symbol: str, ohlcv_dataframe: pd.DataFrame) -> Optional[TradingSignal]:
        if len(ohlcv_dataframe) < self.requires_minimum_candles():
            return None

        closes = ohlcv_dataframe["close"]
        highs = ohlcv_dataframe["high"]
        lows = ohlcv_dataframe["low"]
        volumes = ohlcv_dataframe["volume"]

        fast_ema = closes.ewm(span=self.fast_ema_period, adjust=False).mean()
        slow_ema = closes.ewm(span=self.slow_ema_period, adjust=False).mean()
        atr_series = self._compute_atr(highs, lows, closes)
        momentum = self._compute_momentum(closes)

        current_price = closes.iloc[-1]
        current_atr = atr_series.iloc[-1]

        current_fast = fast_ema.iloc[-1]
        previous_fast = fast_ema.iloc[-2]
        current_slow = slow_ema.iloc[-1]
        previous_slow = slow_ema.iloc[-2]
        current_momentum = momentum.iloc[-1]

        avg_volume = volumes.rolling(20).mean().iloc[-1]
        current_volume = volumes.iloc[-1]
        volume_confirmation = current_volume > avg_volume

        # Bullish EMA crossover with positive momentum and volume
        bullish_cross = previous_fast <= previous_slow and current_fast > current_slow
        if bullish_cross and current_momentum > 0 and volume_confirmation:
            signal_strength = min(abs(current_momentum) * 10 + 0.5, 1.0)
            stop_loss = current_price - (self.atr_stop_multiplier * current_atr)
            take_profit = current_price + (self.atr_target_multiplier * current_atr)
            return TradingSignal(
                symbol=symbol,
                strategy_name=self.name,
                signal_type="BUY",
                strength=round(signal_strength, 3),
                price=current_price,
                suggested_stop_loss=round(stop_loss, 8),
                suggested_take_profit=round(take_profit, 8),
                details={
                    "fast_ema": round(current_fast, 4),
                    "slow_ema": round(current_slow, 4),
                    "atr": round(current_atr, 4),
                    "momentum": round(current_momentum, 4),
                    "condition": "bullish_ema_crossover",
                },
            )

        # Bearish EMA crossover with negative momentum and volume
        bearish_cross = previous_fast >= previous_slow and current_fast < current_slow
        if bearish_cross and current_momentum < 0 and volume_confirmation:
            signal_strength = min(abs(current_momentum) * 10 + 0.5, 1.0)
            stop_loss = current_price + (self.atr_stop_multiplier * current_atr)
            take_profit = current_price - (self.atr_target_multiplier * current_atr)
            return TradingSignal(
                symbol=symbol,
                strategy_name=self.name,
                signal_type="SELL",
                strength=round(signal_strength, 3),
                price=current_price,
                suggested_stop_loss=round(stop_loss, 8),
                suggested_take_profit=round(take_profit, 8),
                details={
                    "fast_ema": round(current_fast, 4),
                    "slow_ema": round(current_slow, 4),
                    "atr": round(current_atr, 4),
                    "momentum": round(current_momentum, 4),
                    "condition": "bearish_ema_crossover",
                },
            )

        return None

    def requires_minimum_candles(self) -> int:
        return max(self.slow_ema_period, self.atr_period, self.momentum_period) * 2
