import logging
from datetime import datetime, timedelta
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc, and_, Integer, case

from models.database import get_db_session, TradeRecord, BotSettings
from models.schemas import BotSettingsSchema, TradeRecordSchema
from engine.trading_engine import trading_engine, STRATEGY_REGISTRY, HFT_STRATEGY_REGISTRY

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
        "active_strategies": [s for s in row.active_strategies.split(",") if s] if row.active_strategies else [],
        "active_symbols": [s for s in row.active_symbols.split(",") if s] if row.active_symbols else [],
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
    settings_dict = _settings_row_to_dict(settings)
    # If bot is running in paper mode, show the actual in-memory balance (not stale DB value)
    # This reflects the real available cash after positions are opened
    if trading_engine.is_running and settings.paper_trading_enabled:
        settings_dict["paper_balance"] = trading_engine.paper_balance
    return settings_dict


@router.put("/settings")
async def update_settings(payload: BotSettingsSchema, session: AsyncSession = Depends(get_db_session)):
    settings = await _get_or_create_settings(session)
    # Use in-memory balance if bot is running, otherwise use DB value
    old_paper_balance = trading_engine.paper_balance if (trading_engine.is_running and settings.paper_trading_enabled) else settings.paper_balance
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

    # Update paper balance in memory ONLY if user actually changed it in settings form
    # Don't overwrite in-memory balance when user is just changing other settings (e.g., max drawdown)
    if settings.paper_trading_enabled and payload.paper_balance != old_paper_balance:
        trading_engine.paper_balance = payload.paper_balance
        # Reset peak portfolio value to new balance to prevent incorrect drawdown calculations
        trading_engine.risk_manager.peak_portfolio_value = payload.paper_balance

    # Hot-reload safe settings into the running engine (risk params, strategies, symbols)
    hot_reload_result = {}
    if trading_engine.is_running:
        hot_reload_result = trading_engine.apply_settings(_settings_row_to_dict(settings))

    return {
        "message": "Settings updated",
        "settings": _settings_row_to_dict(settings),
        **hot_reload_result,
    }


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
    is_paper = trading_engine.paper_trading

    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    week_ago = today - timedelta(days=7)

    # Aggregate counts and sums with DB-side SQL — avoids loading full table into RAM
    base = and_(TradeRecord.is_paper_trade == is_paper, TradeRecord.status == "closed")

    r_total = await session.execute(select(func.count(TradeRecord.id)).where(TradeRecord.is_paper_trade == is_paper))
    total_trades = r_total.scalar_one()

    r_wins = await session.execute(select(func.count(TradeRecord.id), func.sum(TradeRecord.profit_loss))
                                   .where(and_(base, TradeRecord.profit_loss > 0)))
    wins_row = r_wins.one()
    winning_count = wins_row[0] or 0
    total_wins = float(wins_row[1] or 0.0)

    r_loss = await session.execute(select(func.count(TradeRecord.id), func.sum(TradeRecord.profit_loss))
                                   .where(and_(base, TradeRecord.profit_loss <= 0)))
    loss_row = r_loss.one()
    losing_count = loss_row[0] or 0
    total_losses = abs(float(loss_row[1] or 0.0))

    r_closed = await session.execute(select(func.count(TradeRecord.id)).where(base))
    closed_count = r_closed.scalar_one() or 0

    profit_factor = (total_wins / total_losses) if total_losses > 0 else float("inf")
    win_rate = (winning_count / closed_count * 100) if closed_count else 0.0

    r_daily = await session.execute(select(func.coalesce(func.sum(TradeRecord.profit_loss), 0.0))
                                    .where(and_(base, TradeRecord.closed_at >= today)))
    daily_pnl = float(r_daily.scalar_one())

    r_weekly = await session.execute(select(func.coalesce(func.sum(TradeRecord.profit_loss), 0.0))
                                     .where(and_(base, TradeRecord.closed_at >= week_ago)))
    weekly_pnl = float(r_weekly.scalar_one())

    # Max drawdown — fetch most recent 1000 trades (desc), reverse for chronological order
    r_dd = await session.execute(
        select(TradeRecord.profit_loss, TradeRecord.closed_at)
        .where(base).order_by(desc(TradeRecord.closed_at)).limit(1000)
    )
    dd_rows = list(reversed(r_dd.all()))
    max_drawdown = 0.0
    running_peak = 0.0
    running_pnl = 0.0
    for pnl_val, _ in dd_rows:
        running_pnl += pnl_val or 0
        if running_pnl > running_peak:
            running_peak = running_pnl
        dd = running_peak - running_pnl
        if dd > max_drawdown:
            max_drawdown = dd

    return {
        **stats,
        "total_trades": total_trades,
        "winning_trades": winning_count,
        "losing_trades": losing_count,
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
        query = query.where(TradeRecord.symbol.ilike(f"%{symbol}%"))
    if status:
        query = query.where(TradeRecord.status == status)
    query = query.offset(offset).limit(limit)
    result = await session.execute(query)
    trades = result.scalars().all()
    return [TradeRecordSchema.model_validate(t) for t in trades]


@router.get("/trades/count")
async def get_trade_count(
    symbol: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    session: AsyncSession = Depends(get_db_session),
):
    query = select(func.count(TradeRecord.id))
    if symbol:
        query = query.where(TradeRecord.symbol.ilike(f"%{symbol}%"))
    if status:
        query = query.where(TradeRecord.status == status)
    result = await session.execute(query)
    return {"count": result.scalar_one()}


@router.get("/market/prices")
async def get_market_prices():
    return trading_engine.market_prices


@router.get("/signals/recent")
async def get_recent_signals():
    return trading_engine.recent_signals[:20]


@router.get("/strategies")
async def get_strategies():
    registry = trading_engine._strategy_registry
    strategy_info = []
    for key, strategy in registry.items():
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
    new_state = not strategy.enabled
    strategy.enabled = new_state
    # Sync to HFT registry so toggling works in both Standard and HFT mode
    hft_strategy = HFT_STRATEGY_REGISTRY.get(strategy_id)
    if hft_strategy:
        hft_strategy.enabled = new_state
    return {"strategy_id": strategy_id, "enabled": new_state}


@router.get("/analytics/pnl-chart")
async def get_pnl_chart_data(days: int = Query(default=30, ge=1, le=365), session: AsyncSession = Depends(get_db_session)):
    since = datetime.utcnow() - timedelta(days=days)
    is_paper = trading_engine.paper_trading
    base_filter = and_(TradeRecord.status == "closed", TradeRecord.is_paper_trade == is_paper)
    # Bug #6: Fetch historical PnL before the window as equity baseline so chart
    # continues from the correct watermark instead of always restarting at 0
    baseline_result = await session.execute(
        select(func.coalesce(func.sum(TradeRecord.profit_loss), 0.0))
        .where(and_(base_filter, TradeRecord.closed_at < since))
    )
    cumulative_pnl = float(baseline_result.scalar_one())
    result = await session.execute(
        select(TradeRecord)
        .where(base_filter)
        .where(TradeRecord.closed_at >= since)
        .order_by(TradeRecord.closed_at)
    )
    trades = result.scalars().all()
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

    # Bug #7: derive the canonical feature header from the union of ALL row keys
    # so a corrupted/empty first signal_features blob never strips ML columns.
    all_keys: dict[str, None] = {}
    for row in rows:
        all_keys.update(dict.fromkeys(row.keys()))
    return {
        "total_samples": len(rows),
        "positive_samples": sum(r["profitable"] for r in rows),
        "negative_samples": sum(1 - r["profitable"] for r in rows),
        "features": list(all_keys.keys()),
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
    is_paper = trading_engine.paper_trading
    # Fetch raw joined labels — split on " + " and credit each base strategy individually
    rows = await session.execute(
        select(
            TradeRecord.strategy,
            TradeRecord.profit_loss,
        )
        .where(and_(TradeRecord.status == "closed", TradeRecord.is_paper_trade == is_paper))
    )
    perf: dict[str, dict] = {}
    for strategy_label, pnl in rows.all():
        for base_strategy in (strategy_label or "unknown").split(" + "):
            base_strategy = base_strategy.strip()
            if base_strategy not in perf:
                perf[base_strategy] = {"total": 0, "wins": 0, "total_pnl": 0.0}
            perf[base_strategy]["total"] += 1
            perf[base_strategy]["total_pnl"] += pnl or 0.0
            if (pnl or 0.0) > 0:
                perf[base_strategy]["wins"] += 1
    result_list = []
    for strategy_name, stats in perf.items():
        total = stats["total"]
        wins = stats["wins"]
        result_list.append({
            "strategy": strategy_name,
            "total_trades": total,
            "wins": wins,
            "losses": total - wins,
            "win_rate": round(wins / total * 100, 1) if total > 0 else 0,
            "total_pnl": round(stats["total_pnl"], 2),
        })
    return result_list
