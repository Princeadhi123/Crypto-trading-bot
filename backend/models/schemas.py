from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime


class TradeRecordSchema(BaseModel):
    id: int
    symbol: str
    side: str
    strategy: str
    entry_price: float
    exit_price: Optional[float] = None
    quantity: float
    profit_loss: Optional[float] = None
    profit_loss_percent: Optional[float] = None
    stop_loss_price: Optional[float] = None
    take_profit_price: Optional[float] = None
    status: str
    is_paper_trade: bool
    opened_at: datetime
    closed_at: Optional[datetime] = None
    notes: Optional[str] = None

    class Config:
        from_attributes = True


class BotSettingsSchema(BaseModel):
    is_running: bool = False
    paper_trading_enabled: bool = True
    paper_balance: float = 10000.0
    max_portfolio_risk_percent: float = Field(default=2.0, ge=0.1, le=10.0)
    max_drawdown_percent: float = Field(default=10.0, ge=1.0, le=50.0)
    default_stop_loss_percent: float = Field(default=2.0, ge=0.5, le=20.0)
    default_take_profit_percent: float = Field(default=4.0, ge=1.0, le=50.0)
    max_concurrent_positions: int = Field(default=5, ge=1, le=20)
    active_strategies: List[str] = ["rsi", "macd", "bollinger", "scalping"]
    active_symbols: List[str] = ["BTC/USDT", "ETH/USDT", "BNB/USDT", "SOL/USDT"]
    hft_mode: bool = False


class PortfolioStatsSchema(BaseModel):
    total_balance: float
    available_balance: float
    total_equity: float
    unrealized_pnl: float
    realized_pnl: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    profit_factor: float
    max_drawdown: float
    current_drawdown: float
    daily_pnl: float
    weekly_pnl: float


class ActivePositionSchema(BaseModel):
    symbol: str
    side: str
    strategy: str
    entry_price: float
    current_price: float
    quantity: float
    unrealized_pnl: float
    unrealized_pnl_percent: float
    stop_loss_price: float
    take_profit_price: float
    opened_at: datetime
    trade_id: int


class MarketDataSchema(BaseModel):
    symbol: str
    price: float
    change_24h: float
    change_24h_percent: float
    volume_24h: float
    high_24h: float
    low_24h: float


class SignalSchema(BaseModel):
    symbol: str
    strategy: str
    signal_type: str
    strength: float
    price: float
    timestamp: datetime
    details: dict


class BotStatusSchema(BaseModel):
    is_running: bool
    paper_trading: bool
    active_positions: int
    total_signals_today: int
    trades_today: int
    uptime_seconds: float
    last_tick: Optional[datetime] = None
