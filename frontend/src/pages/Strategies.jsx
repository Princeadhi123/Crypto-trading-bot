import React, { useState, useEffect } from 'react'
import { TrendingUp, BarChart2, Activity, Zap, ToggleLeft, ToggleRight, Cpu, Radio } from 'lucide-react'
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts'
import { botApi } from '../api'
import { PageHeader, EmptyState, SkeletonCard } from '../components/ui'

const REGIME_LABELS = {
  trending_up: { label: 'Trending Up', color: '#4ade80', bg: 'rgba(74,222,128,0.1)', border: 'rgba(74,222,128,0.25)' },
  trending_down: { label: 'Trending Down', color: '#f87171', bg: 'rgba(248,113,113,0.1)', border: 'rgba(248,113,113,0.25)' },
  ranging: { label: 'Ranging', color: '#60a5fa', bg: 'rgba(96,165,250,0.1)', border: 'rgba(96,165,250,0.25)' },
  high_volatility: { label: 'High Volatility', color: '#f59e0b', bg: 'rgba(245,158,11,0.1)', border: 'rgba(245,158,11,0.25)' },
  low_volatility: { label: 'Low Volatility', color: '#a78bfa', bg: 'rgba(167,139,250,0.1)', border: 'rgba(167,139,250,0.25)' },
}

const strategyDescriptions = {
  rsi: {
    icon: Activity,
    color: 'blue',
    description: 'RSI Mean Reversion — Buys on oversold bounces (RSI < 30), sells on overbought reversals (RSI > 70). Works best in ranging markets.',
    timeframe: '5m',
    type: 'Mean Reversion',
  },
  macd: {
    icon: TrendingUp,
    color: 'emerald',
    description: 'MACD Momentum — Detects trend direction changes via MACD/Signal crossovers. Histogram confirmation required. Best in trending markets.',
    timeframe: '5m',
    type: 'Momentum',
  },
  bollinger: {
    icon: BarChart2,
    color: 'purple',
    description: 'Bollinger Bands — Trades price bounces off the lower/upper bands with volume confirmation. Dynamic ATR-based exits.',
    timeframe: '5m',
    type: 'Volatility',
  },
  scalping: {
    icon: Zap,
    color: 'yellow',
    description: 'EMA Scalping — Fast 5/13 EMA crossovers with momentum and volume filter for quick in-and-out trades with ATR stops.',
    timeframe: '5m',
    type: 'Scalping',
  },
  pairs: {
    icon: TrendingUp,
    color: 'teal',
    description: 'Statistical Arbitrage — Pairs trading on the BTC/ETH log-price spread using rolling z-score (OLS hedge ratio). Market-neutral: generates alpha in both bull and bear markets by trading relative price divergence.',
    timeframe: '5m',
    type: 'Pairs / Market-Neutral',
  },
}

const colorStyles = {
  blue: { bg: 'rgba(59,130,246,0.1)', border: 'rgba(59,130,246,0.25)', text: '#60a5fa' },
  emerald: { bg: 'rgba(34,197,94,0.1)', border: 'rgba(34,197,94,0.25)', text: '#4ade80' },
  purple: { bg: 'rgba(168,85,247,0.1)', border: 'rgba(168,85,247,0.25)', text: '#c084fc' },
  yellow: { bg: 'rgba(234,179,8,0.1)', border: 'rgba(234,179,8,0.25)', text: '#facc15' },
  teal: { bg: 'rgba(20,184,166,0.1)', border: 'rgba(20,184,166,0.25)', text: '#2dd4bf' },
}

export default function Strategies({ wsEvents }) {
  const [strategies, setStrategies] = useState([])
  const [performance, setPerformance] = useState([])
  const [livePerf, setLivePerf] = useState([])
  const [regimeInfo, setRegimeInfo] = useState(null)
  const [loading, setLoading] = useState(true)

  const fetchData = async () => {
    try {
      const [strategiesRes, perfRes, livePerfRes, regimeRes] = await Promise.all([
        botApi.getStrategies(),
        botApi.getStrategyPerformance(),
        botApi.getLivePerformance(),
        botApi.getRegimeInfo(),
      ])
      setStrategies(strategiesRes.data)
      setPerformance(perfRes.data)
      setLivePerf(livePerfRes.data)
      setRegimeInfo(regimeRes.data)
    } catch (e) {
      console.error(e)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchData()
    const interval = setInterval(fetchData, 1000)  // 1 second for real-time updates
    return () => clearInterval(interval)
  }, [])

  // WebSocket real-time updates
  useEffect(() => {
    const latest = wsEvents?.[0]
    if (!latest) return
    if (latest.event === 'new_trade' || latest.event === 'trade_closed') {
      fetchData()
    }
  }, [wsEvents])

  const toggleStrategy = async (id) => {
    try {
      await botApi.toggleStrategy(id)
      setStrategies(prev =>
        prev.map(s => s.id === id ? { ...s, enabled: !s.enabled } : s)
      )
    } catch (e) {
      console.error(e)
    }
  }

  if (loading) {
    return (
      <div className="p-6 space-y-5">
        <div className="h-8 skeleton" style={{ width: 200 }} />
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {[1,2,3,4].map(i => <SkeletonCard key={i} rows={4} />)}
        </div>
      </div>
    )
  }

  return (
    <div className="p-6 space-y-5 animate-fade-in">
      <PageHeader
        title="Strategies"
        subtitle="Configure and monitor your active trading algorithms"
        onRefresh={fetchData}
        loading={loading}
      />

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {strategies.map((strategy) => {
          const info = strategyDescriptions[strategy.id] || {}
          const Icon = info.icon || Activity
          const colorKey = info.color || 'blue'
          const cs = colorStyles[colorKey]
          return (
            <div key={strategy.id} className="card" style={{ position: 'relative' }}>
              <div className="flex items-start justify-between mb-3">
                <div className="flex items-center gap-3">
                  <div className="p-2 rounded-lg" style={{ backgroundColor: cs.bg, border: `1px solid ${cs.border}` }}>
                    <Icon size={18} style={{ color: cs.text }} />
                  </div>
                  <div>
                    <h3 className="font-semibold text-slate-200">{strategy.name}</h3>
                    <div className="flex items-center gap-2 mt-0.5">
                      <span className="text-xs px-1.5 py-0.5 rounded" style={{ backgroundColor: cs.bg, color: cs.text }}>
                        {info.type || 'Strategy'}
                      </span>
                      <span className="text-xs text-slate-500">{info.timeframe}</span>
                    </div>
                  </div>
                </div>
                <button
                  onClick={() => toggleStrategy(strategy.id)}
                  className="flex items-center gap-2 text-sm transition-colors"
                >
                  {strategy.enabled
                    ? <ToggleRight size={28} className="text-emerald-400" />
                    : <ToggleLeft size={28} className="text-slate-600" />
                  }
                </button>
              </div>
              <p className="text-xs text-slate-400 leading-relaxed mb-3">{info.description}</p>
              <div className={`flex items-center gap-1.5 text-xs font-medium ${strategy.enabled ? 'text-emerald-400' : 'text-slate-500'}`}>
                <div className={`w-1.5 h-1.5 rounded-full ${strategy.enabled ? 'bg-emerald-400 pulse-dot' : 'bg-slate-600'}`} />
                {strategy.enabled ? 'Active — scanning for signals' : 'Disabled'}
              </div>
            </div>
          )
        })}
      </div>

      {performance.length > 0 && (
        <div className="card">
          <h2 className="text-[13px] font-semibold mb-4" style={{ color: 'var(--text-primary)' }}>Strategy Performance</h2>
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            <ResponsiveContainer width="100%" height={220}>
              <BarChart data={performance} layout="vertical">
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(100,116,139,0.15)" horizontal={false} />
                <XAxis type="number" tick={{ fontSize: 10, fill: '#64748b' }} tickFormatter={v => `$${v}`} />
                <YAxis type="category" dataKey="strategy" tick={{ fontSize: 10, fill: '#94a3b8' }} width={80} />
                <Tooltip
                  contentStyle={{ backgroundColor: '#141e35', border: '1px solid rgba(100,116,139,0.3)', borderRadius: 8 }}
                  labelStyle={{ color: '#cbd5e1' }}
                  formatter={(value) => [`$${value.toFixed(2)}`, 'Total P&L']}
                />
                <Bar dataKey="total_pnl" fill="#22c55e" radius={[0, 4, 4, 0]} />
              </BarChart>
            </ResponsiveContainer>

            <div className="space-y-3">
              {performance.map((perf) => (
                <div key={perf.strategy} className="rounded-xl p-3" style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border)' }}>
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-sm font-semibold text-slate-200">{perf.strategy}</span>
                    <span className={`text-sm font-bold ${perf.total_pnl >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                      {perf.total_pnl >= 0 ? '+' : ''}${perf.total_pnl.toFixed(2)}
                    </span>
                  </div>
                  <div className="flex gap-4 text-xs text-slate-400">
                    <span>Trades: <b className="text-slate-300">{perf.total_trades}</b></span>
                    <span>Win Rate: <b className={perf.win_rate >= 50 ? 'text-emerald-400' : 'text-red-400'}>{perf.win_rate}%</b></span>
                    <span>W/L: <b className="text-slate-300">{perf.wins}/{perf.losses}</b></span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {performance.length === 0 && (
        <div className="card">
          <EmptyState icon={BarChart2} title="No performance data yet" description="Start the bot and let it trade to see strategy analytics" />
        </div>
      )}

      {regimeInfo && (
        <div className="card space-y-4">
          <h2 className="text-[13px] font-semibold flex items-center gap-2" style={{ color: 'var(--text-primary)' }}>
            <Radio size={14} style={{ color: '#3b82f6' }} /> Live Market Regimes
          </h2>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
            {Object.entries(regimeInfo.current_regimes || {}).map(([symbol, regime]) => {
              const r = REGIME_LABELS[regime] || { label: regime, color: '#94a3b8', bg: 'rgba(148,163,184,0.1)', border: 'rgba(148,163,184,0.25)' }
              return (
                <div key={symbol} className="rounded-lg p-3 border" style={{ backgroundColor: '#1a2540', borderColor: r.border }}>
                  <div className="text-xs font-semibold text-slate-300 mb-1">{symbol.replace('/USDT', '')}</div>
                  <div className="text-xs font-bold px-2 py-0.5 rounded-full inline-block" style={{ color: r.color, backgroundColor: r.bg }}>
                    {r.label}
                  </div>
                </div>
              )
            })}
            {Object.keys(regimeInfo.current_regimes || {}).length === 0 && (
              <p className="text-xs text-slate-500 col-span-4">Regime data available after bot starts trading.</p>
            )}
          </div>
        </div>
      )}

      <div className="card space-y-4">
        <h2 className="text-[13px] font-semibold flex items-center gap-2" style={{ color: 'var(--text-primary)' }}>
          <Cpu size={14} style={{ color: '#8b5cf6' }} /> Institutional Metrics (Live)
        </h2>
        {livePerf.length > 0 ? (
          <>
            <div className="overflow-x-auto">
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead>
                  <tr>
                    <th style={{ textAlign: 'left', padding: '10px 16px', fontSize: '11px', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--text-muted)', borderBottom: '1px solid var(--border)', background: 'rgba(255,255,255,0.02)', width: '180px' }}>Strategy</th>
                    <th style={{ textAlign: 'right', padding: '10px 16px', fontSize: '11px', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--text-muted)', borderBottom: '1px solid var(--border)', background: 'rgba(255,255,255,0.02)', width: '140px' }}>Sharpe (rolling)</th>
                    <th style={{ textAlign: 'right', padding: '10px 16px', fontSize: '11px', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--text-muted)', borderBottom: '1px solid var(--border)', background: 'rgba(255,255,255,0.02)', width: '130px' }}>Kelly Fraction</th>
                    <th style={{ textAlign: 'right', padding: '10px 16px', fontSize: '11px', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--text-muted)', borderBottom: '1px solid var(--border)', background: 'rgba(255,255,255,0.02)', width: '200px' }}>Dynamic Weight</th>
                    <th style={{ textAlign: 'right', padding: '10px 16px', fontSize: '11px', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--text-muted)', borderBottom: '1px solid var(--border)', background: 'rgba(255,255,255,0.02)', width: '100px' }}>Win Rate</th>
                    <th style={{ textAlign: 'right', padding: '10px 16px', fontSize: '11px', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--text-muted)', borderBottom: '1px solid var(--border)', background: 'rgba(255,255,255,0.02)', width: '80px' }}>Trades</th>
                  </tr>
                </thead>
                <tbody>
                  {livePerf.map((p) => (
                    <tr key={p.strategy} style={{ borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
                      <td style={{ padding: '12px 16px', fontSize: '13px', width: '180px' }} className="font-medium text-slate-300">{p.strategy}</td>
                      <td style={{ padding: '12px 16px', fontSize: '13px', textAlign: 'right', width: '140px' }}>
                        <span className={`font-mono font-semibold ${p.rolling_sharpe > 0 ? 'text-emerald-400' : p.rolling_sharpe < 0 ? 'text-red-400' : 'text-slate-400'}`}>
                          {p.rolling_sharpe > 0 ? '+' : ''}{p.rolling_sharpe.toFixed(3)}
                        </span>
                      </td>
                      <td style={{ padding: '12px 16px', fontSize: '13px', textAlign: 'right', width: '130px' }}>
                        <span className="font-mono text-blue-300">{p.kelly_fraction.toFixed(2)}%</span>
                      </td>
                      <td style={{ padding: '12px 16px', fontSize: '13px', textAlign: 'right', width: '200px' }}>
                        <div className="flex items-center justify-end gap-2">
                          <div className="h-1.5 rounded-full w-16 overflow-hidden" style={{ backgroundColor: 'rgba(100,116,139,0.2)' }}>
                            <div className="h-full rounded-full" style={{ width: `${Math.min(p.dynamic_weight / 2 * 100, 100)}%`, backgroundColor: p.dynamic_weight >= 1.0 ? '#22c55e' : '#f59e0b' }} />
                          </div>
                          <span className={`font-mono text-xs ${p.dynamic_weight >= 1.0 ? 'text-emerald-400' : 'text-yellow-400'}`}>{p.dynamic_weight.toFixed(2)}x</span>
                        </div>
                      </td>
                      <td style={{ padding: '12px 16px', fontSize: '13px', textAlign: 'right', width: '100px' }}>
                        <span className={p.win_rate >= 50 ? 'text-emerald-400' : 'text-red-400'}>{p.win_rate}%</span>
                      </td>
                      <td style={{ padding: '12px 16px', fontSize: '13px', textAlign: 'right', width: '80px' }} className="text-slate-400">{p.total_trades}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="text-xs text-slate-500">Dynamic weights update after each closed trade. Kelly fraction = half-Kelly criterion based on rolling win rate and reward/risk ratio.</p>
          </>
        ) : (
          <p className="text-sm text-center py-8" style={{ color: 'var(--text-muted)' }}>
            No performance data yet. Start the bot and close some trades to populate institutional metrics.
          </p>
        )}
      </div>
    </div>
  )
}
