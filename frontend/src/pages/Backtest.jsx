import React, { useState, useEffect, useRef } from 'react'
import {
  FlaskConical, Play, Trophy, TrendingUp, TrendingDown,
  Loader2, CheckCircle2, XCircle, Zap, BarChart2, ArrowRight
} from 'lucide-react'
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, BarChart, Bar, ReferenceLine
} from 'recharts'
import { botApi } from '../api'
import { PageHeader } from '../components/ui'

const ALL_SYMBOLS = ['BTC/USDT', 'ETH/USDT', 'BNB/USDT', 'SOL/USDT', 'DOGE/USDT', 'XRP/USDT']
const ALL_STRATEGIES = [
  { id: 'rsi', label: 'RSI Mean Reversion' },
  { id: 'macd', label: 'MACD Momentum' },
  { id: 'bollinger', label: 'Bollinger Bands' },
  { id: 'scalping', label: 'EMA Scalping' },
  { id: 'pairs', label: 'Statistical Arbitrage' },
]
const MODES = [
  { id: 'compare', label: 'Compare All', desc: 'Runs each strategy solo + ensemble + adaptive, ranks by return' },
  { id: 'adaptive', label: 'Adaptive', desc: 'Regime-routed weights — mirrors the live engine' },
  { id: 'ensemble', label: 'Ensemble', desc: 'Flat-weight multi-strategy voting' },
  { id: 'solo', label: 'Solo', desc: 'Each signal trades independently' },
]

const card = { backgroundColor: 'var(--bg-surface)', border: '1px solid var(--border)' }

function MetricTile({ label, value, positive }) {
  const color = positive === undefined ? 'var(--text-primary)' : positive ? '#4ade80' : '#f87171'
  return (
    <div className="rounded-xl p-3" style={card}>
      <div className="text-[10px] uppercase tracking-wide text-slate-500">{label}</div>
      <div className="text-lg font-bold mt-0.5" style={{ color }}>{value}</div>
    </div>
  )
}

export default function Backtest() {
  const [symbols, setSymbols] = useState(['BTC/USDT', 'ETH/USDT'])
  const [strategies, setStrategies] = useState(['rsi', 'macd', 'bollinger', 'scalping'])
  const [mode, setMode] = useState('compare')
  const [days, setDays] = useState(30)
  const [balance, setBalance] = useState(10000)

  const [job, setJob] = useState(null)          // active/last job detail
  const [jobs, setJobs] = useState([])          // history
  const [running, setRunning] = useState(false)
  const [error, setError] = useState(null)
  const [applied, setApplied] = useState(false)
  const [selectedRun, setSelectedRun] = useState(null) // drill into compare run
  const pollRef = useRef(null)

  const fetchJobs = async () => {
    try { setJobs((await botApi.getBacktestJobs()).data) } catch { /* noop */ }
  }
  useEffect(() => { fetchJobs() }, [])
  useEffect(() => () => clearInterval(pollRef.current), [])

  const pollJob = (jobId) => {
    clearInterval(pollRef.current)
    pollRef.current = setInterval(async () => {
      try {
        const { data } = await botApi.getBacktestJob(jobId)
        setJob(data)
        if (data.status === 'completed' || data.status === 'failed') {
          clearInterval(pollRef.current)
          setRunning(false)
          fetchJobs()
        }
      } catch {
        clearInterval(pollRef.current)
        setRunning(false)
      }
    }, 1500)
  }

  const startBacktest = async () => {
    setError(null); setApplied(false); setSelectedRun(null); setJob(null)
    if (!symbols.length || !strategies.length) { setError('Select at least one symbol and strategy'); return }
    setRunning(true)
    try {
      const { data } = await botApi.runBacktest({
        symbols, strategies, mode, days,
        initial_balance: balance,
      })
      pollJob(data.job_id)
    } catch (e) {
      setError(e?.response?.data?.detail || 'Failed to start backtest')
      setRunning(false)
    }
  }

  const applyWinner = async () => {
    try {
      await botApi.applyBacktestWinner(job.id)
      setApplied(true)
    } catch (e) {
      setError(e?.response?.data?.detail || 'Failed to apply')
    }
  }

  const loadJob = async (jobId) => {
    setSelectedRun(null); setApplied(false)
    try { setJob((await botApi.getBacktestJob(jobId)).data) } catch { /* noop */ }
  }

  const toggle = (list, setList, item) =>
    setList(list.includes(item) ? list.filter(x => x !== item) : [...list, item])

  const result = job?.result
  const isCompare = result?.mode === 'compare'
  const activeReport = isCompare
    ? (selectedRun ? result.runs[selectedRun] : null)
    : result

  return (
    <div className="p-6 space-y-5">
      <PageHeader
        icon={FlaskConical}
        title="Backtesting Lab"
        subtitle="Replay strategies over real Binance history — rank, validate, then deploy to paper trading"
      />

      {/* ── Config panel ─────────────────────────────────────── */}
      <div className="rounded-xl p-4 space-y-4" style={card}>
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          <div>
            <div className="text-[11px] uppercase tracking-wide text-slate-500 mb-2">Symbols</div>
            <div className="flex flex-wrap gap-2">
              {ALL_SYMBOLS.map(s => (
                <button key={s} onClick={() => toggle(symbols, setSymbols, s)}
                  className="px-2.5 py-1 rounded-lg text-[12px] font-medium transition-all"
                  style={symbols.includes(s)
                    ? { backgroundColor: 'rgba(59,130,246,0.15)', border: '1px solid rgba(59,130,246,0.4)', color: '#60a5fa' }
                    : { backgroundColor: 'transparent', border: '1px solid var(--border)', color: 'var(--text-muted)' }}>
                  {s.replace('/USDT', '')}
                </button>
              ))}
            </div>
          </div>
          <div>
            <div className="text-[11px] uppercase tracking-wide text-slate-500 mb-2">Strategies</div>
            <div className="flex flex-wrap gap-2">
              {ALL_STRATEGIES.map(s => (
                <button key={s.id} onClick={() => toggle(strategies, setStrategies, s.id)}
                  className="px-2.5 py-1 rounded-lg text-[12px] font-medium transition-all"
                  style={strategies.includes(s.id)
                    ? { backgroundColor: 'rgba(34,197,94,0.15)', border: '1px solid rgba(34,197,94,0.4)', color: '#4ade80' }
                    : { backgroundColor: 'transparent', border: '1px solid var(--border)', color: 'var(--text-muted)' }}>
                  {s.label}
                </button>
              ))}
            </div>
          </div>
        </div>

        <div>
          <div className="text-[11px] uppercase tracking-wide text-slate-500 mb-2">Mode</div>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-2">
            {MODES.map(m => (
              <button key={m.id} onClick={() => setMode(m.id)}
                className="p-2.5 rounded-lg text-left transition-all"
                style={mode === m.id
                  ? { backgroundColor: 'rgba(168,85,247,0.12)', border: '1px solid rgba(168,85,247,0.4)' }
                  : { backgroundColor: 'transparent', border: '1px solid var(--border)' }}>
                <div className="text-[12px] font-semibold" style={{ color: mode === m.id ? '#c084fc' : 'var(--text-primary)' }}>{m.label}</div>
                <div className="text-[10px] mt-0.5 text-slate-500">{m.desc}</div>
              </button>
            ))}
          </div>
        </div>

        <div className="flex flex-wrap items-end gap-4">
          {[
            { label: 'Days of history', value: days, set: setDays, min: 3, max: 365 },
            { label: 'Initial balance $', value: balance, set: setBalance, min: 100, max: 1000000 },
          ].map(f => (
            <div key={f.label}>
              <div className="text-[11px] uppercase tracking-wide text-slate-500 mb-1">{f.label}</div>
              <input type="number" value={f.value} min={f.min} max={f.max} step={f.step || 1}
                onChange={e => f.set(Number(e.target.value))}
                className="w-28 px-2 py-1.5 rounded-lg text-[13px] outline-none"
                style={{ backgroundColor: 'var(--bg-base)', border: '1px solid var(--border)', color: 'var(--text-primary)' }} />
            </div>
          ))}
          <button onClick={startBacktest} disabled={running}
            className="flex items-center gap-2 px-4 py-2 rounded-lg text-[13px] font-semibold transition-all disabled:opacity-50"
            style={{ background: 'linear-gradient(135deg,#10b981,#3b82f6)', color: 'white' }}>
            {running ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />}
            {running ? 'Running…' : 'Run Backtest'}
          </button>
        </div>

        <div className="text-[11px] text-slate-500">
          Position sizing, correlation limits, and max drawdown are pulled live from your{' '}
          <span style={{ color: 'var(--text-primary)' }}>Settings</span> page risk configuration —
          same risk engine used by paper/live trading.
        </div>

        {error && (
          <div className="flex items-center gap-2 text-[12px] text-red-400">
            <XCircle size={14} /> {error}
          </div>
        )}
        {running && job && (
          <div className="space-y-1">
            <div className="text-[11px] text-slate-500 capitalize">{job.status?.replace('_', ' ')}…</div>
            <div className="h-1.5 rounded-full overflow-hidden" style={{ backgroundColor: 'var(--bg-base)' }}>
              <div className="h-full rounded-full transition-all"
                style={{ width: `${(job.progress || 0) * 100}%`, background: 'linear-gradient(90deg,#10b981,#3b82f6)' }} />
            </div>
          </div>
        )}
      </div>

      {/* ── Compare leaderboard ──────────────────────────────── */}
      {isCompare && (
        <div className="rounded-xl p-4" style={card}>
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2">
              <Trophy size={15} color="#facc15" />
              <span className="text-[13px] font-semibold" style={{ color: 'var(--text-primary)' }}>Strategy Leaderboard</span>
            </div>
            <button onClick={applyWinner} disabled={applied}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[12px] font-semibold disabled:opacity-60"
              style={{ backgroundColor: 'rgba(34,197,94,0.15)', border: '1px solid rgba(34,197,94,0.4)', color: '#4ade80' }}>
              {applied ? <CheckCircle2 size={13} /> : <Zap size={13} />}
              {applied ? 'Applied to Paper Trading' : `Deploy Winner (${result.best})`}
            </button>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-[12px]">
              <thead>
                <tr className="text-slate-500 text-left">
                  {['#', 'Configuration', 'Return %', 'Win Rate', 'Trades', 'Sharpe', 'Max DD %', 'Profit Factor', ''].map(h => (
                    <th key={h} className="py-2 pr-4 font-medium">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {result.comparison.map((row, i) => (
                  <tr key={row.name} className="border-t" style={{ borderColor: 'rgba(100,116,139,0.1)' }}>
                    <td className="py-2 pr-4">{i === 0 ? '🏆' : i + 1}</td>
                    <td className="py-2 pr-4 font-semibold capitalize" style={{ color: 'var(--text-primary)' }}>{row.name}</td>
                    <td className="py-2 pr-4 font-bold" style={{ color: row.total_return_pct >= 0 ? '#4ade80' : '#f87171' }}>
                      {row.total_return_pct >= 0 ? '+' : ''}{row.total_return_pct}%
                    </td>
                    <td className="py-2 pr-4">{row.win_rate}%</td>
                    <td className="py-2 pr-4">{row.total_trades}</td>
                    <td className="py-2 pr-4">{row.sharpe_ratio}</td>
                    <td className="py-2 pr-4 text-red-400">{row.max_drawdown_pct}%</td>
                    <td className="py-2 pr-4">{row.profit_factor ?? '∞'}</td>
                    <td className="py-2">
                      <button onClick={() => setSelectedRun(row.name)}
                        className="flex items-center gap-1 text-[11px]" style={{ color: '#60a5fa' }}>
                        Detail <ArrowRight size={11} />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* ── Single run report ────────────────────────────────── */}
      {activeReport && (
        <>
          {isCompare && (
            <div className="text-[12px] text-slate-500 capitalize">
              Detail view: <span className="font-semibold" style={{ color: 'var(--text-primary)' }}>{selectedRun}</span>
            </div>
          )}
          <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-3">
            <MetricTile label="Final Equity" value={`$${activeReport.metrics.final_equity.toLocaleString()}`} />
            <MetricTile label="Total Return" value={`${activeReport.metrics.total_return_pct >= 0 ? '+' : ''}${activeReport.metrics.total_return_pct}%`} positive={activeReport.metrics.total_return_pct >= 0} />
            <MetricTile label="Win Rate" value={`${activeReport.metrics.win_rate}%`} positive={activeReport.metrics.win_rate >= 50} />
            <MetricTile label="Sharpe" value={activeReport.metrics.sharpe_ratio} positive={activeReport.metrics.sharpe_ratio > 1} />
            <MetricTile label="Max Drawdown" value={`${activeReport.metrics.max_drawdown_pct}%`} positive={false} />
            <MetricTile label="Trades / Fees" value={`${activeReport.metrics.total_trades} / $${activeReport.metrics.total_fees}`} />
          </div>

          {/* Equity curve */}
          <div className="rounded-xl p-4" style={card}>
            <div className="flex items-center gap-2 mb-3">
              <TrendingUp size={15} color="#4ade80" />
              <span className="text-[13px] font-semibold" style={{ color: 'var(--text-primary)' }}>Equity Curve</span>
            </div>
            <ResponsiveContainer width="100%" height={260}>
              <LineChart data={activeReport.equity_curve}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(100,116,139,0.1)" />
                <XAxis dataKey="timestamp" tick={{ fontSize: 10, fill: '#64748b' }} minTickGap={60} />
                <YAxis domain={['auto', 'auto']} tick={{ fontSize: 10, fill: '#64748b' }} width={70} />
                <Tooltip contentStyle={{ backgroundColor: '#0f172a', border: '1px solid rgba(100,116,139,0.3)', borderRadius: 8, fontSize: 12 }} />
                <ReferenceLine y={activeReport.config.initial_balance} stroke="#64748b" strokeDasharray="4 4" />
                <Line type="monotone" dataKey="equity" stroke="#4ade80" strokeWidth={1.5} dot={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            {/* Per-strategy PnL */}
            <div className="rounded-xl p-4" style={card}>
              <div className="flex items-center gap-2 mb-3">
                <BarChart2 size={15} color="#c084fc" />
                <span className="text-[13px] font-semibold" style={{ color: 'var(--text-primary)' }}>PnL by Strategy</span>
              </div>
              <ResponsiveContainer width="100%" height={200}>
                <BarChart data={activeReport.strategy_breakdown}>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(100,116,139,0.1)" />
                  <XAxis dataKey="strategy" tick={{ fontSize: 10, fill: '#64748b' }} />
                  <YAxis tick={{ fontSize: 10, fill: '#64748b' }} />
                  <Tooltip contentStyle={{ backgroundColor: '#0f172a', border: '1px solid rgba(100,116,139,0.3)', borderRadius: 8, fontSize: 12 }} />
                  <Bar dataKey="total_pnl" radius={[4, 4, 0, 0]}>
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
              <div className="mt-2 space-y-1">
                {activeReport.strategy_breakdown.map(s => (
                  <div key={s.strategy} className="flex justify-between text-[11px]">
                    <span className="text-slate-400">{s.strategy}</span>
                    <span>
                      <span className="text-slate-500 mr-3">{s.trades} trades · {s.win_rate}% WR</span>
                      <span style={{ color: s.total_pnl >= 0 ? '#4ade80' : '#f87171' }}>${s.total_pnl}</span>
                    </span>
                  </div>
                ))}
              </div>
            </div>

            {/* Regime breakdown */}
            <div className="rounded-xl p-4" style={card}>
              <div className="flex items-center gap-2 mb-3">
                <TrendingDown size={15} color="#f59e0b" />
                <span className="text-[13px] font-semibold" style={{ color: 'var(--text-primary)' }}>Performance by Market Regime</span>
              </div>
              <div className="space-y-2">
                {activeReport.regime_breakdown.map(r => (
                  <div key={r.regime} className="flex items-center justify-between p-2 rounded-lg"
                    style={{ backgroundColor: 'var(--bg-base)' }}>
                    <span className="text-[12px] capitalize text-slate-300">{r.regime.replace('_', ' ')}</span>
                    <div className="flex gap-4 text-[11px]">
                      <span className="text-slate-500">{r.trades} trades</span>
                      <span className="text-slate-500">{r.win_rate}% WR</span>
                      <span className="font-semibold" style={{ color: r.total_pnl >= 0 ? '#4ade80' : '#f87171' }}>
                        ${r.total_pnl}
                      </span>
                    </div>
                  </div>
                ))}
                {!activeReport.regime_breakdown.length && (
                  <div className="text-[12px] text-slate-500">No trades generated</div>
                )}
              </div>
            </div>
          </div>

          {/* Trade log */}
          <div className="rounded-xl p-4" style={card}>
            <div className="text-[13px] font-semibold mb-3" style={{ color: 'var(--text-primary)' }}>
              Trade Log <span className="text-slate-500 font-normal">(last {activeReport.trades.length})</span>
            </div>
            <div className="overflow-x-auto max-h-80 overflow-y-auto">
              <table className="w-full text-[11px]">
                <thead className="sticky top-0" style={{ backgroundColor: 'var(--bg-surface)' }}>
                  <tr className="text-slate-500 text-left">
                    {['Symbol', 'Side', 'Strategy', 'Regime', 'Entry', 'Exit', 'PnL', 'Reason', 'Opened'].map(h => (
                      <th key={h} className="py-1.5 pr-3 font-medium">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {activeReport.trades.slice().reverse().map((t, i) => (
                    <tr key={i} className="border-t" style={{ borderColor: 'rgba(100,116,139,0.08)' }}>
                      <td className="py-1.5 pr-3">{t.symbol}</td>
                      <td className="py-1.5 pr-3" style={{ color: t.side === 'BUY' ? '#4ade80' : '#f87171' }}>{t.side}</td>
                      <td className="py-1.5 pr-3 text-slate-400">{t.strategy}</td>
                      <td className="py-1.5 pr-3 text-slate-500 capitalize">{t.regime?.replace('_', ' ')}</td>
                      <td className="py-1.5 pr-3">{t.entry_price}</td>
                      <td className="py-1.5 pr-3">{t.exit_price}</td>
                      <td className="py-1.5 pr-3 font-semibold" style={{ color: t.pnl >= 0 ? '#4ade80' : '#f87171' }}>
                        {t.pnl >= 0 ? '+' : ''}{t.pnl}
                      </td>
                      <td className="py-1.5 pr-3 text-slate-500">{t.exit_reason}</td>
                      <td className="py-1.5 text-slate-500">{t.opened_at}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}

      {/* ── Job history ──────────────────────────────────────── */}
      {jobs.length > 0 && (
        <div className="rounded-xl p-4" style={card}>
          <div className="text-[13px] font-semibold mb-3" style={{ color: 'var(--text-primary)' }}>Previous Runs</div>
          <div className="space-y-1.5">
            {jobs.map(j => (
              <button key={j.id} onClick={() => loadJob(j.id)}
                className="w-full flex items-center justify-between p-2 rounded-lg text-left transition-all hover:opacity-80"
                style={{ backgroundColor: 'var(--bg-base)' }}>
                <div className="flex items-center gap-2 text-[12px]">
                  {j.status === 'completed' ? <CheckCircle2 size={13} color="#4ade80" />
                    : j.status === 'failed' ? <XCircle size={13} color="#f87171" />
                    : <Loader2 size={13} className="animate-spin" color="#60a5fa" />}
                  <span className="capitalize font-medium" style={{ color: 'var(--text-primary)' }}>{j.config?.mode}</span>
                  <span className="text-slate-500">{(j.config?.symbols || []).join(', ')}</span>
                  <span className="text-slate-600">{j.config?.days}d · {j.config?.timeframe}</span>
                </div>
                <span className="text-[10px] text-slate-600">{new Date(j.started_at).toLocaleString()}</span>
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
