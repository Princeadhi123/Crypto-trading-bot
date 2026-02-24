import pandas as pd
import numpy as np
from dataclasses import dataclass
from enum import Enum


class MarketRegime(Enum):
    TRENDING_UP = "trending_up"
    TRENDING_DOWN = "trending_down"
    RANGING = "ranging"
    HIGH_VOLATILITY = "high_volatility"
    LOW_VOLATILITY = "low_volatility"


@dataclass
class RegimeAnalysis:
    regime: MarketRegime
    adx_value: float
    volatility_percentile: float
    trend_strength: float        # 0.0 to 1.0
    volatility_ratio: float      # current ATR / historical ATR mean
    suitable_strategies: list[str]


class MarketRegimeDetector:
    """
    Classifies market conditions using ADX, ATR volatility percentile,
    and price momentum — exactly how institutional quant desks route
    capital to the correct strategy type for current conditions.

    Regime routing:
    - TRENDING: MACD + EMA Scalping get higher weight
    - RANGING:  RSI + Bollinger Bands get higher weight
    - HIGH_VOL: All position sizes reduced; wider stops
    - LOW_VOL:  Tighter stops; scalping preferred
    """

    def __init__(self, adx_period: int = 14, atr_period: int = 14,
                 volatility_lookback: int = 100, trending_adx_threshold: float = 25.0):
        self.adx_period = adx_period
        self.atr_period = atr_period
        self.volatility_lookback = volatility_lookback
        self.trending_adx_threshold = trending_adx_threshold

    def _compute_atr(self, highs: pd.Series, lows: pd.Series, closes: pd.Series) -> pd.Series:
        prev_closes = closes.shift(1)
        tr = pd.concat([
            highs - lows,
            (highs - prev_closes).abs(),
            (lows - prev_closes).abs(),
        ], axis=1).max(axis=1)
        return tr.ewm(span=self.atr_period, adjust=False).mean()

    def _compute_adx(self, highs: pd.Series, lows: pd.Series, closes: pd.Series) -> pd.Series:
        prev_highs = highs.shift(1)
        prev_lows = lows.shift(1)
        up_move = highs - prev_highs
        down_move = prev_lows - lows

        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

        atr = self._compute_atr(highs, lows, closes)
        atr_safe = atr.replace(0, np.nan)

        plus_di = 100 * pd.Series(plus_dm, index=highs.index).ewm(
            span=self.adx_period, adjust=False).mean() / atr_safe
        minus_di = 100 * pd.Series(minus_dm, index=highs.index).ewm(
            span=self.adx_period, adjust=False).mean() / atr_safe

        di_sum = plus_di + minus_di
        dx = (100 * (plus_di - minus_di).abs() / di_sum.replace(0, np.nan)).fillna(0)
        adx = dx.ewm(span=self.adx_period, adjust=False).mean()
        return adx, plus_di, minus_di

    def analyze(self, ohlcv_dataframe: pd.DataFrame) -> RegimeAnalysis:
        if len(ohlcv_dataframe) < max(self.adx_period * 2, self.volatility_lookback):
            return RegimeAnalysis(
                regime=MarketRegime.RANGING,
                adx_value=20.0,
                volatility_percentile=50.0,
                trend_strength=0.5,
                volatility_ratio=1.0,
                suitable_strategies=["rsi", "macd", "bollinger", "scalping"],
            )

        highs = ohlcv_dataframe["high"]
        lows = ohlcv_dataframe["low"]
        closes = ohlcv_dataframe["close"]

        adx_series, plus_di, minus_di = self._compute_adx(highs, lows, closes)
        atr_series = self._compute_atr(highs, lows, closes)

        current_adx = float(adx_series.iloc[-1])
        current_plus_di = float(plus_di.iloc[-1])
        current_minus_di = float(minus_di.iloc[-1])
        current_atr = float(atr_series.iloc[-1])
        current_price = float(closes.iloc[-1])

        historical_atrs = atr_series.iloc[-self.volatility_lookback:]
        atr_as_pct = (atr_series / closes.replace(0, np.nan) * 100).dropna()
        current_atr_pct = (current_atr / current_price) * 100
        vol_percentile = float(
            (atr_as_pct.iloc[-self.volatility_lookback:] <= current_atr_pct).mean() * 100
        )
        mean_historical_atr = float(historical_atrs.mean())
        volatility_ratio = current_atr / mean_historical_atr if mean_historical_atr > 0 else 1.0

        trend_strength = min(current_adx / 100.0, 1.0)

        # Determine regime
        if vol_percentile >= 80:
            regime = MarketRegime.HIGH_VOLATILITY
            suitable = ["bollinger"]
        elif vol_percentile <= 20:
            regime = MarketRegime.LOW_VOLATILITY
            suitable = ["scalping", "rsi"]
        elif current_adx >= self.trending_adx_threshold:
            if current_plus_di > current_minus_di:
                regime = MarketRegime.TRENDING_UP
                suitable = ["macd", "scalping"]
            else:
                regime = MarketRegime.TRENDING_DOWN
                suitable = ["macd", "scalping"]
        else:
            regime = MarketRegime.RANGING
            suitable = ["rsi", "bollinger"]

        return RegimeAnalysis(
            regime=regime,
            adx_value=round(current_adx, 2),
            volatility_percentile=round(vol_percentile, 1),
            trend_strength=round(trend_strength, 3),
            volatility_ratio=round(volatility_ratio, 3),
            suitable_strategies=suitable,
        )

    def get_strategy_weights(self, regime_analysis: RegimeAnalysis) -> dict[str, float]:
        """
        Returns a weight multiplier for each strategy based on current regime.
        Strategies not suited for the current regime get a very low weight.
        """
        all_strategies = ["rsi", "macd", "bollinger", "scalping", "pairs"]
        suitable = set(regime_analysis.suitable_strategies)
        weights = {}
        for strategy in all_strategies:
            if strategy in suitable:
                weights[strategy] = 1.0
            elif strategy == "pairs":
                # Pairs trading is market-neutral; penalise in strong trends but never zero
                if regime_analysis.regime in (MarketRegime.TRENDING_UP, MarketRegime.TRENDING_DOWN):
                    weights[strategy] = 0.40
                else:
                    weights[strategy] = 0.80  # Ranging / low-vol: pairs works well
            else:
                weights[strategy] = 0.25

        # Scale all weights down in high volatility (institutional risk-off)
        if regime_analysis.regime == MarketRegime.HIGH_VOLATILITY:
            for key in weights:
                # Pairs gets a smaller volatility penalty — it profits from divergence
                weights[key] *= 0.5 if key != "pairs" else 0.7

        return weights
