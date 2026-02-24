import React, { useState, useEffect, useCallback } from 'react'
import {
  Activity, DollarSign, Target, AlertTriangle,
  Play, Square, Zap, TrendingUp, TrendingDown, BarChart2
} from 'lucide-react'
import { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts'
import { botApi } from '../api'
import {
  StatCard, EmptyState,
  ChartTooltip, fmtCurrency, fmtPct, fmtPrice, fmtNumber
} from '../components/ui'

export default function Dashboard({ wsEvents }) {
  const [status, setStatus] = useState(null)
  const [portfolio, setPortfolio] = useState(null)
  const [positions, setPositions] = useState([])
  const [prices, setPrices] = useState({})
  const [pnlChart, setPnlChart] = useState([])
  const [recentSignals, setRecentSignals] = useState([])
  const [loading, setLoading] = useState(false)
  const [botActionLoading, setBotActionLoading] = useState(false)

  const fetchAll = useCallback(async () => {
    try {
      const [statusRes, portfolioRes, positionsRes, pricesRes, signalsRes, chartRes] = await Promise.all([
        botApi.getStatus(),
        botApi.getPortfolio(),
        botApi.getPositions(),
        botApi.getMarketPrices(),
        botApi.getRecentSignals(),
        botApi.getPnlChart(7),
      ])
      setStatus(statusRes.data)
      setPortfolio(portfolioRes.data)
      setPositions(positionsRes.data)
      setPrices(pricesRes.data)
      setRecentSignals(signalsRes.data)
      setPnlChart(chartRes.data)
    } catch (e) {
      console.error('Dashboard fetch error:', e)
    }
  }, [])

  useEffect(() => {
    setLoading(true)
    fetchAll().finally(() => setLoading(false))
    const interval = setInterval(fetchAll, 15000)
    return () => clearInterval(interval)
  }, [fetchAll])

  useEffect(() => {
    const latest = wsEvents[0]
    if (!latest) return
    if (latest.event === 'price_update') {
      setPrices(latest.data.prices || {})
    } else if (latest.event === 'new_trade' || latest.event === 'trade_closed') {
      fetchAll()
    }
  }, [wsEvents, fetchAll])

  const toggleBot = async () => {
    setBotActionLoading(true)
    try {
      if (status?.is_running) {
        await botApi.stopBot()
      } else {
        await botApi.startBot()
      }
      await fetchAll()
    } catch (e) {
      console.error('Bot toggle error:', e)
    } finally {
      setBotActionLoading(false)
    }
  }

  const isRunning = status?.is_running
  const pnl = portfolio?.realized_pnl || 0
  const pnlPositive = pnl >= 0
  const winRate = portfolio?.win_rate || 0

  return (
    <div className="p-6 space-y-5 animate-fade-in">

      {/* ── Header ─────────────────────────────────────────────── */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-[18px] font-bold tracking-tight" style={{ color: 'var(--text-primary)' }}>
            Trading Dashboard
          </h1>
          <div className="flex items-center gap-2 mt-1">
            <span className={`badge ${status?.paper_trading ? 'badge-paper' : 'badge-live'}`}>
              {status?.paper_trading ? 'Paper Trading' : '⚡ Live Trading'}
            </span>
            {status?.hft_mode && <span className="badge badge-hft">⚡ HFT · 1m</span>}
            {isRunning && (
              <div className="flex items-center gap-1.5">
                <div className="pulse-dot" />
                <span className="text-[11px] font-medium" style={{ color: '#34d399' }}>Active</span>
              </div>
            )}
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button className="btn-ghost" onClick={fetchAll}>
            <Activity size={13} className={loading ? 'animate-spin' : ''} />
            Refresh
          </button>
          <button
            onClick={toggleBot}
            disabled={botActionLoading}
            className={isRunning ? 'btn-danger' : 'btn-primary'}
          >
            {isRunning ? <><Square size={12} />Stop Bot</> : <><Play size={12} />Start Bot</>}
          </button>
        </div>
      </div>

      {/* ── Paper trading notice ────────────────────────────────── */}
      {status?.paper_trading && (
        <div
          className="flex items-center gap-3 px-4 py-3 rounded-xl text-[12px]"
          style={{ background: 'rgba(245,158,11,0.06)', border: '1px solid rgba(245,158,11,0.15)' }}
        >
          <AlertTriangle size={13} style={{ color: '#f59e0b', flexShrink: 0 }} />
          <span style={{ color: '#fcd34d' }}>
            <strong>Paper Trading Mode</strong> — Simulated balance only. No real funds at risk.
          </span>
        </div>
      )}

      {/* ── Stat row ────────────────────────────────────────────── */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard
          title="Portfolio Value"
          value={fmtCurrency(portfolio?.total_equity)}
          subtitle="Total equity"
          icon={DollarSign}
          accentColor="#10b981"
        />
        <StatCard
          title="Realized P&L"
          value={`${pnlPositive ? '+' : ''}${fmtCurrency(pnl)}`}
          subtitle={`Win rate: ${winRate.toFixed(1)}%`}
          icon={pnlPositive ? TrendingUp : TrendingDown}
          accentColor={pnlPositive ? '#10b981' : '#ef4444'}
        />
        <StatCard
          title="Open Positions"
          value={positions.length}
          subtitle={`Unrealized: ${fmtCurrency(portfolio?.unrealized_pnl)}`}
          icon={Activity}
          accentColor="#3b82f6"
        />
        <StatCard
          title="Win Rate"
          value={`${winRate.toFixed(1)}%`}
          subtitle={`${portfolio?.winning_trades || 0}W / ${portfolio?.losing_trades || 0}L`}
          icon={Target}
          accentColor={winRate >= 50 ? '#10b981' : '#ef4444'}
        />
      </div>

      {/* ── Chart + Prices ──────────────────────────────────────── */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
        <div className="lg:col-span-2 card">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-[13px] font-semibold" style={{ color: 'var(--text-primary)' }}>
              Cumulative P&L
            </h2>
            <span className="text-[11px]" style={{ color: 'var(--text-muted)' }}>7 days</span>
          </div>
          {pnlChart.length > 0 ? (
            <ResponsiveContainer width="100%" height={210}>
              <AreaChart data={pnlChart} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
                <defs>
                  <linearGradient id="pnlGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#10b981" stopOpacity={0.25} />
                    <stop offset="100%" stopColor="#10b981" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="2 4" stroke="rgba(255,255,255,0.04)" />
                <XAxis
                  dataKey="date"
                  tick={{ fontSize: 10, fill: 'var(--text-muted)' }}
                  tickFormatter={v => v.split(' ')[0]}
                  axisLine={false}
                  tickLine={false}
                />
                <YAxis
                  tick={{ fontSize: 10, fill: 'var(--text-muted)' }}
                  tickFormatter={v => `$${v}`}
                  axisLine={false}
                  tickLine={false}
                  width={52}
                />
                <Tooltip content={<ChartTooltip formatter={v => fmtCurrency(v)} />} />
                <Area
                  type="monotone"
                  dataKey="cumulative_pnl"
                  stroke="#10b981"
                  fill="url(#pnlGrad)"
                  strokeWidth={2}
                  dot={false}
                  activeDot={{ r: 4, fill: '#10b981', strokeWidth: 0 }}
                />
              </AreaChart>
            </ResponsiveContainer>
          ) : (
            <EmptyState
              icon={BarChart2}
              title="No trade data yet"
              description="Start the bot to begin generating performance history"
            />
          )}
        </div>

        <div className="card">
          <h2 className="text-[13px] font-semibold mb-4" style={{ color: 'var(--text-primary)' }}>
            Market Prices
          </h2>
          <div className="space-y-0">
            {Object.entries(prices).slice(0, 6).map(([symbol, price]) => (
              <div
                key={symbol}
                className="flex items-center justify-between py-2.5"
                style={{ borderBottom: '1px solid var(--border)' }}
              >
                <div className="flex items-center gap-2">
                  <div
                    className="w-6 h-6 rounded-lg flex items-center justify-center text-[9px] font-bold"
                    style={{ background: 'rgba(16,185,129,0.1)', color: '#10b981' }}
                  >
                    {symbol.slice(0, 2)}
                  </div>
                  <span className="text-[13px] font-medium" style={{ color: 'var(--text-secondary)' }}>
                    {symbol.replace('/USDT', '')}
                  </span>
                </div>
                <span className="text-[13px] font-semibold mono" style={{ color: 'var(--text-primary)' }}>
                  {fmtPrice(Number(price))}
                </span>
              </div>
            ))}
            {Object.keys(prices).length === 0 && (
              <p className="text-[12px] text-center py-6" style={{ color: 'var(--text-muted)' }}>
                Waiting for price data…
              </p>
            )}
          </div>
        </div>
      </div>

      {/* ── Positions + Signals ─────────────────────────────────── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
        <div className="card">
          <h2 className="text-[13px] font-semibold mb-4" style={{ color: 'var(--text-primary)' }}>
            Open Positions
          </h2>
          {positions.length === 0 ? (
            <EmptyState icon={Activity} title="No open positions" description="Signals will be executed automatically once the bot is running" />
          ) : (
            <div className="space-y-3">
              {positions.map((pos) => {
                const isProfit = pos.unrealized_pnl >= 0
                return (
                  <div
                    key={pos.trade_id}
                    className="rounded-xl p-3"
                    style={{
                      background: 'var(--bg-elevated)',
                      border: `1px solid ${isProfit ? 'rgba(16,185,129,0.15)' : 'rgba(239,68,68,0.15)'}`,
                    }}
                  >
                    <div className="flex items-center justify-between mb-2.5">
                      <div className="flex items-center gap-2">
                        <span className={`badge badge-${pos.side.toLowerCase()}`}>{pos.side}</span>
                        <span className="text-[13px] font-semibold" style={{ color: 'var(--text-primary)' }}>
                          {pos.symbol}
                        </span>
                      </div>
                      <div className="text-right">
                        <div className={`text-[14px] font-bold mono ${isProfit ? 'pnl-positive' : 'pnl-negative'}`}>
                          {isProfit ? '+' : ''}{fmtCurrency(pos.unrealized_pnl)}
                        </div>
                        <div className={`text-[11px] ${isProfit ? 'pnl-positive' : 'pnl-negative'}`}>
                          {fmtPct(pos.unrealized_pnl_percent)}
                        </div>
                      </div>
                    </div>
                    <div className="grid grid-cols-3 gap-2 text-[11px]" style={{ color: 'var(--text-muted)' }}>
                      <div>Entry <div className="font-medium mono mt-0.5" style={{ color: 'var(--text-secondary)' }}>{fmtPrice(pos.entry_price)}</div></div>
                      <div>Current <div className="font-medium mono mt-0.5" style={{ color: 'var(--text-primary)' }}>{fmtPrice(pos.current_price)}</div></div>
                      <div>Strategy <div className="font-medium mt-0.5 truncate" style={{ color: 'var(--text-secondary)', fontSize: 10 }}>{pos.strategy.split('+')[0].trim()}</div></div>
                    </div>
                    <div className="flex gap-4 mt-2.5 text-[10px]">
                      <span style={{ color: 'var(--text-muted)' }}>SL: <span style={{ color: '#f87171' }}>{fmtPrice(pos.stop_loss_price)}</span></span>
                      <span style={{ color: 'var(--text-muted)' }}>TP: <span style={{ color: '#34d399' }}>{fmtPrice(pos.take_profit_price)}</span></span>
                      {pos.trailing_stop_activated && (
                        <span className="badge badge-hft" style={{ fontSize: 9 }}>Trail Active</span>
                      )}
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </div>

        <div className="card">
          <h2 className="text-[13px] font-semibold mb-4" style={{ color: 'var(--text-primary)' }}>
            Recent Signals
          </h2>
          {recentSignals.length === 0 ? (
            <EmptyState icon={Zap} title="No signals yet" description="Signals appear here as the bot scans markets" />
          ) : (
            <div>
              {recentSignals.slice(0, 8).map((signal, idx) => (
                <div
                  key={`${signal.symbol}-${idx}`}
                  className="flex items-center gap-3 py-2.5"
                  style={{ borderBottom: '1px solid var(--border)' }}
                >
                  <span className={`badge badge-${signal.signal_type.toLowerCase()} flex-shrink-0`}>
                    {signal.signal_type}
                  </span>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center justify-between">
                      <span className="text-[13px] font-semibold" style={{ color: 'var(--text-primary)' }}>
                        {signal.symbol}
                      </span>
                      <div className="flex items-center gap-1">
                        <Zap size={9} style={{ color: '#f59e0b' }} />
                        <span className="text-[10px] font-bold" style={{ color: '#f59e0b' }}>
                          {(signal.strength * 100).toFixed(0)}%
                        </span>
                      </div>
                    </div>
                    <div className="flex items-center justify-between mt-0.5">
                      <span className="text-[11px] mono" style={{ color: 'var(--text-muted)' }}>
                        {fmtPrice(Number(signal.price))}
                      </span>
                      <span className="text-[10px] truncate max-w-[100px]" style={{ color: 'var(--text-muted)' }}>
                        {signal.strategy?.split('+')[0].trim()}
                      </span>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* ── Bottom stats ────────────────────────────────────────── */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard
          title="Total Trades"
          value={fmtNumber(portfolio?.total_trades)}
          icon={Activity}
          accentColor="#3b82f6"
        />
        <StatCard
          title="Daily P&L"
          value={`${(portfolio?.daily_pnl || 0) >= 0 ? '+' : ''}${fmtCurrency(portfolio?.daily_pnl)}`}
          icon={(portfolio?.daily_pnl || 0) >= 0 ? TrendingUp : TrendingDown}
          accentColor={(portfolio?.daily_pnl || 0) >= 0 ? '#10b981' : '#ef4444'}
        />
        <StatCard
          title="Profit Factor"
          value={`${(portfolio?.profit_factor || 0).toFixed(2)}×`}
          icon={Target}
          accentColor="#8b5cf6"
        />
        <StatCard
          title="Max Drawdown"
          value={fmtCurrency(portfolio?.max_drawdown)}
          icon={AlertTriangle}
          accentColor="#f59e0b"
        />
      </div>
    </div>
  )
}
