import pandas as pd
from typing import Optional
from .base_strategy import BaseStrategy, TradingSignal


class MacdStrategy(BaseStrategy):
    """
    MACD (Moving Average Convergence Divergence) momentum strategy.
    Generates buy signals on bullish MACD crossovers and sell signals on bearish crossovers.
    Filters signals using the histogram direction for higher confidence.
    """

    def __init__(
        self,
        fast_period: int = 12,
        slow_period: int = 26,
        signal_period: int = 9,
        stop_loss_percent: float = 2.5,
        take_profit_percent: float = 5.0,
    ):
        super().__init__("MACD Momentum")
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.signal_period = signal_period
        self.stop_loss_percent = stop_loss_percent
        self.take_profit_percent = take_profit_percent

    def _compute_macd(self, closes: pd.Series):
        ema_fast = closes.ewm(span=self.fast_period, adjust=False).mean()
        ema_slow = closes.ewm(span=self.slow_period, adjust=False).mean()
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=self.signal_period, adjust=False).mean()
        histogram = macd_line - signal_line
        return macd_line, signal_line, histogram

    def compute_signal(self, symbol: str, ohlcv_dataframe: pd.DataFrame) -> Optional[TradingSignal]:
        if len(ohlcv_dataframe) < self.requires_minimum_candles():
            return None

        closes = ohlcv_dataframe["close"]
        macd_line, signal_line, histogram = self._compute_macd(closes)

        current_price = closes.iloc[-1]
        current_macd = macd_line.iloc[-1]
        previous_macd = macd_line.iloc[-2]
        current_signal = signal_line.iloc[-1]
        previous_signal = signal_line.iloc[-2]
        current_histogram = histogram.iloc[-1]
        previous_histogram = histogram.iloc[-2]

        # Bullish crossover: MACD crosses above signal line with rising histogram
        bullish_crossover = previous_macd <= previous_signal and current_macd > current_signal
        histogram_rising = current_histogram > previous_histogram

        if bullish_crossover and histogram_rising:
            signal_strength = min(abs(current_histogram) / (abs(current_macd) + 1e-10), 1.0)
            return TradingSignal(
                symbol=symbol,
                strategy_name=self.name,
                signal_type="BUY",
                strength=round(signal_strength, 3),
                price=current_price,
                suggested_stop_loss=self.calculate_stop_loss(current_price, "BUY", self.stop_loss_percent),
                suggested_take_profit=self.calculate_take_profit(current_price, "BUY", self.take_profit_percent),
                details={
                    "macd": round(current_macd, 6),
                    "signal": round(current_signal, 6),
                    "histogram": round(current_histogram, 6),
                    "condition": "bullish_crossover",
                },
            )

        # Bearish crossover: MACD crosses below signal line with falling histogram
        bearish_crossover = previous_macd >= previous_signal and current_macd < current_signal
        histogram_falling = current_histogram < previous_histogram

        if bearish_crossover and histogram_falling:
            signal_strength = min(abs(current_histogram) / (abs(current_macd) + 1e-10), 1.0)
            return TradingSignal(
                symbol=symbol,
                strategy_name=self.name,
                signal_type="SELL",
                strength=round(signal_strength, 3),
                price=current_price,
                suggested_stop_loss=self.calculate_stop_loss(current_price, "SELL", self.stop_loss_percent),
                suggested_take_profit=self.calculate_take_profit(current_price, "SELL", self.take_profit_percent),
                details={
                    "macd": round(current_macd, 6),
                    "signal": round(current_signal, 6),
                    "histogram": round(current_histogram, 6),
                    "condition": "bearish_crossover",
                },
            )

        return None

    def requires_minimum_candles(self) -> int:
        return self.slow_period + self.signal_period + 10
