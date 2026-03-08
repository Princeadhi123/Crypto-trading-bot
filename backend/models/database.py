from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import String, Float, DateTime, Boolean, Integer, Text, TypeDecorator
from datetime import datetime
from typing import Optional
import os
from dotenv import load_dotenv

load_dotenv()


class EncryptedText(TypeDecorator):
    """SQLAlchemy column type that transparently encrypts/decrypts using Fernet.
    Falls back to plain text when FIELD_ENCRYPTION_KEY is not configured.
    Safe to add to an existing DB — existing unencrypted values are returned as-is.
    """
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        from utils.encryption import encrypt_value
        return encrypt_value(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        from utils.encryption import decrypt_value
        return decrypt_value(value)

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./trading_bot.db")

engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class TradeRecord(Base):
    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(20))
    side: Mapped[str] = mapped_column(String(10))
    strategy: Mapped[str] = mapped_column(Text)
    entry_price: Mapped[float] = mapped_column(Float)
    exit_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    quantity: Mapped[float] = mapped_column(Float)
    profit_loss: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    profit_loss_percent: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    stop_loss_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    take_profit_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="open")
    is_paper_trade: Mapped[bool] = mapped_column(Boolean, default=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(EncryptedText, nullable=True)
    signal_features: Mapped[Optional[str]] = mapped_column(EncryptedText, nullable=True)
    exit_reason: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)


class BotSettings(Base):
    __tablename__ = "bot_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    is_running: Mapped[bool] = mapped_column(Boolean, default=False)
    paper_trading_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    paper_balance: Mapped[float] = mapped_column(Float, default=10000.0)
    max_portfolio_risk_percent: Mapped[float] = mapped_column(Float, default=2.0)
    max_drawdown_percent: Mapped[float] = mapped_column(Float, default=10.0)
    default_stop_loss_percent: Mapped[float] = mapped_column(Float, default=2.0)
    default_take_profit_percent: Mapped[float] = mapped_column(Float, default=4.0)
    max_concurrent_positions: Mapped[int] = mapped_column(Integer, default=5)
    active_strategies: Mapped[str] = mapped_column(Text, default="rsi,macd,bollinger,scalping")
    active_symbols: Mapped[str] = mapped_column(Text, default="BTC/USDT,ETH/USDT,BNB/USDT,SOL/USDT")
    hft_mode: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


async def init_database():
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


async def get_db_session():
    async with AsyncSessionLocal() as session:
        yield session
