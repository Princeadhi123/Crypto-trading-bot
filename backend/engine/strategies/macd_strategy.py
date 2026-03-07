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
        trend_ema_period: int = 20,
    ):
        super().__init__("MACD Momentum")
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.signal_period = signal_period
        self.stop_loss_percent = stop_loss_percent
        self.take_profit_percent = take_profit_percent
        self.trend_ema_period = trend_ema_period

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
        prior_histogram = histogram.iloc[-3]

        trend_ema = closes.ewm(span=self.trend_ema_period, adjust=False).mean()
        current_trend_ema = trend_ema.iloc[-1]

        # Bullish crossover: MACD crosses above signal line
        # 70% WR gate: zero-line + 2-candle histogram expansion + price above trend EMA
        bullish_crossover = previous_macd <= previous_signal and current_macd > current_signal
        histogram_expanding_2 = current_histogram > previous_histogram > prior_histogram
        macd_above_zero = current_macd > 0
        price_above_trend = current_price > current_trend_ema

        if bullish_crossover and histogram_expanding_2 and macd_above_zero and price_above_trend:
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

        # Bearish crossover: MACD crosses below signal line
        # 70% WR gate: zero-line + 2-candle histogram contraction + price below trend EMA
        bearish_crossover = previous_macd >= previous_signal and current_macd < current_signal
        histogram_contracting_2 = current_histogram < previous_histogram < prior_histogram
        macd_below_zero = current_macd < 0
        price_below_trend = current_price < current_trend_ema

        if bearish_crossover and histogram_contracting_2 and macd_below_zero and price_below_trend:
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
        return max(self.slow_period + self.signal_period + 10, self.trend_ema_period + 10)
