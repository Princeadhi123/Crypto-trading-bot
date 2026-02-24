import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
import ccxt.async_support as ccxt

_FUTURES_EXCHANGE: Optional[ccxt.Exchange] = None

async def _get_public_futures_exchange() -> ccxt.Exchange:
    """Returns a shared public Binance futures exchange (no auth needed for funding rates)."""
    global _FUTURES_EXCHANGE
    if _FUTURES_EXCHANGE is None:
        _FUTURES_EXCHANGE = ccxt.binance({"options": {"defaultType": "swap"}, "enableRateLimit": True})
    return _FUTURES_EXCHANGE

logger = logging.getLogger(__name__)

PERPETUAL_SYMBOL_MAP = {
    "BTC/USDT": "BTC/USDT:USDT",
    "ETH/USDT": "ETH/USDT:USDT",
    "BNB/USDT": "BNB/USDT:USDT",
    "SOL/USDT": "SOL/USDT:USDT",
    "AVAX/USDT": "AVAX/USDT:USDT",
}

SIMULATED_FUNDING_RATES = {
    "BTC/USDT": 0.0001,
    "ETH/USDT": 0.00015,
    "BNB/USDT": 0.00008,
    "SOL/USDT": 0.00020,
    "AVAX/USDT": 0.00018,
}


@dataclass
class FundingRateReading:
    symbol: str
    funding_rate: float        # e.g. 0.0001 = 0.01% per 8 hours
    annualized_rate: float     # funding_rate * 3 * 365 (3 fundings/day)
    signal_bias: str           # "BEARISH_FOR_LONGS", "BULLISH_FOR_LONGS", "NEUTRAL"
    signal_strength: float     # 0.0 to 1.0
    timestamp: datetime
    is_simulated: bool


class FundingRateSignal:
    """
    Perpetual futures funding rate signal — one of the most powerful and
    crypto-exclusive alpha sources used by Alameda Research, Cumberland,
    Jump Trading, and all major crypto market makers / hedge funds.

    How funding rates work:
    - Perpetual futures have no expiry; a funding mechanism keeps price ≈ spot
    - When perp price > spot (bullish market): longs PAY shorts
      → High positive funding = crowded long trade = contrarian SELL signal
    - When perp price < spot (bearish market): shorts PAY longs
      → High negative funding = crowded short trade = contrarian BUY signal

    Extreme funding rates reliably precede reversals because overleveraged
    positions become unsustainable — a classic crowded-trade unwind.

    Thresholds used:
    - |rate| > 0.05% per 8h (0.15%/day, ~55%/year): extreme signal
    - |rate| > 0.03% per 8h: moderate signal
    - |rate| < 0.01% per 8h: neutral (no funding alpha)
    """

    EXTREME_FUNDING_THRESHOLD = 0.0005  # 0.05% per 8h
    MODERATE_FUNDING_THRESHOLD = 0.0003  # 0.03% per 8h
    NEUTRAL_THRESHOLD = 0.0001           # 0.01% per 8h

    def __init__(self, exchange: Optional[ccxt.Exchange] = None):
        self._exchange = exchange
        self._cache: dict[str, FundingRateReading] = {}

    def set_exchange(self, exchange: Optional[ccxt.Exchange]):
        self._exchange = exchange

    async def get_funding_rate(self, spot_symbol: str) -> FundingRateReading:
        perp_symbol = PERPETUAL_SYMBOL_MAP.get(spot_symbol)
        is_simulated = True

        raw_rate = SIMULATED_FUNDING_RATES.get(spot_symbol, 0.0001)

        if perp_symbol:
            try:
                # Funding rates require a futures/swap exchange context.
                # Use provided exchange if it is futures-type, otherwise fall back
                # to the shared public Binance swap exchange (no API key required).
                exchange_to_use = self._exchange
                if exchange_to_use is None or exchange_to_use.options.get("defaultType") == "spot":
                    exchange_to_use = await _get_public_futures_exchange()
                funding_info = await exchange_to_use.fetch_funding_rate(perp_symbol)
                raw_rate = float(funding_info.get("fundingRate", raw_rate))
                is_simulated = False
            except Exception as exc:
                logger.debug("Funding rate fetch failed for %s: %s", perp_symbol, exc)

        annualized = raw_rate * 3 * 365 * 100  # 3 fundings/day, convert to %

        signal_bias, strength = self._interpret_funding(raw_rate)

        reading = FundingRateReading(
            symbol=spot_symbol,
            funding_rate=round(raw_rate, 8),
            annualized_rate=round(annualized, 2),
            signal_bias=signal_bias,
            signal_strength=round(strength, 3),
            timestamp=datetime.utcnow(),
            is_simulated=is_simulated,
        )
        self._cache[spot_symbol] = reading
        return reading

    def _interpret_funding(self, rate: float) -> tuple[str, float]:
        abs_rate = abs(rate)

        if abs_rate < self.NEUTRAL_THRESHOLD:
            return "NEUTRAL", 0.0

        if rate > 0:
            # Positive funding: longs paying shorts → crowded long → bearish signal
            if abs_rate >= self.EXTREME_FUNDING_THRESHOLD:
                return "BEARISH_FOR_LONGS", min((abs_rate / self.EXTREME_FUNDING_THRESHOLD) * 0.8, 1.0)
            return "BEARISH_FOR_LONGS", min((abs_rate / self.MODERATE_FUNDING_THRESHOLD) * 0.5, 0.7)
        else:
            # Negative funding: shorts paying longs → crowded short → bullish signal
            if abs_rate >= self.EXTREME_FUNDING_THRESHOLD:
                return "BULLISH_FOR_LONGS", min((abs_rate / self.EXTREME_FUNDING_THRESHOLD) * 0.8, 1.0)
            return "BULLISH_FOR_LONGS", min((abs_rate / self.MODERATE_FUNDING_THRESHOLD) * 0.5, 0.7)

    def is_signal_aligned_with_funding(self, signal_direction: str, funding: FundingRateReading) -> bool:
        """
        Returns True if the trade direction aligns with (or is not opposed by) funding.
        - BUY aligned with BULLISH_FOR_LONGS = confirmed
        - BUY against BEARISH_FOR_LONGS (extreme) = blocked
        - SELL against BULLISH_FOR_LONGS (extreme) = blocked
        """
        if funding.signal_bias == "NEUTRAL":
            return True
        if signal_direction == "BUY" and funding.signal_bias == "BEARISH_FOR_LONGS" and funding.signal_strength > 0.6:
            return False
        if signal_direction == "SELL" and funding.signal_bias == "BULLISH_FOR_LONGS" and funding.signal_strength > 0.6:
            return False
        return True

    def get_confidence_adjustment(self, signal_direction: str, funding: FundingRateReading) -> float:
        if funding.signal_bias == "NEUTRAL":
            return 1.0
        aligned = (signal_direction == "BUY" and funding.signal_bias == "BULLISH_FOR_LONGS") or \
                  (signal_direction == "SELL" and funding.signal_bias == "BEARISH_FOR_LONGS")
        if aligned:
            return 1.0 + funding.signal_strength * 0.3  # Funding confirmation = up to 30% boost
        return 1.0 - funding.signal_strength * 0.2       # Funding contradiction = up to 20% penalty

    def get_all_cached(self) -> list[dict]:
        return [
            {
                "symbol": r.symbol,
                "funding_rate": r.funding_rate,
                "funding_rate_pct": round(r.funding_rate * 100, 4),
                "annualized_rate_pct": r.annualized_rate,
                "signal_bias": r.signal_bias,
                "signal_strength": r.signal_strength,
                "is_simulated": r.is_simulated,
            }
            for r in self._cache.values()
        ]
