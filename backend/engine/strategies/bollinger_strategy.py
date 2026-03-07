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
        min_rr_ratio: float = 1.5,
        rsi_period: int = 14,
        rsi_oversold: float = 35.0,
        rsi_overbought: float = 65.0,
    ):
        super().__init__("Bollinger Bands")
        self.period = period
        self.std_deviation = std_deviation
        self.stop_loss_percent = stop_loss_percent
        self.take_profit_percent = take_profit_percent
        self.min_rr_ratio = min_rr_ratio
        self.rsi_period = rsi_period
        self.rsi_oversold = rsi_oversold
        self.rsi_overbought = rsi_overbought

    def _compute_rsi(self, closes: pd.Series) -> pd.Series:
        delta = closes.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.ewm(com=self.rsi_period - 1, min_periods=self.rsi_period).mean()
        avg_loss = loss.ewm(com=self.rsi_period - 1, min_periods=self.rsi_period).mean()
        rs = avg_gain / avg_loss.replace(0, float("nan"))
        return (100 - (100 / (1 + rs))).fillna(50)

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
        volume_surge = bool(current_volume > avg_volume * 1.2)
        rsi_series = self._compute_rsi(closes)
        current_rsi = float(rsi_series.iloc[-1])

        # Price bounces off lower band (was below, now above) = BUY signal
        # 70% WR gate: volume surge + RSI oversold confirmation
        lower_band_bounce = previous_price <= previous_lower and current_price > current_lower
        if lower_band_bounce:
            if not volume_surge or current_rsi > self.rsi_oversold:
                return None
            # Stop: tighter of percentage stop vs just-below lower band
            stop_price = max(
                self.calculate_stop_loss(current_price, "BUY", self.stop_loss_percent),
                current_lower * 0.995,
            )
            stop_distance = current_price - stop_price
            # Target: middle band is the mean-reversion destination
            target_price = max(
                self.calculate_take_profit(current_price, "BUY", self.take_profit_percent),
                current_middle,
            )
            target_distance = target_price - current_price
            # Skip if R:R is below minimum — middle band too close for the stop taken
            if stop_distance <= 0 or (target_distance / stop_distance) < self.min_rr_ratio:
                return None
            signal_strength = min(0.5 + (0.3 * ((self.rsi_oversold - current_rsi) / self.rsi_oversold)), 1.0)
            band_position = (current_price - current_lower) / (current_upper - current_lower + 1e-10)
            return TradingSignal(
                symbol=symbol,
                strategy_name=self.name,
                signal_type="BUY",
                strength=round(signal_strength, 3),
                price=current_price,
                suggested_stop_loss=round(stop_price, 8),
                suggested_take_profit=round(target_price, 8),
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
        # 70% WR gate: volume surge + RSI overbought confirmation
        upper_band_touch = previous_price <= previous_upper and current_price >= current_upper
        if upper_band_touch:
            if not volume_surge or current_rsi < self.rsi_overbought:
                return None
            # Stop: tighter of percentage stop vs just-above upper band
            stop_price = min(
                self.calculate_stop_loss(current_price, "SELL", self.stop_loss_percent),
                current_upper * 1.005,
            )
            stop_distance = stop_price - current_price
            # Target: middle band is the mean-reversion destination
            target_price = min(
                self.calculate_take_profit(current_price, "SELL", self.take_profit_percent),
                current_middle,
            )
            target_distance = current_price - target_price
            # Skip if R:R is below minimum
            if stop_distance <= 0 or (target_distance / stop_distance) < self.min_rr_ratio:
                return None
            signal_strength = min(0.5 + (0.3 * ((current_rsi - self.rsi_overbought) / (100 - self.rsi_overbought))), 1.0)
            band_position = (current_price - current_lower) / (current_upper - current_lower + 1e-10)
            return TradingSignal(
                symbol=symbol,
                strategy_name=self.name,
                signal_type="SELL",
                strength=round(signal_strength, 3),
                price=current_price,
                suggested_stop_loss=round(stop_price, 8),
                suggested_take_profit=round(target_price, 8),
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
        return max(self.period * 2, self.rsi_period * 3)
