import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional, Callable

logger = logging.getLogger(__name__)

TRADE_FEE_RATE = 0.001   # mirrors trading_engine constant; keep in sync
_MIN_NOTIONAL = 5.0      # Binance minimum order value in quote currency


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
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

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

    def _slice_count(self, total_quantity: float, price: Optional[float], requested: Optional[int]) -> int:
        count = requested or self.default_slices
        if price is None or price <= 0:
            return count
        max_count = max(1, int((total_quantity * price) / _MIN_NOTIONAL))
        if count > max_count:
            logger.info("TWAP: reducing slices %d -> %d to satisfy MIN_NOTIONAL", count, max_count)
            return max_count
        return count

    @staticmethod
    def _precise_quantity(exchange, symbol: str, quantity: float) -> float:
        if exchange is None:
            return quantity
        try:
            return float(exchange.amount_to_precision(symbol, quantity))
        except Exception:
            return quantity

    def _build_slices(self, symbol: str, total_quantity: float, count: int,
                      interval: float, exchange) -> list[TwapSlice]:
        slice_qty = self._precise_quantity(exchange, symbol, total_quantity / count)
        now = datetime.now(timezone.utc).replace(microsecond=0)
        result = []
        for index in range(count):
            quantity = slice_qty
            if index == count - 1 and exchange is not None:
                remainder = total_quantity - slice_qty * (count - 1)
                quantity = self._precise_quantity(exchange, symbol, remainder)
            result.append(TwapSlice(
                slice_number=index + 1,
                quantity=quantity,
                target_time=now + timedelta(seconds=index * interval),
            ))
        return result

    def create_order(
        self,
        symbol: str,
        side: str,
        total_quantity: float,
        slices: Optional[int] = None,
        interval_seconds: Optional[float] = None,
        exchange=None,
        price: Optional[float] = None,
    ) -> TwapOrder:
        count = self._slice_count(total_quantity, price, slices)
        interval = interval_seconds or self.default_interval_seconds
        now = datetime.now(timezone.utc)
        order = TwapOrder(
            symbol=symbol,
            side=side,
            total_quantity=total_quantity,
            total_slices=count,
            interval_seconds=interval,
            slices=self._build_slices(symbol, total_quantity, count, interval, exchange),
            started_at=now,
        )
        self._active_orders[f"{symbol}_{side}_{now.timestamp()}"] = order
        logger.info("TWAP order created: %s %s qty=%.6f in %d slices every %.0fs",
                    side, symbol, total_quantity, count, interval)
        return order

    async def _fill_slice(self, order: TwapOrder, slice_order: TwapSlice,
                          get_current_price_fn: Callable, exchange, slippage_factor: float) -> tuple[float, float]:
        current_price = await get_current_price_fn(order.symbol)
        if current_price is None:
            raise ValueError(f"No price for {order.symbol}")
        if exchange is not None:
            live_order = await exchange.create_market_order(order.symbol, order.side, slice_order.quantity)
            fill_price = float(live_order.get("average") or live_order.get("price") or current_price)
            raw_filled = float(live_order.get("filled") or slice_order.quantity)
            quantity = raw_filled * (1 - TRADE_FEE_RATE) if order.side.upper() == "BUY" else raw_filled
        else:
            import random
            direction = 1.0 if order.side.upper() == "BUY" else -1.0
            fill_price = current_price + current_price * slippage_factor * direction * random.uniform(0.5, 1.5)
            quantity = slice_order.quantity
        return fill_price, quantity

    async def _execute_slice(self, order: TwapOrder, slice_order: TwapSlice,
                             index: int, get_current_price_fn: Callable,
                             on_slice_filled_fn: Optional[Callable], exchange,
                             slippage_factor: float) -> tuple[float, float]:
        if index > 0:
            await asyncio.sleep(order.interval_seconds)
        fill_price, quantity = await self._fill_slice(
            order, slice_order, get_current_price_fn, exchange, slippage_factor)
        slice_order.executed = True
        slice_order.fill_price = round(fill_price, 8)
        slice_order.executed_at = datetime.now(timezone.utc)
        logger.info("TWAP slice %d/%d filled: %s %s %.6f @ %.4f",
                    index + 1, order.total_slices, order.side, order.symbol,
                    slice_order.quantity, fill_price)
        if on_slice_filled_fn:
            await on_slice_filled_fn(slice_order)
        return fill_price, quantity

    def _purge_completed_orders(self) -> None:
        now = datetime.now(timezone.utc)
        stale_keys = [
            key for key, order in self._active_orders.items()
            if order.is_complete and (now - order.started_at).total_seconds() > 3600
        ]
        for key in stale_keys:
            del self._active_orders[key]

    async def execute_order(
        self,
        order: TwapOrder,
        get_current_price_fn: Callable,
        on_slice_filled_fn: Optional[Callable] = None,
        exchange=None,
    ) -> TwapOrder:
        slippage_factor = self.simulated_slippage_bps / 10000
        total_value = 0.0
        total_qty_filled = 0.0
        for index, slice_order in enumerate(order.slices):
            try:
                fill_price, quantity = await self._execute_slice(
                    order, slice_order, index, get_current_price_fn,
                    on_slice_filled_fn, exchange, slippage_factor)
                total_value += fill_price * quantity
                total_qty_filled += quantity
            except ValueError:
                logger.warning("TWAP: no price for %s, skipping slice %d", order.symbol, index + 1)
            except Exception:
                logger.exception("TWAP slice %d error", index + 1)
        order.total_filled = total_qty_filled
        order.avg_fill_price = round(total_value / total_qty_filled, 8) if total_qty_filled > 0 else 0.0
        order.is_complete = True
        logger.info("TWAP complete: %s %s avg_fill=%.4f total_filled=%.6f",
                    order.side, order.symbol, order.avg_fill_price, order.total_filled)
        self._purge_completed_orders()
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
