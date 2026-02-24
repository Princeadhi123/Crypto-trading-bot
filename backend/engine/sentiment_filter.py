import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional
import httpx

logger = logging.getLogger(__name__)

FEAR_GREED_API_URL = "https://api.alternative.me/fng/?limit=1&format=json"


@dataclass
class SentimentReading:
    value: int                  # 0-100 (0=extreme fear, 100=extreme greed)
    classification: str         # "Extreme Fear", "Fear", "Neutral", "Greed", "Extreme Greed"
    timestamp: datetime
    trading_allowed: bool
    trading_bias: str           # "BUY_ONLY", "SELL_ONLY", "BOTH", "NONE"
    reason: str


class SentimentFilter:
    """
    Crypto Fear & Greed Index sentiment filter — a standard market-regime
    overlay used by crypto hedge funds and systematic traders.

    Institutional logic:
    - Extreme Fear (0-20): Best time to buy (market oversold, panic selling).
      Only allow BUY signals. Contrarian positioning — "be greedy when others are fearful."
    - Fear (21-39): Allow BUY signals with reduced confidence requirement.
    - Neutral (40-60): Allow all signals normally.
    - Greed (61-79): Only allow SELL signals — reduce longs, avoid chasing tops.
    - Extreme Greed (80-100): Suspend new BUY trades entirely. High crash risk.
      Only SELL signals allowed — protect existing profits.

    The index is sourced from Alternative.me which aggregates: volatility,
    market momentum, social media, surveys, Bitcoin dominance, and Google trends.
    """

    CLASSIFICATION_THRESHOLDS = [
        (0, 24, "Extreme Fear", "BUY_ONLY"),
        (25, 44, "Fear", "BUY_ONLY"),
        (45, 55, "Neutral", "BOTH"),
        (56, 74, "Greed", "SELL_ONLY"),
        (75, 100, "Extreme Greed", "SELL_ONLY"),
    ]

    def __init__(
        self,
        cache_ttl_minutes: int = 30,
        extreme_fear_threshold: int = 25,
        extreme_greed_threshold: int = 75,
    ):
        self.cache_ttl_minutes = cache_ttl_minutes
        self.extreme_fear_threshold = extreme_fear_threshold
        self.extreme_greed_threshold = extreme_greed_threshold
        self._cached_reading: Optional[SentimentReading] = None
        self._last_fetch: Optional[datetime] = None
        self._fallback_value = 50  # Neutral if API unavailable

    def _classify(self, value: int) -> tuple[str, str]:
        for low, high, classification, bias in self.CLASSIFICATION_THRESHOLDS:
            if low <= value <= high:
                return classification, bias
        return "Neutral", "BOTH"

    async def fetch_current_sentiment(self) -> SentimentReading:
        now = datetime.utcnow()
        if (self._cached_reading is not None and self._last_fetch is not None and
                (now - self._last_fetch).total_seconds() < self.cache_ttl_minutes * 60):
            return self._cached_reading

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(FEAR_GREED_API_URL)
                data = response.json()
                entry = data["data"][0]
                value = int(entry["value"])
                classification, bias = self._classify(value)
                trading_allowed = True
                reason = f"Fear & Greed Index: {value} ({classification})"
                reading = SentimentReading(
                    value=value,
                    classification=classification,
                    timestamp=now,
                    trading_allowed=trading_allowed,
                    trading_bias=bias,
                    reason=reason,
                )
                self._cached_reading = reading
                self._last_fetch = now
                logger.info("Sentiment: %d (%s) → bias=%s", value, classification, bias)
                return reading
        except Exception as exc:
            logger.warning("Fear & Greed API unavailable: %s — using neutral fallback", exc)
            fallback = SentimentReading(
                value=self._fallback_value,
                classification="Neutral (API unavailable)",
                timestamp=now,
                trading_allowed=True,
                trading_bias="BOTH",
                reason="Sentiment API unavailable — all signals permitted",
            )
            # Cache the fallback for 5 minutes so we don't hammer the API on every tick
            self._cached_reading = fallback
            self._last_fetch = now
            return fallback

    def is_signal_allowed(self, signal_direction: str, sentiment: SentimentReading) -> bool:
        """
        Returns True if the signal direction is compatible with current sentiment.
        Institutions call this the 'macro filter' — no matter how good a signal
        looks technically, you don't fight extreme market sentiment.
        """
        bias = sentiment.trading_bias
        if bias == "BOTH":
            return True
        if bias == "NONE":
            return False
        if bias == "BUY_ONLY":
            return signal_direction == "BUY"
        if bias == "SELL_ONLY":
            return signal_direction == "SELL"
        return True

    def get_confidence_adjustment(self, sentiment: SentimentReading, signal_direction: str = "BUY") -> float:
        """
        Returns a multiplier for signal confidence based on sentiment alignment.
        Direction-aware: contrarian signals get a boost, trend-following signals get a penalty.
        - BUY in Extreme Fear = contrarian boost (fearful market = buying opportunity)
        - SELL in Extreme Greed = contrarian boost (greedy market = selling opportunity)
        - BUY in Extreme Greed = strong penalty (chasing tops)
        - SELL in Extreme Fear = strong penalty (panic selling with crowd)
        """
        value = sentiment.value
        is_buy = signal_direction == "BUY"
        if value <= 24:    # Extreme Fear
            return 1.25 if is_buy else 0.60
        if value <= 44:    # Fear
            return 1.10 if is_buy else 0.80
        if value <= 55:    # Neutral
            return 1.00
        if value <= 74:    # Greed
            return 0.80 if is_buy else 1.10
        return 0.60 if is_buy else 1.25  # Extreme Greed: BUY penalised, SELL boosted
