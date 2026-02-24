import pandas as pd
import numpy as np
from typing import Optional
from .base_strategy import BaseStrategy, TradingSignal


class StatisticalArbitrageStrategy(BaseStrategy):
    """
    Statistical Arbitrage (Pairs Trading) — the foundational alpha source at
    Renaissance Technologies, Citadel, and Two Sigma.

    How it works:
    1. Track the price ratio/spread between two historically correlated assets (BTC/ETH)
    2. Compute a rolling z-score of that spread
    3. When the spread deviates significantly (z > entry_threshold), it's expected
       to mean-revert — trade the divergence, profit on convergence

    This strategy is market-neutral: it can generate returns in both bull AND bear
    markets because it trades *relative* price, not absolute direction.

    Spread formula: spread = log(BTC_price) - hedge_ratio * log(ETH_price)
    where hedge_ratio is the rolling OLS regression coefficient (Engle-Granger).
    """

    def __init__(
        self,
        spread_lookback: int = 60,
        zscore_entry_threshold: float = 2.0,
        zscore_exit_threshold: float = 0.5,
        stop_loss_percent: float = 3.0,
        take_profit_percent: float = 2.5,
    ):
        super().__init__("Statistical Arbitrage")
        self.spread_lookback = spread_lookback
        self.zscore_entry_threshold = zscore_entry_threshold
        self.zscore_exit_threshold = zscore_exit_threshold
        self.stop_loss_percent = stop_loss_percent
        self.take_profit_percent = take_profit_percent

    def _compute_hedge_ratio(self, log_prices_a: pd.Series, log_prices_b: pd.Series) -> float:
        """Rolling OLS hedge ratio using covariance method."""
        cov = np.cov(log_prices_a.values, log_prices_b.values)
        if cov[1, 1] == 0:
            return 1.0
        return cov[0, 1] / cov[1, 1]

    def _compute_spread_zscore(
        self, log_prices_a: pd.Series, log_prices_b: pd.Series
    ) -> tuple[pd.Series, float]:
        hedge_ratio = self._compute_hedge_ratio(log_prices_a, log_prices_b)
        spread = log_prices_a - hedge_ratio * log_prices_b
        spread_mean = spread.rolling(self.spread_lookback).mean()
        spread_std = spread.rolling(self.spread_lookback).std()
        zscore = (spread - spread_mean) / (spread_std + 1e-10)
        return zscore, hedge_ratio

    def compute_signal_from_pair(
        self,
        symbol_a: str,
        ohlcv_a: pd.DataFrame,
        ohlcv_b: pd.DataFrame,
    ) -> Optional[TradingSignal]:
        """
        Computes pair signal for symbol_a relative to symbol_b.
        symbol_a is the asset to trade; symbol_b is the hedge/reference.
        """
        if len(ohlcv_a) < self.requires_minimum_candles() or len(ohlcv_b) < self.requires_minimum_candles():
            return None

        min_len = min(len(ohlcv_a), len(ohlcv_b))
        closes_a = np.log(ohlcv_a["close"].values[-min_len:])
        closes_b = np.log(ohlcv_b["close"].values[-min_len:])

        log_a = pd.Series(closes_a)
        log_b = pd.Series(closes_b)
        zscore_series, hedge_ratio = self._compute_spread_zscore(log_a, log_b)

        current_zscore = float(zscore_series.iloc[-1])
        previous_zscore = float(zscore_series.iloc[-2])
        current_price = float(ohlcv_a["close"].iloc[-1])

        if np.isnan(current_zscore):
            return None

        # Spread too high: asset_a is overvalued vs asset_b → SELL asset_a (expect mean reversion down)
        if current_zscore > self.zscore_entry_threshold and previous_zscore <= self.zscore_entry_threshold:
            strength = min((current_zscore - self.zscore_entry_threshold) / 2.0, 1.0)
            return TradingSignal(
                symbol=symbol_a,
                strategy_name=self.name,
                signal_type="SELL",
                strength=round(strength, 3),
                price=current_price,
                suggested_stop_loss=self.calculate_stop_loss(current_price, "SELL", self.stop_loss_percent),
                suggested_take_profit=self.calculate_take_profit(current_price, "SELL", self.take_profit_percent),
                details={
                    "zscore": round(current_zscore, 3),
                    "hedge_ratio": round(hedge_ratio, 4),
                    "condition": "spread_too_high_mean_revert_down",
                    "pair": "BTC/ETH",
                },
            )

        # Spread too low: asset_a is undervalued vs asset_b → BUY asset_a (expect mean reversion up)
        if current_zscore < -self.zscore_entry_threshold and previous_zscore >= -self.zscore_entry_threshold:
            strength = min((abs(current_zscore) - self.zscore_entry_threshold) / 2.0, 1.0)
            return TradingSignal(
                symbol=symbol_a,
                strategy_name=self.name,
                signal_type="BUY",
                strength=round(strength, 3),
                price=current_price,
                suggested_stop_loss=self.calculate_stop_loss(current_price, "BUY", self.stop_loss_percent),
                suggested_take_profit=self.calculate_take_profit(current_price, "BUY", self.take_profit_percent),
                details={
                    "zscore": round(current_zscore, 3),
                    "hedge_ratio": round(hedge_ratio, 4),
                    "condition": "spread_too_low_mean_revert_up",
                    "pair": "BTC/ETH",
                },
            )

        return None

    def compute_signal(self, symbol: str, ohlcv_dataframe: pd.DataFrame) -> Optional[TradingSignal]:
        return None  # This strategy requires two dataframes; use compute_signal_from_pair

    def requires_minimum_candles(self) -> int:
        return self.spread_lookback + 20
