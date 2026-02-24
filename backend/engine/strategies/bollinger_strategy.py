import pandas as pd
from typing import Optional
from .base_strategy import BaseStrategy, TradingSignal


class BollingerBandsStrategy(BaseStrategy):
    """
    Bollinger Bands breakout/mean-reversion strategy.
    Buys when price bounces off lower band with volume confirmation,
    sells when price hits upper band with volume confirmation.
    Also handles band squeeze breakouts for momentum trades.
    """

    def __init__(
        self,
        period: int = 20,
        std_deviation: float = 2.0,
        stop_loss_percent: float = 1.5,
        take_profit_percent: float = 3.0,
    ):
        super().__init__("Bollinger Bands")
        self.period = period
        self.std_deviation = std_deviation
        self.stop_loss_percent = stop_loss_percent
        self.take_profit_percent = take_profit_percent

    def _compute_bands(self, closes: pd.Series):
        rolling_mean = closes.rolling(window=self.period).mean()
        rolling_std = closes.rolling(window=self.period).std()
        upper_band = rolling_mean + (self.std_deviation * rolling_std)
        lower_band = rolling_mean - (self.std_deviation * rolling_std)
        bandwidth = (upper_band - lower_band) / rolling_mean
        return rolling_mean, upper_band, lower_band, bandwidth

    def compute_signal(self, symbol: str, ohlcv_dataframe: pd.DataFrame) -> Optional[TradingSignal]:
        if len(ohlcv_dataframe) < self.requires_minimum_candles():
            return None

        closes = ohlcv_dataframe["close"]
        volumes = ohlcv_dataframe["volume"]
        middle_band, upper_band, lower_band, _ = self._compute_bands(closes)

        current_price = closes.iloc[-1]
        previous_price = closes.iloc[-2]
        current_upper = upper_band.iloc[-1]
        current_lower = lower_band.iloc[-1]
        current_middle = middle_band.iloc[-1]
        previous_lower = lower_band.iloc[-2]
        previous_upper = upper_band.iloc[-2]

        avg_volume = volumes.rolling(20).mean().iloc[-1]
        current_volume = volumes.iloc[-1]
        volume_surge = current_volume > avg_volume * 1.2

        # Price bounces off lower band (was below, now above) = BUY signal
        lower_band_bounce = previous_price <= previous_lower and current_price > current_lower
        if lower_band_bounce:
            signal_strength = min(0.6 + (0.4 if volume_surge else 0.0), 1.0)
            band_position = (current_price - current_lower) / (current_upper - current_lower + 1e-10)
            return TradingSignal(
                symbol=symbol,
                strategy_name=self.name,
                signal_type="BUY",
                strength=round(signal_strength, 3),
                price=current_price,
                suggested_stop_loss=min(self.calculate_stop_loss(current_price, "BUY", self.stop_loss_percent), current_lower * 0.995),
                suggested_take_profit=min(self.calculate_take_profit(current_price, "BUY", self.take_profit_percent), current_middle),
                details={
                    "upper_band": round(current_upper, 4),
                    "middle_band": round(current_middle, 4),
                    "lower_band": round(current_lower, 4),
                    "band_position": round(band_position, 3),
                    "volume_surge": volume_surge,
                    "condition": "lower_band_bounce",
                },
            )

        # Price hits upper band (was below, now above) = SELL signal
        upper_band_touch = previous_price <= previous_upper and current_price >= current_upper
        if upper_band_touch:
            signal_strength = min(0.6 + (0.4 if volume_surge else 0.0), 1.0)
            band_position = (current_price - current_lower) / (current_upper - current_lower + 1e-10)
            return TradingSignal(
                symbol=symbol,
                strategy_name=self.name,
                signal_type="SELL",
                strength=round(signal_strength, 3),
                price=current_price,
                suggested_stop_loss=max(self.calculate_stop_loss(current_price, "SELL", self.stop_loss_percent), current_upper * 1.005),
                suggested_take_profit=max(self.calculate_take_profit(current_price, "SELL", self.take_profit_percent), current_middle),
                details={
                    "upper_band": round(current_upper, 4),
                    "middle_band": round(current_middle, 4),
                    "lower_band": round(current_lower, 4),
                    "band_position": round(band_position, 3),
                    "volume_surge": volume_surge,
                    "condition": "upper_band_touch",
                },
            )

        return None

    def requires_minimum_candles(self) -> int:
        return self.period * 2
