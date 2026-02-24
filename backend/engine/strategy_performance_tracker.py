import math
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass
class TradeOutcome:
    strategy_name: str
    pnl_percent: float
    closed_at: datetime


@dataclass
class StrategyMetrics:
    strategy_name: str
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    total_pnl_percent: float = 0.0
    rolling_sharpe: float = 0.0
    win_rate: float = 0.5
    avg_win_percent: float = 0.0
    avg_loss_percent: float = 0.0
    kelly_fraction: float = 0.02
    dynamic_weight: float = 1.0


class StrategyPerformanceTracker:
    """
    Tracks per-strategy performance in a rolling window and computes:

    1. Rolling Sharpe Ratio — risk-adjusted return over recent trades,
       exactly as used by quant desks to rank alpha sources.

    2. Kelly Criterion Fraction — mathematically optimal capital allocation:
         f* = (win_rate / avg_loss) - (loss_rate / avg_win)
       Capped at a half-Kelly (f*/2) for practical safety, a standard
       institutional practice.

    3. Dynamic Weight — strategies with positive Sharpe get proportionally
       more capital; strategies with negative Sharpe get near-zero weight.
       This is how multi-strategy funds continuously reallocate alpha.
    """

    def __init__(self, rolling_window: int = 30, min_trades_for_kelly: int = 5):
        self.rolling_window = rolling_window
        self.min_trades_for_kelly = min_trades_for_kelly
        self._outcomes: dict[str, deque[TradeOutcome]] = {}
        self._metrics: dict[str, StrategyMetrics] = {}

    def record_trade_outcome(self, strategy_name: str, pnl_percent: float):
        if strategy_name not in self._outcomes:
            self._outcomes[strategy_name] = deque(maxlen=self.rolling_window)
            self._metrics[strategy_name] = StrategyMetrics(strategy_name=strategy_name)

        outcome = TradeOutcome(
            strategy_name=strategy_name,
            pnl_percent=pnl_percent,
            closed_at=datetime.now(timezone.utc),
        )
        self._outcomes[strategy_name].append(outcome)
        self._recompute_metrics(strategy_name)
        self._recompute_dynamic_weights()

    def _recompute_metrics(self, strategy_name: str):
        outcomes = list(self._outcomes.get(strategy_name, []))
        metrics = self._metrics.get(strategy_name)
        if not metrics or not outcomes:
            return

        returns = [o.pnl_percent for o in outcomes]
        metrics.total_trades = len(returns)
        wins = [r for r in returns if r > 0]
        losses = [r for r in returns if r <= 0]

        metrics.wins = len(wins)
        metrics.losses = len(losses)
        metrics.total_pnl_percent = sum(returns)
        metrics.win_rate = len(wins) / len(returns) if returns else 0.5

        metrics.avg_win_percent = sum(wins) / len(wins) if wins else 0.0
        metrics.avg_loss_percent = abs(sum(losses) / len(losses)) if losses else 0.0

        # Rolling Sharpe (annualized assuming ~288 trades/day on 5m bars)
        if len(returns) >= 3:
            import statistics
            mean_return = statistics.mean(returns)
            std_return = statistics.stdev(returns) if len(returns) > 1 else 1e-10
            raw_sharpe = mean_return / (std_return + 1e-10)
            metrics.rolling_sharpe = round(raw_sharpe * math.sqrt(288), 4)
        else:
            metrics.rolling_sharpe = 0.0

        # Kelly Criterion (half-Kelly for safety)
        if metrics.total_trades >= self.min_trades_for_kelly and metrics.avg_loss_percent > 0 and metrics.avg_win_percent > 0:
            win_rate = metrics.win_rate
            loss_rate = 1.0 - win_rate
            kelly_full = (win_rate / metrics.avg_loss_percent) - (loss_rate / metrics.avg_win_percent)
            half_kelly = kelly_full / 2.0
            metrics.kelly_fraction = max(0.005, min(half_kelly, 0.05))
        else:
            metrics.kelly_fraction = 0.02

    def _recompute_dynamic_weights(self):
        all_sharpes = {
            name: m.rolling_sharpe
            for name, m in self._metrics.items()
            if m.total_trades >= 3
        }
        if not all_sharpes:
            return

        # Strategies with positive Sharpe get proportional weight; negative get minimum
        positive_sharpes = {k: v for k, v in all_sharpes.items() if v > 0}
        total_positive = sum(positive_sharpes.values()) if positive_sharpes else 1.0

        for strategy_name, metrics in self._metrics.items():
            if metrics.total_trades < 3:
                metrics.dynamic_weight = 1.0  # neutral until enough data
                continue
            sharpe = metrics.rolling_sharpe
            if sharpe <= 0:
                metrics.dynamic_weight = 0.15  # not killing it — might recover
            else:
                normalized = sharpe / (total_positive + 1e-10)
                metrics.dynamic_weight = round(max(0.15, min(normalized * len(positive_sharpes), 2.0)), 4)

    def get_metrics(self, strategy_name: str) -> StrategyMetrics:
        return self._metrics.get(strategy_name, StrategyMetrics(strategy_name=strategy_name))

    def get_all_dynamic_weights(self) -> dict[str, float]:
        return {
            name: metrics.dynamic_weight
            for name, metrics in self._metrics.items()
        }

    def get_kelly_fraction(self, strategy_name: str) -> float:
        return self._metrics.get(strategy_name, StrategyMetrics(strategy_name=strategy_name)).kelly_fraction

    def get_combined_weights(self, regime_weights: dict[str, float]) -> dict[str, float]:
        """
        Combines regime-based weights with performance-based dynamic weights.
        Final weight = regime_weight * dynamic_weight.
        Used to get the true allocation multiplier for each strategy.
        """
        combined = {}
        all_keys = set(regime_weights.keys()) | set(self._metrics.keys())
        for key in all_keys:
            regime_w = regime_weights.get(key, 1.0)
            dynamic_w = self._metrics[key].dynamic_weight if key in self._metrics else 1.0
            combined[key] = round(regime_w * dynamic_w, 4)
        return combined

    def get_summary(self) -> list[dict]:
        summary = []
        for name, metrics in self._metrics.items():
            summary.append({
                "strategy": name,
                "total_trades": metrics.total_trades,
                "wins": metrics.wins,
                "losses": metrics.losses,
                "win_rate": round(metrics.win_rate * 100, 1),
                "rolling_sharpe": metrics.rolling_sharpe,
                "kelly_fraction": round(metrics.kelly_fraction * 100, 2),
                "dynamic_weight": metrics.dynamic_weight,
                "avg_win_pct": round(metrics.avg_win_percent, 3),
                "avg_loss_pct": round(metrics.avg_loss_percent, 3),
            })
        return sorted(summary, key=lambda x: x["rolling_sharpe"], reverse=True)
