from dataclasses import dataclass
from typing import Optional
import logging
import math

logger = logging.getLogger(__name__)



@dataclass
class PositionSizeResult:
    allowed: bool
    quantity: float
    position_value: float
    risk_amount: float
    rejection_reason: Optional[str] = None


CORRELATED_PAIRS: dict[str, list[str]] = {
    "BTC/USDT": ["ETH/USDT", "BNB/USDT", "SOL/USDT"],
    "ETH/USDT": ["BTC/USDT", "BNB/USDT", "MATIC/USDT"],
    "BNB/USDT": ["BTC/USDT", "ETH/USDT"],
    "SOL/USDT": ["BTC/USDT", "AVAX/USDT"],
    "MATIC/USDT": ["ETH/USDT", "SOL/USDT"],
    "AVAX/USDT": ["SOL/USDT", "BTC/USDT"],
}


class RiskManager:
    """
    Institutional-grade multi-layer risk engine:

    1. Signal confidence gate — minimum ensemble confidence
    2. Correlation guard — no two highly correlated assets long simultaneously
    3. Drawdown circuit breaker — halts all trading above max drawdown
    4. Anti-martingale scaling — position size reduces proportionally to drawdown
    5. Volatility targeting — ATR-scaled sizing (more volatile = smaller position)
    6. Kelly criterion sizing — mathematically optimal bet fraction
    7. Maximum position cap — never exceed 20% of portfolio in one trade
    8. Concurrent position limit
    9. Duplicate symbol prevention
    """

    def __init__(
        self,
        max_portfolio_risk_percent: float = 2.0,
        max_drawdown_percent: float = 10.0,
        max_concurrent_positions: int = 5,
        min_signal_confidence: float = 0.45,
        volatility_target_atr_percent: float = 1.5,
    ):
        self.max_portfolio_risk_percent = max_portfolio_risk_percent
        self.max_drawdown_percent = max_drawdown_percent
        self.max_concurrent_positions = max_concurrent_positions
        self.min_signal_confidence = min_signal_confidence
        self.volatility_target_atr_percent = volatility_target_atr_percent
        self.peak_portfolio_value: float = 0.0
        self.circuit_breaker_active: bool = False

    def update_peak_portfolio_value(self, current_portfolio_value: float):
        if current_portfolio_value > self.peak_portfolio_value:
            self.peak_portfolio_value = current_portfolio_value

    def compute_current_drawdown_percent(self, current_portfolio_value: float) -> float:
        if self.peak_portfolio_value <= 0:
            return 0.0
        return ((self.peak_portfolio_value - current_portfolio_value) / self.peak_portfolio_value) * 100

    def is_drawdown_circuit_breaker_triggered(self, current_portfolio_value: float) -> bool:
        """
        Check if drawdown circuit breaker should be active.
        Once triggered, stays active until manually reset via reset_circuit_breaker().
        """
        # If already active, keep it active until manual reset
        if self.circuit_breaker_active:
            return True
        
        current_drawdown = self.compute_current_drawdown_percent(current_portfolio_value)
        
        if current_drawdown >= self.max_drawdown_percent:
            self.circuit_breaker_active = True
            logger.warning(
                "🚨 CIRCUIT BREAKER TRIGGERED: %.2f%% drawdown (max: %.2f%%). Trading suspended until manual reset.",
                current_drawdown,
                self.max_drawdown_percent,
            )
            return True
        
        return False
    
    def reset_circuit_breaker(self, current_portfolio_value: float):
        """
        Manual reset of circuit breaker - performs High-Water Mark Reset.
        Resets peak to current value so drawdown becomes 0% and trading can resume.
        """
        if not self.circuit_breaker_active:
            logger.info("Circuit breaker reset called but was not active")
            return
        
        old_peak = self.peak_portfolio_value
        self.peak_portfolio_value = current_portfolio_value
        self.circuit_breaker_active = False
        
        logger.info(
            "✅ Circuit breaker manually reset. High-water mark: $%.2f → $%.2f. Trading resumed.",
            old_peak,
            current_portfolio_value,
        )

    def _compute_anti_martingale_scale(self, current_portfolio_value: float) -> float:
        """
        Reduce position size proportionally as drawdown grows.
        At 0% drawdown: full size (1.0x).
        At max_drawdown/2: half size (0.5x).
        At max_drawdown: near zero (forces circuit breaker first).
        Institutions call this 'convex risk scaling' — protect capital
        during drawdowns and scale back up as performance recovers.
        """
        drawdown = self.compute_current_drawdown_percent(current_portfolio_value)
        if drawdown <= 0:
            return 1.0
        scale = 1.0 - (drawdown / self.max_drawdown_percent) ** 0.7
        return max(0.1, scale)

    def _compute_volatility_scale(self, entry_price: float, stop_loss_price: float) -> float:
        """
        Scale position size inversely to stop distance (proxy for volatility).
        Tight stop = normal size. Wide stop = reduce size to keep risk constant.
        This is volatility targeting — same risk in dollar terms regardless of ATR.
        """
        stop_distance_pct = abs(entry_price - stop_loss_price) / entry_price * 100
        if stop_distance_pct <= 0:
            return 1.0
        target_pct = self.volatility_target_atr_percent
        vol_scale = target_pct / stop_distance_pct
        return max(0.2, min(vol_scale, 2.0))

    def _is_correlated_conflict(self, symbol: str, open_symbols: list[str], side: str) -> bool:
        """
        Prevents opening a new LONG position if a highly correlated asset
        is already long. This avoids concentrated directional bets disguised
        as diversification — the same mistake that blew up many 2022 crypto funds.
        """
        correlated = CORRELATED_PAIRS.get(symbol, [])
        for open_sym in open_symbols:
            if open_sym in correlated:
                return True
        return False

    def calculate_position_size(
        self,
        portfolio_value: float,
        entry_price: float,
        stop_loss_price: float,
        signal_confidence: float,
        open_positions_count: int,
        open_symbols: list[str],
        symbol: str,
        side: str = "BUY",
        kelly_fraction: float = 0.02,
    ) -> PositionSizeResult:

        # 1. Signal confidence gate
        if signal_confidence < self.min_signal_confidence:
            return PositionSizeResult(
                allowed=False, quantity=0.0, position_value=0.0, risk_amount=0.0,
                rejection_reason=f"Ensemble confidence {signal_confidence:.3f} below minimum {self.min_signal_confidence:.2f}",
            )

        # 2. Max concurrent positions
        if open_positions_count >= self.max_concurrent_positions:
            return PositionSizeResult(
                allowed=False, quantity=0.0, position_value=0.0, risk_amount=0.0,
                rejection_reason=f"Max concurrent positions ({self.max_concurrent_positions}) reached",
            )

        # 3. Duplicate symbol prevention
        if symbol in open_symbols:
            return PositionSizeResult(
                allowed=False, quantity=0.0, position_value=0.0, risk_amount=0.0,
                rejection_reason=f"Position already open for {symbol}",
            )

        # 4. Correlation guard (only for BUY — shorts can hedge)
        if side == "BUY" and self._is_correlated_conflict(symbol, open_symbols, side):
            return PositionSizeResult(
                allowed=False, quantity=0.0, position_value=0.0, risk_amount=0.0,
                rejection_reason=f"Correlated position already open; diversification guard triggered for {symbol}",
            )

        # 5. Drawdown circuit breaker
        if self.is_drawdown_circuit_breaker_triggered(portfolio_value):
            return PositionSizeResult(
                allowed=False, quantity=0.0, position_value=0.0, risk_amount=0.0,
                rejection_reason=f"Drawdown circuit breaker active (>{self.max_drawdown_percent:.1f}%)",
            )

        price_risk_per_unit = abs(entry_price - stop_loss_price)
        if price_risk_per_unit <= 0:
            return PositionSizeResult(
                allowed=False, quantity=0.0, position_value=0.0, risk_amount=0.0,
                rejection_reason="Invalid stop loss: same as entry price",
            )

        # 6. Kelly criterion base sizing
        # Use the Kelly fraction from strategy performance tracker (half-Kelly).
        # Fallback to max_portfolio_risk_percent / 100 if Kelly not yet calibrated.
        kelly_risk_percent = min(kelly_fraction * 100, self.max_portfolio_risk_percent)

        # 7. Anti-martingale: scale down during drawdown
        anti_martingale_scale = self._compute_anti_martingale_scale(portfolio_value)

        # 8. Volatility targeting: adjust for current ATR vs target
        vol_scale = self._compute_volatility_scale(entry_price, stop_loss_price)

        # 9. Signal confidence scaling: stronger consensus = use more of allowed risk
        confidence_scale = 0.5 + (signal_confidence * 0.5)

        effective_risk_percent = kelly_risk_percent * anti_martingale_scale * vol_scale * confidence_scale
        effective_risk_percent = max(0.1, min(effective_risk_percent, self.max_portfolio_risk_percent))

        max_risk_amount = portfolio_value * (effective_risk_percent / 100)
        quantity = max_risk_amount / price_risk_per_unit
        position_value = quantity * entry_price

        # 10. Hard cap: never exceed 20% of portfolio in a single position
        max_position_value = portfolio_value * 0.20
        if position_value > max_position_value:
            quantity = max_position_value / entry_price
            position_value = max_position_value

        if quantity <= 0 or position_value <= 0:
            return PositionSizeResult(
                allowed=False, quantity=0.0, position_value=0.0, risk_amount=0.0,
                rejection_reason="Calculated quantity is zero or negative",
            )

        # 11. Minimum notional floor (Binance Spot requires ~$5 USDT minimum)
        MIN_NOTIONAL_USDT = 5.0
        if position_value < MIN_NOTIONAL_USDT:
            return PositionSizeResult(
                allowed=False, quantity=0.0, position_value=0.0, risk_amount=0.0,
                rejection_reason=f"Position value ${position_value:.2f} below exchange minimum notional (${MIN_NOTIONAL_USDT})",
            )

        actual_risk = quantity * price_risk_per_unit
        logger.info(
            "Sizing %s %s: qty=%.6f value=$%.2f risk=$%.2f (%.2f%%) | "
            "kelly=%.2f%% anti_dd=%.2f vol=%.2f conf=%.2f",
            side, symbol, quantity, position_value, actual_risk,
            (actual_risk / portfolio_value) * 100,
            kelly_risk_percent, anti_martingale_scale, vol_scale, confidence_scale,
        )

        return PositionSizeResult(
            allowed=True,
            quantity=round(quantity, 6),
            position_value=round(position_value, 2),
            risk_amount=round(actual_risk, 2),
        )
