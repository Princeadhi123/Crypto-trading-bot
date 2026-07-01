"""
Shared ML feature schema — single source of truth used by BOTH the offline
trainer (ml/trainer.py) and the runtime signal filter (engine/ml_filter.py).

Any change to the feature set must be made here so that training and inference
never drift apart.
"""
from typing import Optional

import pandas as pd

# Categorical features — one-hot encoded inside the sklearn pipeline
CATEGORICAL_FEATURES = ["side", "regime", "sentiment_bias", "funding_bias", "symbol"]

# Numeric features — passed through to the gradient boosting model directly
NUMERIC_FEATURES = [
    "ensemble_confidence",
    "regime_boost",
    "agreeing_strategies_count",
    "disagreeing_strategies_count",
    "sentiment_value",
    "funding_rate",
    "kelly_fraction",
    "stop_distance_pct",
    "risk_reward_ratio",
    "portfolio_drawdown_at_entry",
    "open_positions_at_entry",
    "hft_mode",
    "hour_of_day",
]

ALL_FEATURES = CATEGORICAL_FEATURES + NUMERIC_FEATURES

TARGET_COLUMN = "profitable"

_NUMERIC_DEFAULTS = {
    "ensemble_confidence": 0.5,
    "regime_boost": 1.0,
    "agreeing_strategies_count": 1,
    "disagreeing_strategies_count": 0,
    "sentiment_value": 50,
    "funding_rate": 0.0,
    "kelly_fraction": 0.02,
    "stop_distance_pct": 1.0,
    "risk_reward_ratio": 1.0,
    "portfolio_drawdown_at_entry": 0.0,
    "open_positions_at_entry": 0,
    "hft_mode": 0,
    "hour_of_day": 12,
}

_CATEGORICAL_DEFAULTS = {
    "side": "BUY",
    "regime": "ranging",
    "sentiment_bias": "BOTH",
    "funding_bias": "NEUTRAL",
    "symbol": "BTC/USDT",
}


def _normalize_categorical(record: dict, normalized: dict):
    # 'side' may arrive as 'direction' (live ml_features dict uses 'direction')
    side = record.get("side") or record.get("direction") or _CATEGORICAL_DEFAULTS["side"]
    normalized["side"] = str(side).upper()

    for column in ("regime", "sentiment_bias", "funding_bias", "symbol"):
        value = record.get(column)
        normalized[column] = str(value) if value is not None and str(value) != "nan" else _CATEGORICAL_DEFAULTS[column]


def _parse_hft_mode(raw) -> int:
    if isinstance(raw, str):
        return 1 if raw.strip().lower() in ("true", "1") else 0
    return int(bool(raw))

def _parse_numeric(column: str, raw) -> float:
    try:
        value = float(raw)
        if value != value:  # NaN check
            return _NUMERIC_DEFAULTS[column]
        return value
    except (TypeError, ValueError):
        return _NUMERIC_DEFAULTS[column]

def _normalize_numeric(record: dict, normalized: dict):
    for column in NUMERIC_FEATURES:
        if column == "hour_of_day":
            continue
        if column == "hft_mode":
            normalized["hft_mode"] = _parse_hft_mode(record.get("hft_mode", 0))
            continue
        
        raw = record.get(column, _NUMERIC_DEFAULTS[column])
        normalized[column] = _parse_numeric(column, raw)


def _normalize_hour(record: dict, opened_at: Optional[object], normalized: dict):
    # hour_of_day: from explicit value, opened_at timestamp, or current UTC hour
    hour = record.get("hour_of_day")
    if hour is None and opened_at is not None:
        try:
            hour = pd.Timestamp(opened_at).hour
        except (TypeError, ValueError):
            hour = None
    if hour is None:
        from datetime import datetime, timezone
        hour = datetime.now(timezone.utc).hour
    normalized["hour_of_day"] = int(hour)


def normalize_record(record: dict, opened_at: Optional[object] = None) -> dict:
    """Normalize a raw feature record (from live engine, DB JSON, or CSV row)
    into the canonical feature dict expected by the model pipeline."""
    normalized: dict = {}
    _normalize_categorical(record, normalized)
    _normalize_numeric(record, normalized)
    _normalize_hour(record, opened_at, normalized)
    return normalized


def build_feature_frame(records: list[dict]) -> pd.DataFrame:
    """Build a model-ready DataFrame (canonical column order) from raw records."""
    normalized = [normalize_record(r, r.get("opened_at")) for r in records]
    frame = pd.DataFrame(normalized)
    return frame[ALL_FEATURES]
