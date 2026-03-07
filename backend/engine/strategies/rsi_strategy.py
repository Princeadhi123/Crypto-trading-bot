import pandas as pd
import numpy as np
from typing import Optional
from .base_strategy import BaseStrategy, TradingSignal


class RsiStrategy(BaseStrategy):
    """
    RSI (Relative Strength Index) mean-reversion strategy.
    Buys on oversold conditions (RSI < oversold_threshold) with bullish confirmation,
    sells on overbought conditions (RSI > overbought_threshold) with bearish confirmation.
    """

    def __init__(
        self,
        rsi_period: int = 14,
        oversold_threshold: float = 25.0,
        overbought_threshold: float = 75.0,
        stop_loss_percent: float = 2.0,
        take_profit_percent: float = 4.0,
        trend_ema_period: int = 50,
        trend_filter_pct: float = 1.0,
        volume_multiplier: float = 1.3,
        rsi_acceleration_min: float = 2.0,
    ):
        super().__init__("RSI Mean Reversion")
        self.rsi_period = rsi_period
        self.oversold_threshold = oversold_threshold
        self.overbought_threshold = overbought_threshold
        self.stop_loss_percent = stop_loss_percent
        self.take_profit_percent = take_profit_percent
        self.trend_ema_period = trend_ema_period
        self.trend_filter_pct = trend_filter_pct
        self.volume_multiplier = volume_multiplier
        self.rsi_acceleration_min = rsi_acceleration_min

    def _compute_rsi(self, closes: pd.Series) -> pd.Series:
        delta = closes.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        average_gain = gain.ewm(com=self.rsi_period - 1, min_periods=self.rsi_period).mean()
        average_loss = loss.ewm(com=self.rsi_period - 1, min_periods=self.rsi_period).mean()
        relative_strength = average_gain / average_loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + relative_strength))
        return rsi.fillna(50)

    def compute_signal(self, symbol: str, ohlcv_dataframe: pd.DataFrame) -> Optional[TradingSignal]:
        if len(ohlcv_dataframe) < self.requires_minimum_candles():
            return None

        closes = ohlcv_dataframe["close"]
        volumes = ohlcv_dataframe["volume"]
        rsi_series = self._compute_rsi(closes)
        trend_ema = closes.ewm(span=self.trend_ema_period, adjust=False).mean()

        current_rsi = rsi_series.iloc[-1]
        previous_rsi = rsi_series.iloc[-2]
        current_price = closes.iloc[-1]
        previous_price = closes.iloc[-2]
        current_trend_ema = trend_ema.iloc[-1]

        avg_volume = volumes.rolling(20).mean().iloc[-1]
        current_volume = volumes.iloc[-1]
        volume_surge = current_volume > avg_volume * self.volume_multiplier

        # Oversold recovery: RSI crosses above oversold threshold
        # 70% WR gate: trend ok + volume surge + price already bouncing + RSI accelerating
        not_in_strong_downtrend = current_price >= current_trend_ema * (1 - self.trend_filter_pct / 100)
        rsi_accelerating_up = (current_rsi - previous_rsi) >= self.rsi_acceleration_min
        price_bouncing_up = current_price > previous_price
        if (previous_rsi <= self.oversold_threshold
                and current_rsi > self.oversold_threshold
                and not_in_strong_downtrend
                and volume_surge
                and price_bouncing_up
                and rsi_accelerating_up):
            signal_strength = min((self.oversold_threshold - previous_rsi) / self.oversold_threshold + 0.4, 1.0)
            return TradingSignal(
                symbol=symbol,
                strategy_name=self.name,
                signal_type="BUY",
                strength=round(signal_strength, 3),
                price=current_price,
                suggested_stop_loss=self.calculate_stop_loss(current_price, "BUY", self.stop_loss_percent),
                suggested_take_profit=self.calculate_take_profit(current_price, "BUY", self.take_profit_percent),
                details={"rsi": round(current_rsi, 2), "previous_rsi": round(previous_rsi, 2), "condition": "oversold_recovery"},
            )

        # Overbought reversal: RSI crosses below overbought threshold
        # 70% WR gate: trend ok + volume surge + price already declining + RSI decelerating
        not_in_strong_uptrend = current_price <= current_trend_ema * (1 + self.trend_filter_pct / 100)
        rsi_accelerating_down = (previous_rsi - current_rsi) >= self.rsi_acceleration_min
        price_declining = current_price < previous_price
        if (previous_rsi >= self.overbought_threshold
                and current_rsi < self.overbought_threshold
                and not_in_strong_uptrend
                and volume_surge
                and price_declining
                and rsi_accelerating_down):
            signal_strength = min((previous_rsi - self.overbought_threshold) / (100 - self.overbought_threshold) + 0.4, 1.0)
            return TradingSignal(
                symbol=symbol,
                strategy_name=self.name,
                signal_type="SELL",
                strength=round(signal_strength, 3),
                price=current_price,
                suggested_stop_loss=self.calculate_stop_loss(current_price, "SELL", self.stop_loss_percent),
                suggested_take_profit=self.calculate_take_profit(current_price, "SELL", self.take_profit_percent),
                details={"rsi": round(current_rsi, 2), "previous_rsi": round(previous_rsi, 2), "condition": "overbought_reversal"},
            )

        return None

    def requires_minimum_candles(self) -> int:
        return max(self.rsi_period * 3, self.trend_ema_period + 10)
