from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime
import pandas as pd


@dataclass
class TradingSignal:
    symbol: str
    strategy_name: str
    signal_type: str  # "BUY", "SELL", "HOLD"
    strength: float   # 0.0 to 1.0
    price: float
    suggested_stop_loss: float
    suggested_take_profit: float
    timestamp: datetime = field(default_factory=datetime.utcnow)
    details: dict = field(default_factory=dict)


class BaseStrategy(ABC):
    def __init__(self, name: str):
        self.name = name
        self.enabled = True

    @abstractmethod
    def compute_signal(self, symbol: str, ohlcv_dataframe: pd.DataFrame) -> Optional[TradingSignal]:
        """
        Analyzes OHLCV data and returns a TradingSignal or None if no signal.
        ohlcv_dataframe columns: ['timestamp', 'open', 'high', 'low', 'close', 'volume']
        """
        pass

    def calculate_stop_loss(self, price: float, side: str, stop_loss_percent: float) -> float:
        if side == "BUY":
            return round(price * (1 - stop_loss_percent / 100), 8)
        return round(price * (1 + stop_loss_percent / 100), 8)

    def calculate_take_profit(self, price: float, side: str, take_profit_percent: float) -> float:
        if side == "BUY":
            return round(price * (1 + take_profit_percent / 100), 8)
        return round(price * (1 - take_profit_percent / 100), 8)

    def requires_minimum_candles(self) -> int:
        return 50
