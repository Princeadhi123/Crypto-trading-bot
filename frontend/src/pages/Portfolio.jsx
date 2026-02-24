import React, { useState, useEffect } from 'react'
import { DollarSign, TrendingUp, TrendingDown, Target, AlertTriangle, Award } from 'lucide-react'
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  PieChart, Pie, Cell, Legend
} from 'recharts'
import { botApi } from '../api'
import {
  PageHeader, StatCard, EmptyState, InfoRow,
  fmtCurrency, ChartTooltip
} from '../components/ui'

const PIE_COLORS = ['#10b981', '#3b82f6', '#8b5cf6', '#f59e0b', '#ef4444', '#06b6d4']

function MeterBar({ label, value, max, color }) {
  const pct = Math.min((value / max) * 100, 100)
  return (
    <div>
      <div className="flex justify-between mb-1.5 text-[12px]">
        <span style={{ color: 'var(--text-muted)' }}>{label}</span>
        <span className="font-semibold mono" style={{ color }}>{value.toFixed(1)}%</span>
      </div>
      <div className="h-1.5 rounded-full overflow-hidden" style={{ background: 'rgba(255,255,255,0.06)' }}>
        <div
          className="h-full rounded-full transition-all duration-700"
          style={{ width: `${pct}%`, background: `linear-gradient(90deg, ${color}aa, ${color})` }}
        />
      </div>
    </div>
  )
}

export default function Portfolio() {
  const [portfolio, setPortfolio] = useState(null)
  const [pnlChart, setPnlChart] = useState([])
  const [strategyPerf, setStrategyPerf] = useState([])
  const [loading, setLoading] = useState(true)

  const fetchData = async () => {
    try {
      const [portfolioRes, chartRes, stratRes] = await Promise.all([
        botApi.getPortfolio(),
        botApi.getPnlChart(30),
        botApi.getStrategyPerformance(),
      ])
      setPortfolio(portfolioRes.data)
      setPnlChart(chartRes.data)
      setStrategyPerf(stratRes.data)
    } catch (e) {
      console.error(e)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchData()
    const interval = setInterval(fetchData, 30000)
    return () => clearInterval(interval)
  }, [])

  const winRate = portfolio?.win_rate || 0
  const drawdown = portfolio?.current_drawdown || 0
  const realizedPnl = portfolio?.realized_pnl || 0
  const unrealizedPnl = portfolio?.unrealized_pnl || 0
  const dailyPnl = portfolio?.daily_pnl || 0
  const weeklyPnl = portfolio?.weekly_pnl || 0
  const drawdownColor = drawdown < 5 ? '#10b981' : drawdown < 10 ? '#f59e0b' : '#ef4444'

  const pieData = strategyPerf.filter(s => s.total_trades > 0).map(s => ({ name: s.strategy, value: s.total_trades }))

  return (
    <div className="p-6 space-y-5 animate-fade-in">
      <PageHeader
        title="Portfolio"
        subtitle="Performance analytics and risk metrics"
        onRefresh={fetchData}
        loading={loading}
      />

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard title="Total Equity" value={fmtCurrency(portfolio?.total_equity)} icon={DollarSign} accentColor="#10b981" />
        <StatCard title="Realized P&L" value={`${realizedPnl >= 0 ? '+' : ''}${fmtCurrency(realizedPnl)}`} icon={realizedPnl >= 0 ? TrendingUp : TrendingDown} accentColor={realizedPnl >= 0 ? '#10b981' : '#ef4444'} />
        <StatCard title="Daily P&L" value={`${dailyPnl >= 0 ? '+' : ''}${fmtCurrency(dailyPnl)}`} icon={Target} accentColor={dailyPnl >= 0 ? '#10b981' : '#ef4444'} />
        <StatCard title="Weekly P&L" value={`${weeklyPnl >= 0 ? '+' : ''}${fmtCurrency(weeklyPnl)}`} icon={Award} accentColor={weeklyPnl >= 0 ? '#10b981' : '#ef4444'} />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
        <div className="lg:col-span-2 card">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-[13px] font-semibold" style={{ color: 'var(--text-primary)' }}>Cumulative P&L</h2>
            <span className="text-[11px]" style={{ color: 'var(--text-muted)' }}>30 days</span>
          </div>
          {pnlChart.length > 0 ? (
            <ResponsiveContainer width="100%" height={230}>
              <LineChart data={pnlChart} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="2 4" stroke="rgba(255,255,255,0.04)" />
                <XAxis dataKey="date" tick={{ fontSize: 10, fill: 'var(--text-muted)' }} tickFormatter={v => v.split(' ')[0]} axisLine={false} tickLine={false} />
                <YAxis tick={{ fontSize: 10, fill: 'var(--text-muted)' }} tickFormatter={v => `$${v}`} axisLine={false} tickLine={false} width={52} />
                <Tooltip content={<ChartTooltip formatter={v => [`$${Number(v).toFixed(2)}`, 'Cumulative P&L']} />} />
                <Line type="monotone" dataKey="cumulative_pnl" stroke="#10b981" strokeWidth={2} dot={false} activeDot={{ r: 4, fill: '#10b981', strokeWidth: 0 }} />
              </LineChart>
            </ResponsiveContainer>
          ) : (
            <EmptyState icon={TrendingUp} title="No closed trades yet" description="Close some positions to see performance history" />
          )}
        </div>

        <div className="card space-y-5">
          <h2 className="text-[13px] font-semibold" style={{ color: 'var(--text-primary)' }}>Risk Metrics</h2>
          <div className="space-y-4">
            <MeterBar label="Win Rate" value={winRate} max={100} color={winRate >= 50 ? '#10b981' : '#ef4444'} />
            <MeterBar label="Current Drawdown" value={drawdown} max={20} color={drawdownColor} />
          </div>
          <div className="divider" />
          <div className="space-y-0.5">
            <InfoRow label="Unrealized P&L" value={fmtCurrency(unrealizedPnl)} valueColor={unrealizedPnl >= 0 ? '#34d399' : '#f87171'} />
            <InfoRow label="Profit Factor" value={`${(portfolio?.profit_factor || 0).toFixed(2)}×`} valueColor="#60a5fa" />
            <InfoRow label="Total Trades" value={portfolio?.total_trades || 0} />
            <InfoRow label="Wins / Losses" value={`${portfolio?.winning_trades || 0} / ${portfolio?.losing_trades || 0}`} />
            <InfoRow label="Max Drawdown" value={fmtCurrency(portfolio?.max_drawdown)} valueColor="#f59e0b" />
          </div>
        </div>
      </div>

      {pieData.length > 0 && (
        <div className="card">
          <h2 className="text-[13px] font-semibold mb-4" style={{ color: 'var(--text-primary)' }}>Trade Distribution by Strategy</h2>
          <ResponsiveContainer width="100%" height={190}>
            <PieChart>
              <Pie data={pieData} cx="50%" cy="50%" innerRadius={50} outerRadius={75} dataKey="value" paddingAngle={4}>
                {pieData.map((entry, index) => (
                  <Cell key={entry.name} fill={PIE_COLORS[index % PIE_COLORS.length]} />
                ))}
              </Pie>
              <Legend iconType="circle" iconSize={7} formatter={(v) => <span style={{ color: 'var(--text-secondary)', fontSize: 11 }}>{v}</span>} />
              <Tooltip contentStyle={{ background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 8 }} formatter={(v) => [v, 'Trades']} />
            </PieChart>
          </ResponsiveContainer>
        </div>
      )}

      <div
        className="flex items-start gap-3 p-4 rounded-xl text-[12px]"
        style={{ background: 'rgba(239,68,68,0.04)', border: '1px solid rgba(239,68,68,0.12)' }}
      >
        <AlertTriangle size={13} style={{ color: '#f87171', flexShrink: 0, marginTop: 1 }} />
        <span style={{ color: '#94a3b8', lineHeight: 1.6 }}>
          <strong style={{ color: '#f87171' }}>Risk Disclaimer:</strong> Crypto trading involves substantial risk. Past performance does not guarantee future results. Never invest more than you can afford to lose.
        </span>
      </div>
    </div>
  )
}
