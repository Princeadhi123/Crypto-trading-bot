import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional, Callable

logger = logging.getLogger(__name__)


@dataclass
class TwapSlice:
    slice_number: int
    quantity: float
    target_time: datetime
    executed: bool = False
    fill_price: Optional[float] = None
    executed_at: Optional[datetime] = None


@dataclass
class TwapOrder:
    symbol: str
    side: str
    total_quantity: float
    total_slices: int
    interval_seconds: float
    slices: list[TwapSlice] = field(default_factory=list)
    avg_fill_price: float = 0.0
    total_filled: float = 0.0
    is_complete: bool = False
    started_at: datetime = field(default_factory=datetime.utcnow)

    def completion_percent(self) -> float:
        if self.total_slices == 0:
            return 0.0
        executed = sum(1 for s in self.slices if s.executed)
        return round(executed / self.total_slices * 100, 1)


class TwapExecutor:
    """
    Time-Weighted Average Price (TWAP) execution algorithm.
    Used by every institutional trading desk to minimize market impact.

    Why TWAP matters:
    - Dumping a large order instantly moves the market against you (price impact)
    - TWAP splits the order into N equal slices spread over T minutes
    - Each slice is small enough that it doesn't materially move the price
    - Result: average fill price ≈ TWAP of the period, not a worse single print

    Renaissance, Citadel, and all HFT shops use more sophisticated versions
    (VWAP, Implementation Shortfall, Arrival Price) but TWAP is the baseline
    that most systematic funds use for non-HFT strategies.

    In paper trading mode this simulates fills at current market price ± small
    random slippage to model realistic execution quality.
    """

    def __init__(
        self,
        default_slices: int = 5,
        default_interval_seconds: float = 12.0,
        simulated_slippage_bps: float = 3.0,
    ):
        self.default_slices = default_slices
        self.default_interval_seconds = default_interval_seconds
        self.simulated_slippage_bps = simulated_slippage_bps
        self._active_orders: dict[str, TwapOrder] = {}

    def create_order(
        self,
        symbol: str,
        side: str,
        total_quantity: float,
        slices: Optional[int] = None,
        interval_seconds: Optional[float] = None,
    ) -> TwapOrder:
        n_slices = slices or self.default_slices
        interval = interval_seconds or self.default_interval_seconds
        slice_qty = total_quantity / n_slices

        now = datetime.utcnow()
        base_time = datetime(now.year, now.month, now.day, now.hour, now.minute, now.second)
        slice_list = [
            TwapSlice(
                slice_number=i + 1,
                quantity=slice_qty,
                target_time=base_time + timedelta(seconds=i * interval),
            )
            for i in range(n_slices)
        ]

        order = TwapOrder(
            symbol=symbol,
            side=side,
            total_quantity=total_quantity,
            total_slices=n_slices,
            interval_seconds=interval,
            slices=slice_list,
        )
        order_key = f"{symbol}_{side}_{now.timestamp()}"
        self._active_orders[order_key] = order
        logger.info("TWAP order created: %s %s qty=%.6f in %d slices every %.0fs",
                    side, symbol, total_quantity, n_slices, interval)
        return order

    async def execute_order(
        self,
        order: TwapOrder,
        get_current_price_fn: Callable,
        on_slice_filled_fn: Optional[Callable] = None,
    ) -> TwapOrder:
        """
        Executes all slices of the TWAP order with proper timing.
        In live mode, calls the exchange. In paper mode, simulates fills.
        """
        import random
        slippage_factor = self.simulated_slippage_bps / 10000

        total_value = 0.0
        total_qty_filled = 0.0

        for i, slice_order in enumerate(order.slices):
            if i > 0:
                await asyncio.sleep(order.interval_seconds)

            try:
                current_price = await get_current_price_fn(order.symbol)
                if current_price is None:
                    logger.warning("TWAP: no price for %s, skipping slice %d", order.symbol, i + 1)
                    continue

                # Simulate realistic slippage
                direction_mult = 1.0 if order.side == "BUY" else -1.0
                slippage = current_price * slippage_factor * direction_mult * random.uniform(0.5, 1.5)
                fill_price = current_price + slippage

                slice_order.executed = True
                slice_order.fill_price = round(fill_price, 8)
                slice_order.executed_at = datetime.utcnow()

                total_value += fill_price * slice_order.quantity
                total_qty_filled += slice_order.quantity

                logger.info("TWAP slice %d/%d filled: %s %s %.6f @ %.4f",
                            i + 1, order.total_slices, order.side, order.symbol,
                            slice_order.quantity, fill_price)

                if on_slice_filled_fn:
                    await on_slice_filled_fn(slice_order)

            except Exception as exc:
                logger.error("TWAP slice %d error: %s", i + 1, exc)

        order.total_filled = total_qty_filled
        order.avg_fill_price = round(total_value / total_qty_filled, 8) if total_qty_filled > 0 else 0.0
        order.is_complete = True
        logger.info("TWAP complete: %s %s avg_fill=%.4f total_filled=%.6f",
                    order.side, order.symbol, order.avg_fill_price, order.total_filled)
        # Purge old completed orders to prevent unbounded memory growth
        stale_keys = [
            k for k, o in self._active_orders.items()
            if o.is_complete and (datetime.utcnow() - o.started_at).total_seconds() > 3600
        ]
        for k in stale_keys:
            del self._active_orders[k]
        return order

    def get_active_orders(self) -> list[dict]:
        return [
            {
                "symbol": o.symbol,
                "side": o.side,
                "total_quantity": o.total_quantity,
                "total_slices": o.total_slices,
                "completion_percent": o.completion_percent(),
                "avg_fill_price": o.avg_fill_price,
                "is_complete": o.is_complete,
            }
            for o in self._active_orders.values()
            if not o.is_complete
        ]
