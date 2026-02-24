from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from engine.strategies.base_strategy import TradingSignal
from engine.regime_detector import RegimeAnalysis, MarketRegime


@dataclass
class EnsembleSignal:
    """
    Aggregated multi-strategy signal — like how institutional quants
    combine many factor models into a single confidence-weighted decision.
    Only emits a trade when multiple independent strategies agree.
    """
    symbol: str
    direction: str          # "BUY" or "SELL"
    composite_confidence: float  # 0.0 to 1.0 (higher = stronger consensus)
    agreeing_strategies: list[str]
    disagreeing_strategies: list[str]
    weighted_entry_price: float
    suggested_stop_loss: float
    suggested_take_profit: float
    regime: MarketRegime
    regime_boost: float     # multiplier from regime alignment
    timestamp: datetime = field(default_factory=datetime.utcnow)
    raw_signals: list[TradingSignal] = field(default_factory=list)

    @property
    def final_confidence(self) -> float:
        return min(self.composite_confidence * self.regime_boost, 1.0)


class SignalEnsemble:
    """
    Aggregates signals from multiple strategies using:
    1. Majority voting — requires a configurable quorum
    2. Weighted confidence — strategies with higher recent Sharpe get more vote weight
    3. Regime alignment — signals aligned with the detected market regime get boosted
    4. Directional consensus check — conflicting signals cancel each other

    This is the core of how multi-factor quant models work: no single
    indicator trades alone; the ensemble only fires when the evidence
    converges across independent alpha sources.
    """

    def __init__(
        self,
        minimum_agreement_count: int = 2,
        minimum_composite_confidence: float = 0.45,
        conflict_cancellation_threshold: float = 0.3,
    ):
        self.minimum_agreement_count = minimum_agreement_count
        self.minimum_composite_confidence = minimum_composite_confidence
        self.conflict_cancellation_threshold = conflict_cancellation_threshold

    def aggregate(
        self,
        symbol: str,
        raw_signals: list[TradingSignal],
        strategy_weights: dict[str, float],
        regime_analysis: RegimeAnalysis,
    ) -> Optional[EnsembleSignal]:
        if not raw_signals:
            return None

        buy_signals = [s for s in raw_signals if s.signal_type == "BUY"]
        sell_signals = [s for s in raw_signals if s.signal_type == "SELL"]

        # Conflict detection: if both buy and sell signals exist, cancel if too balanced
        if buy_signals and sell_signals:
            buy_weight = sum(strategy_weights.get(s.strategy_name.lower().split()[0], 1.0) * s.strength for s in buy_signals)
            sell_weight = sum(strategy_weights.get(s.strategy_name.lower().split()[0], 1.0) * s.strength for s in sell_signals)
            total_weight = buy_weight + sell_weight
            if total_weight > 0:
                conflict_ratio = min(buy_weight, sell_weight) / total_weight
                if conflict_ratio >= self.conflict_cancellation_threshold:
                    return None  # Conflicting evidence — stay out

        dominant_signals = buy_signals if len(buy_signals) >= len(sell_signals) else sell_signals
        direction = "BUY" if dominant_signals == buy_signals else "SELL"

        if len(dominant_signals) < self.minimum_agreement_count:
            return None

        # Compute weighted composite confidence
        total_weight = 0.0
        weighted_confidence = 0.0
        weighted_stop = 0.0
        weighted_tp = 0.0
        weighted_price = 0.0
        strategy_keys_from_name = {
            "RSI Mean Reversion": "rsi",
            "MACD Momentum": "macd",
            "Bollinger Bands": "bollinger",
            "EMA Scalping": "scalping",
            "Statistical Arbitrage": "pairs",
        }
        agreeing = []

        for signal in dominant_signals:
            strategy_key = strategy_keys_from_name.get(signal.strategy_name, signal.strategy_name.lower())
            regime_weight = strategy_weights.get(strategy_key, 1.0)
            effective_weight = regime_weight * signal.strength
            total_weight += effective_weight
            weighted_confidence += signal.strength * effective_weight
            weighted_stop += signal.suggested_stop_loss * effective_weight
            weighted_tp += signal.suggested_take_profit * effective_weight
            weighted_price += signal.price * effective_weight
            agreeing.append(signal.strategy_name)

        if total_weight == 0:
            return None

        composite_confidence = weighted_confidence / total_weight
        avg_stop = weighted_stop / total_weight
        avg_tp = weighted_tp / total_weight
        avg_price = weighted_price / total_weight

        if composite_confidence < self.minimum_composite_confidence:
            return None

        # Regime boost: signal aligned with detected market regime = confidence multiplier
        regime_boost = self._compute_regime_boost(direction, regime_analysis, agreeing, strategy_keys_from_name)

        disagreeing = [s.strategy_name for s in (sell_signals if direction == "BUY" else buy_signals)]

        return EnsembleSignal(
            symbol=symbol,
            direction=direction,
            composite_confidence=round(composite_confidence, 4),
            agreeing_strategies=agreeing,
            disagreeing_strategies=disagreeing,
            weighted_entry_price=round(avg_price, 8),
            suggested_stop_loss=round(avg_stop, 8),
            suggested_take_profit=round(avg_tp, 8),
            regime=regime_analysis.regime,
            regime_boost=round(regime_boost, 3),
            raw_signals=dominant_signals,
        )

    def _compute_regime_boost(
        self,
        direction: str,
        regime_analysis: RegimeAnalysis,
        agreeing_strategy_names: list[str],
        name_to_key: dict[str, str],
    ) -> float:
        suitable = set(regime_analysis.suitable_strategies)
        agreeing_keys = {name_to_key.get(name, name.lower()) for name in agreeing_strategy_names}
        aligned_count = len(agreeing_keys & suitable)

        if len(agreeing_keys) == 0:
            return 1.0

        alignment_ratio = aligned_count / len(agreeing_keys)

        # High-volatility regime always reduces confidence regardless of direction
        if regime_analysis.regime == MarketRegime.HIGH_VOLATILITY:
            return 0.7 + (0.1 * alignment_ratio)

        # Trending regimes: reward momentum signals, slight penalty for mean-reversion
        if regime_analysis.regime in (MarketRegime.TRENDING_UP, MarketRegime.TRENDING_DOWN):
            return 1.0 + (0.3 * alignment_ratio)

        # Ranging: reward mean-reversion signals
        if regime_analysis.regime == MarketRegime.RANGING:
            return 1.0 + (0.2 * alignment_ratio)

        return 1.0 + (0.15 * alignment_ratio)
