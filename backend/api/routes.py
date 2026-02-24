import logging
from datetime import datetime, timedelta
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc

from models.database import get_db_session, TradeRecord, BotSettings
from models.schemas import BotSettingsSchema, TradeRecordSchema
from engine.trading_engine import trading_engine, STRATEGY_REGISTRY

logger = logging.getLogger(__name__)
router = APIRouter()


def _settings_row_to_dict(row: BotSettings) -> dict:
    return {
        "is_running": row.is_running,
        "paper_trading_enabled": row.paper_trading_enabled,
        "paper_balance": row.paper_balance,
        "max_portfolio_risk_percent": row.max_portfolio_risk_percent,
        "max_drawdown_percent": row.max_drawdown_percent,
        "default_stop_loss_percent": row.default_stop_loss_percent,
        "default_take_profit_percent": row.default_take_profit_percent,
        "max_concurrent_positions": row.max_concurrent_positions,
        "active_strategies": row.active_strategies.split(",") if row.active_strategies else [],
        "active_symbols": row.active_symbols.split(",") if row.active_symbols else [],
        "hft_mode": getattr(row, "hft_mode", False),
    }


async def _get_or_create_settings(session: AsyncSession) -> BotSettings:
    result = await session.execute(select(BotSettings).where(BotSettings.id == 1))
    settings = result.scalar_one_or_none()
    if settings is None:
        settings = BotSettings()
        session.add(settings)
        await session.commit()
        await session.refresh(settings)
    return settings


@router.get("/status")
async def get_bot_status(session: AsyncSession = Depends(get_db_session)):
    settings = await _get_or_create_settings(session)
    engine_status = trading_engine.get_status()
    return {
        **engine_status,
        "paper_trading": settings.paper_trading_enabled,
        "hft_mode": trading_engine.hft_mode,
    }


@router.get("/settings")
async def get_settings(session: AsyncSession = Depends(get_db_session)):
    settings = await _get_or_create_settings(session)
    return _settings_row_to_dict(settings)


@router.put("/settings")
async def update_settings(payload: BotSettingsSchema, session: AsyncSession = Depends(get_db_session)):
    settings = await _get_or_create_settings(session)
    settings.paper_trading_enabled = payload.paper_trading_enabled
    settings.paper_balance = payload.paper_balance
    settings.max_portfolio_risk_percent = payload.max_portfolio_risk_percent
    settings.max_drawdown_percent = payload.max_drawdown_percent
    settings.default_stop_loss_percent = payload.default_stop_loss_percent
    settings.default_take_profit_percent = payload.default_take_profit_percent
    settings.max_concurrent_positions = payload.max_concurrent_positions
    settings.active_strategies = ",".join(payload.active_strategies)
    settings.active_symbols = ",".join(payload.active_symbols)
    settings.hft_mode = payload.hft_mode
    settings.updated_at = datetime.utcnow()
    await session.commit()
    return {"message": "Settings updated", "settings": _settings_row_to_dict(settings)}


@router.post("/bot/start")
async def start_bot(session: AsyncSession = Depends(get_db_session)):
    if trading_engine.is_running:
        raise HTTPException(status_code=400, detail="Bot is already running")
    settings = await _get_or_create_settings(session)
    settings_dict = _settings_row_to_dict(settings)
    await trading_engine.start(settings_dict)
    settings.is_running = True
    await session.commit()
    return {"message": "Bot started", "paper_trading": settings.paper_trading_enabled}


@router.post("/bot/stop")
async def stop_bot(session: AsyncSession = Depends(get_db_session)):
    await trading_engine.stop()
    settings = await _get_or_create_settings(session)
    settings.is_running = False
    await session.commit()
    return {"message": "Bot stopped"}


@router.get("/portfolio")
async def get_portfolio(session: AsyncSession = Depends(get_db_session)):
    stats = trading_engine.get_portfolio_stats()

    result = await session.execute(select(TradeRecord))
    all_trades = result.scalars().all()

    winning_trades = [t for t in all_trades if t.profit_loss and t.profit_loss > 0 and t.status == "closed"]
    losing_trades = [t for t in all_trades if t.profit_loss and t.profit_loss <= 0 and t.status == "closed"]
    closed_trades = [t for t in all_trades if t.status == "closed"]

    total_wins = sum(t.profit_loss for t in winning_trades) if winning_trades else 0.0
    total_losses = abs(sum(t.profit_loss for t in losing_trades)) if losing_trades else 0.0
    profit_factor = (total_wins / total_losses) if total_losses > 0 else float("inf")

    win_rate = (len(winning_trades) / len(closed_trades) * 100) if closed_trades else 0.0

    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    week_ago = today - timedelta(days=7)
    daily_pnl = sum(t.profit_loss for t in closed_trades if t.closed_at and t.closed_at >= today and t.profit_loss)
    weekly_pnl = sum(t.profit_loss for t in closed_trades if t.closed_at and t.closed_at >= week_ago and t.profit_loss)

    max_drawdown = 0.0
    running_peak = 0.0
    running_pnl = 0.0
    sorted_closed = sorted(closed_trades, key=lambda t: t.closed_at or datetime.min)
    for trade in sorted_closed:
        running_pnl += trade.profit_loss or 0
        if running_pnl > running_peak:
            running_peak = running_pnl
        dd = running_peak - running_pnl
        if dd > max_drawdown:
            max_drawdown = dd

    return {
        **stats,
        "total_trades": len(all_trades),
        "winning_trades": len(winning_trades),
        "losing_trades": len(losing_trades),
        "win_rate": round(win_rate, 2),
        "profit_factor": round(profit_factor, 2) if profit_factor != float("inf") else 9999.0,
        "max_drawdown": round(max_drawdown, 2),
        "daily_pnl": round(daily_pnl, 2),
        "weekly_pnl": round(weekly_pnl, 2),
    }


@router.get("/positions")
async def get_active_positions():
    return trading_engine.get_active_positions()


@router.get("/trades")
async def get_trade_history(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    symbol: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    session: AsyncSession = Depends(get_db_session),
):
    query = select(TradeRecord).order_by(desc(TradeRecord.opened_at))
    if symbol:
        query = query.where(TradeRecord.symbol == symbol)
    if status:
        query = query.where(TradeRecord.status == status)
    query = query.offset(offset).limit(limit)
    result = await session.execute(query)
    trades = result.scalars().all()
    return [TradeRecordSchema.model_validate(t) for t in trades]


@router.get("/trades/count")
async def get_trade_count(session: AsyncSession = Depends(get_db_session)):
    result = await session.execute(select(func.count(TradeRecord.id)))
    return {"count": result.scalar_one()}


@router.get("/market/prices")
async def get_market_prices():
    return trading_engine.market_prices


@router.get("/signals/recent")
async def get_recent_signals():
    return trading_engine.recent_signals[:20]


@router.get("/strategies")
async def get_strategies():
    strategy_info = []
    for key, strategy in STRATEGY_REGISTRY.items():
        strategy_info.append({
            "id": key,
            "name": strategy.name,
            "enabled": strategy.enabled,
        })
    return strategy_info


@router.patch("/strategies/{strategy_id}/toggle")
async def toggle_strategy(strategy_id: str):
    strategy = STRATEGY_REGISTRY.get(strategy_id)
    if strategy is None:
        raise HTTPException(status_code=404, detail=f"Strategy '{strategy_id}' not found")
    strategy.enabled = not strategy.enabled
    return {"strategy_id": strategy_id, "enabled": strategy.enabled}


@router.get("/analytics/pnl-chart")
async def get_pnl_chart_data(days: int = Query(default=30, ge=1, le=365), session: AsyncSession = Depends(get_db_session)):
    since = datetime.utcnow() - timedelta(days=days)
    result = await session.execute(
        select(TradeRecord)
        .where(TradeRecord.status == "closed")
        .where(TradeRecord.closed_at >= since)
        .order_by(TradeRecord.closed_at)
    )
    trades = result.scalars().all()
    cumulative_pnl = 0.0
    chart_data = []
    for trade in trades:
        cumulative_pnl += trade.profit_loss or 0
        chart_data.append({
            "date": trade.closed_at.strftime("%Y-%m-%d %H:%M") if trade.closed_at else "",
            "pnl": round(trade.profit_loss or 0, 2),
            "cumulative_pnl": round(cumulative_pnl, 2),
            "symbol": trade.symbol,
            "strategy": trade.strategy,
        })
    return chart_data


@router.get("/analytics/var")
async def get_var_report():
    """Value at Risk (VaR 95%/99%), CVaR, Sharpe, and Sortino ratio — institutional portfolio risk metrics."""
    return trading_engine.get_var_report()


@router.get("/analytics/sentiment")
async def get_sentiment():
    """Current Fear & Greed Index sentiment reading and trading bias."""
    return trading_engine.get_sentiment()


@router.get("/analytics/funding-rates")
async def get_funding_rates():
    """Perpetual futures funding rates per symbol — crypto-specific alpha signal."""
    return trading_engine.get_funding_rates()


@router.get("/analytics/ml-training-data")
async def get_ml_training_data(session: AsyncSession = Depends(get_db_session)):
    """
    Export all closed trades with their signal_features as a flat dataset
    ready for XGBoost/LightGBM training (Phase 3 of the AI roadmap).

    Each row = one closed trade.
    Target variable: profitable (1) or not (0).
    Features: all indicator values captured at entry time.
    """
    import json as _json
    result = await session.execute(
        select(TradeRecord)
        .where(TradeRecord.status == "closed")
        .where(TradeRecord.signal_features.is_not(None))
        .order_by(TradeRecord.closed_at)
    )
    trades = result.scalars().all()

    rows = []
    for trade in trades:
        try:
            features = _json.loads(trade.signal_features)
        except (TypeError, ValueError):
            features = {}

        flat_row = {
            "trade_id": trade.id,
            "symbol": trade.symbol,
            "side": trade.side,
            "entry_price": trade.entry_price,
            "exit_price": trade.exit_price,
            "profit_loss": trade.profit_loss,
            "profit_loss_percent": trade.profit_loss_percent,
            "exit_reason": trade.exit_reason,
            "hold_duration_minutes": (
                round((trade.closed_at - trade.opened_at).total_seconds() / 60, 1)
                if trade.closed_at and trade.opened_at else None
            ),
            "profitable": 1 if (trade.profit_loss or 0) > 0 else 0,
            **{k: v for k, v in features.items() if k not in ("raw_signal_details", "symbol")},
        }
        rows.append(flat_row)

    return {
        "total_samples": len(rows),
        "positive_samples": sum(r["profitable"] for r in rows),
        "negative_samples": sum(1 - r["profitable"] for r in rows),
        "features": list(rows[0].keys()) if rows else [],
        "data": rows,
        "note": "Use this dataset to train XGBoost once you have 500+ samples. Target: 'profitable'. Drop: trade_id, entry_price, exit_price, profit_loss, profit_loss_percent.",
    }


@router.get("/analytics/live-performance")
async def get_live_performance():
    """Rolling Sharpe ratio, Kelly fraction, and dynamic weight per strategy — live from the engine."""
    return trading_engine.get_performance_summary()


@router.get("/analytics/regime")
async def get_regime_info():
    """Current market regime per symbol and strategy allocation weights."""
    return trading_engine.get_regime_info()


@router.get("/analytics/strategy-performance")
async def get_strategy_performance(session: AsyncSession = Depends(get_db_session)):
    result = await session.execute(
        select(TradeRecord).where(TradeRecord.status == "closed")
    )
    trades = result.scalars().all()
    performance: dict[str, dict] = {}
    for trade in trades:
        if trade.strategy not in performance:
            performance[trade.strategy] = {"total": 0, "wins": 0, "losses": 0, "total_pnl": 0.0}
        performance[trade.strategy]["total"] += 1
        pnl = trade.profit_loss or 0
        performance[trade.strategy]["total_pnl"] += pnl
        if pnl > 0:
            performance[trade.strategy]["wins"] += 1
        else:
            performance[trade.strategy]["losses"] += 1

    result_list = []
    for strategy_name, stats in performance.items():
        total = stats["total"]
        result_list.append({
            "strategy": strategy_name,
            "total_trades": total,
            "wins": stats["wins"],
            "losses": stats["losses"],
            "win_rate": round(stats["wins"] / total * 100, 1) if total > 0 else 0,
            "total_pnl": round(stats["total_pnl"], 2),
        })
    return result_list
