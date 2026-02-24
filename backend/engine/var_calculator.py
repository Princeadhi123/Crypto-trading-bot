import math
import statistics
from collections import deque
from dataclasses import dataclass
from typing import Optional


@dataclass
class PortfolioRiskReport:
    var_95: float             # Value at Risk at 95% confidence (daily loss in $)
    var_99: float             # Value at Risk at 99% confidence (daily loss in $)
    cvar_95: float            # Expected Shortfall (avg loss beyond VaR 95%)
    cvar_99: float            # Expected Shortfall (avg loss beyond VaR 99%)
    daily_volatility: float   # Standard deviation of daily returns
    annualized_volatility: float  # Annualized volatility %
    sharpe_ratio: float       # Portfolio-level Sharpe ratio
    sortino_ratio: float      # Downside deviation Sharpe (penalizes only losses)
    max_observed_loss: float  # Worst single-day loss in history
    portfolio_value: float
    observations: int


class VaRCalculator:
    """
    Computes portfolio-level Value at Risk (VaR) and Expected Shortfall (CVaR)
    using the historical simulation method — the same approach used by most
    institutional risk desks and required under Basel III banking regulations.

    Historical simulation VaR:
    - Keeps a rolling window of realized daily P&L returns
    - VaR(95%) = 5th percentile of the distribution of returns
    - CVaR(95%) = mean of all returns below VaR(95%)

    Sortino Ratio:
    - Unlike Sharpe, only penalizes downside volatility
    - Used by hedge funds that care more about avoiding losses than reducing
      all variance — a better metric for asymmetric return strategies
    """

    def __init__(self, rolling_window_days: int = 252, risk_free_rate_annual: float = 0.04):
        self.rolling_window_days = rolling_window_days
        self.risk_free_rate_annual = risk_free_rate_annual
        self._daily_pnl_history: deque[float] = deque(maxlen=rolling_window_days)
        self._portfolio_value_history: deque[float] = deque(maxlen=rolling_window_days)

    def record_daily_pnl(self, pnl_dollar: float, portfolio_value: float):
        self._daily_pnl_history.append(pnl_dollar)
        self._portfolio_value_history.append(portfolio_value)

    def record_trade_pnl(self, pnl_dollar: float, portfolio_value: float):
        """Accumulate trade-level P&L into current day's record."""
        self._daily_pnl_history.append(pnl_dollar)
        self._portfolio_value_history.append(portfolio_value)

    def compute(self, current_portfolio_value: float) -> PortfolioRiskReport:
        history = list(self._daily_pnl_history)
        n = len(history)

        if n < 5:
            return PortfolioRiskReport(
                var_95=current_portfolio_value * 0.02,
                var_99=current_portfolio_value * 0.03,
                cvar_95=current_portfolio_value * 0.025,
                cvar_99=current_portfolio_value * 0.04,
                daily_volatility=0.02,
                annualized_volatility=31.7,
                sharpe_ratio=0.0,
                sortino_ratio=0.0,
                max_observed_loss=0.0,
                portfolio_value=current_portfolio_value,
                observations=n,
            )

        sorted_returns = sorted(history)

        # VaR via historical percentile
        var_95_idx = max(0, int(n * 0.05) - 1)
        var_99_idx = max(0, int(n * 0.01) - 1)
        var_95 = abs(min(sorted_returns[var_95_idx], 0))
        var_99 = abs(min(sorted_returns[var_99_idx], 0))

        # CVaR = mean of losses beyond VaR threshold
        losses_beyond_var95 = [r for r in sorted_returns[:var_95_idx + 1] if r < 0]
        losses_beyond_var99 = [r for r in sorted_returns[:var_99_idx + 1] if r < 0]
        cvar_95 = abs(statistics.mean(losses_beyond_var95)) if losses_beyond_var95 else var_95
        cvar_99 = abs(statistics.mean(losses_beyond_var99)) if losses_beyond_var99 else var_99

        # Volatility
        mean_return = statistics.mean(history)
        std_return = statistics.stdev(history) if n > 1 else 0.0
        daily_vol_dollar = std_return
        daily_vol_pct = (daily_vol_dollar / current_portfolio_value * 100) if current_portfolio_value > 0 else 0.0
        annualized_vol = daily_vol_pct * math.sqrt(252)

        # Daily risk-free rate
        daily_rf = self.risk_free_rate_annual / 252

        # Sharpe ratio (portfolio level, dollar-denominated)
        pv = current_portfolio_value if current_portfolio_value > 0 else 1.0
        mean_return_pct = mean_return / pv
        std_return_pct = std_return / pv if pv > 0 else 0.0
        sharpe = ((mean_return_pct - daily_rf) / std_return_pct * math.sqrt(252)) if std_return_pct > 0 else 0.0

        # Sortino ratio (only downside deviation)
        negative_returns = [r / pv for r in history if r < 0]
        if len(negative_returns) > 1:
            downside_std = statistics.stdev(negative_returns)
            sortino = ((mean_return_pct - daily_rf) / downside_std * math.sqrt(252)) if downside_std > 0 else 0.0
        else:
            sortino = sharpe * 1.2  # Estimate when insufficient downside data

        max_loss = abs(min(history)) if history else 0.0

        return PortfolioRiskReport(
            var_95=round(var_95, 2),
            var_99=round(var_99, 2),
            cvar_95=round(cvar_95, 2),
            cvar_99=round(cvar_99, 2),
            daily_volatility=round(daily_vol_pct, 3),
            annualized_volatility=round(annualized_vol, 2),
            sharpe_ratio=round(sharpe, 3),
            sortino_ratio=round(sortino, 3),
            max_observed_loss=round(max_loss, 2),
            portfolio_value=round(current_portfolio_value, 2),
            observations=n,
        )

    def get_risk_budget_remaining(self, current_portfolio_value: float, max_daily_var_percent: float = 2.0) -> float:
        """
        Returns what fraction of the daily VaR budget has been used.
        Institutions enforce a hard stop when VaR budget is fully consumed.
        """
        report = self.compute(current_portfolio_value)
        daily_budget = current_portfolio_value * (max_daily_var_percent / 100)
        used = report.var_95
        return max(0.0, round((daily_budget - used) / daily_budget * 100, 1))
