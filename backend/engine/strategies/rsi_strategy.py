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
        oversold_threshold: float = 30.0,
        overbought_threshold: float = 70.0,
        stop_loss_percent: float = 2.0,
        take_profit_percent: float = 4.0,
    ):
        super().__init__("RSI Mean Reversion")
        self.rsi_period = rsi_period
        self.oversold_threshold = oversold_threshold
        self.overbought_threshold = overbought_threshold
        self.stop_loss_percent = stop_loss_percent
        self.take_profit_percent = take_profit_percent

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
        rsi_series = self._compute_rsi(closes)

        current_rsi = rsi_series.iloc[-1]
        previous_rsi = rsi_series.iloc[-2]
        current_price = closes.iloc[-1]

        # Oversold: RSI crossing back above oversold threshold = BUY signal
        if previous_rsi <= self.oversold_threshold and current_rsi > self.oversold_threshold:
            signal_strength = min((self.oversold_threshold - previous_rsi) / self.oversold_threshold, 1.0)
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

        # Overbought: RSI crossing back below overbought threshold = SELL signal
        if previous_rsi >= self.overbought_threshold and current_rsi < self.overbought_threshold:
            signal_strength = min((previous_rsi - self.overbought_threshold) / (100 - self.overbought_threshold), 1.0)
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
        return self.rsi_period * 3
