import React, { useState, useEffect } from 'react'
import { Settings, Save, AlertTriangle, Plus, X, Key, Zap } from 'lucide-react'
import { botApi } from '../api'
import { PageHeader, fmtCurrency } from '../components/ui'

const AVAILABLE_SYMBOLS = [
  'BTC/USDT', 'ETH/USDT', 'BNB/USDT', 'SOL/USDT',
  'ADA/USDT', 'DOT/USDT', 'MATIC/USDT', 'AVAX/USDT',
  'LINK/USDT', 'LTC/USDT', 'XRP/USDT', 'DOGE/USDT',
]

const AVAILABLE_STRATEGIES = [
  { id: 'rsi', label: 'RSI Mean Reversion' },
  { id: 'macd', label: 'MACD Momentum' },
  { id: 'bollinger', label: 'Bollinger Bands' },
  { id: 'scalping', label: 'EMA Scalping' },
  { id: 'pairs', label: 'Statistical Arbitrage' },
]

function SliderField({ label, value, min, max, step = 0.1, unit = '%', onChange, description }) {
  return (
    <div>
      <div className="flex justify-between items-center mb-1.5">
        <label className="text-sm text-slate-300 font-medium">{label}</label>
        <span className="text-sm font-bold text-emerald-400">{value}{unit}</span>
      </div>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={e => onChange(Number(e.target.value))}
        className="w-full h-1.5 rounded-full appearance-none cursor-pointer"
        style={{ accentColor: '#22c55e' }}
      />
      <div className="flex justify-between text-xs text-slate-600 mt-1">
        <span>{min}{unit}</span>
        <span>{description}</span>
        <span>{max}{unit}</span>
      </div>
    </div>
  )
}

export default function SettingsPage() {
  const [settings, setSettings] = useState(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [hotReloadResult, setHotReloadResult] = useState(null)
  const [newSymbol, setNewSymbol] = useState('')

  useEffect(() => {
    botApi.getSettings()
      .then(res => setSettings(res.data))
      .catch(console.error)
      .finally(() => setLoading(false))
  }, [])

  const handleSave = async () => {
    if (!settings) return
    setSaving(true)
    try {
      const res = await botApi.updateSettings(settings)
      setSaved(true)
      // Show hot-reload feedback if bot is running
      if (res.data?.needs_restart?.length > 0 || res.data?.applied?.length > 0) {
        setHotReloadResult(res.data)
        setTimeout(() => setHotReloadResult(null), 8000)
      }
      setTimeout(() => setSaved(false), 2500)
    } catch (e) {
      console.error(e)
    } finally {
      setSaving(false)
    }
  }

  const toggleStrategy = (id) => {
    setSettings(prev => {
      const current = prev.active_strategies || []
      const updated = current.includes(id)
        ? current.filter(s => s !== id)
        : [...current, id]
      return { ...prev, active_strategies: updated }
    })
  }

  const addSymbol = () => {
    const sym = newSymbol.toUpperCase().trim()
    if (!sym || settings.active_symbols?.includes(sym)) return
    setSettings(prev => ({ ...prev, active_symbols: [...(prev.active_symbols || []), sym] }))
    setNewSymbol('')
  }

  const removeSymbol = (sym) => {
    setSettings(prev => ({ ...prev, active_symbols: prev.active_symbols.filter(s => s !== sym) }))
  }

  if (loading || !settings) {
    return (
      <div className="p-6 space-y-5">
        <div className="skeleton" style={{ height: 32, width: 180 }} />
        {[1, 2, 3].map(i => (
          <div key={i} className="card space-y-3">
            <div className="skeleton" style={{ height: 14, width: 120 }} />
            <div className="skeleton" style={{ height: 60, width: '100%' }} />
          </div>
        ))}
      </div>
    )
  }

  return (
    <div className="p-6 space-y-5 max-w-3xl animate-fade-in">
      <PageHeader
        title="Settings"
        subtitle="Configure your trading bot parameters and risk rules"
        actions={
          <button
            onClick={handleSave}
            disabled={saving}
            className="btn-primary"
          >
            <Save size={12} />
            {saved ? 'Saved!' : 'Save Settings'}
          </button>
        }
      />

      <div className="card space-y-4">
        <h2 className="text-[13px] font-semibold flex items-center gap-2" style={{ color: 'var(--text-primary)' }}>
          <Settings size={14} style={{ color: '#10b981' }} /> Trading Mode
        </h2>
        <div className="flex items-start gap-4">
          <button
            onClick={() => setSettings(p => ({ ...p, paper_trading_enabled: true }))}
            className={`flex-1 rounded-lg p-4 border transition-all ${
              settings.paper_trading_enabled
                ? 'border-emerald-500/40 bg-emerald-500/10'
                : 'border-slate-600/30 hover:border-slate-500/50'
            }`}
          >
            <div className="text-sm font-semibold text-slate-200 mb-1">Paper Trading</div>
            <p className="text-xs text-slate-400">Simulated trades with virtual balance. No real money at risk. Recommended for testing.</p>
            {settings.paper_trading_enabled && <div className="mt-2 text-xs text-emerald-400 font-medium">✓ Active</div>}
          </button>
          <button
            onClick={() => setSettings(p => ({ ...p, paper_trading_enabled: false }))}
            className={`flex-1 rounded-lg p-4 border transition-all ${
              !settings.paper_trading_enabled
                ? 'border-red-500/40 bg-red-500/10'
                : 'border-slate-600/30 hover:border-slate-500/50'
            }`}
          >
            <div className="text-sm font-semibold text-slate-200 mb-1">Live Trading</div>
            <p className="text-xs text-slate-400">Real trades on the exchange. Requires API keys. Real money at risk.</p>
            {!settings.paper_trading_enabled && (
              <div className="mt-2 flex items-center gap-1 text-xs text-red-400 font-medium">
                <AlertTriangle size={11} /> Active — Real money
              </div>
            )}
          </button>
        </div>

        {settings.paper_trading_enabled && (
          <div>
            <label className="text-sm text-slate-300 font-medium">Paper Balance (USD)</label>
            <div className="mt-1.5">
              <input
                type="number"
                value={settings.paper_balance}
                onChange={e => setSettings(p => ({ ...p, paper_balance: Number(Number(e.target.value).toFixed(4)) }))}
                min={100}
                max={1000000}
                step={0.01}
                className="w-48 px-3 py-2 rounded-lg text-sm text-slate-200 focus:outline-none focus:ring-2 focus:ring-emerald-500/50"
                style={{ backgroundColor: '#1a2540', border: '1px solid rgba(100,116,139,0.35)' }}
              />
            </div>
          </div>
        )}
      </div>

      <div className="card space-y-4">
        <h2 className="text-[13px] font-semibold flex items-center gap-2" style={{ color: 'var(--text-primary)' }}>
          <Zap size={14} style={{ color: '#f59e0b' }} /> Execution Speed Mode
        </h2>
        <div className="flex items-start gap-4">
          <button
            onClick={() => setSettings(p => ({ ...p, hft_mode: false }))}
            className={`flex-1 rounded-lg p-4 border transition-all text-left ${
              !settings.hft_mode
                ? 'border-blue-500/40 bg-blue-500/10'
                : 'border-slate-600/30 hover:border-slate-500/50'
            }`}
          >
            <div className="text-sm font-semibold text-slate-200 mb-1">Standard Mode</div>
            <div className="text-xs text-slate-400 space-y-1">
              <div>• 5-minute candles</div>
              <div>• 30-second scan loop</div>
              <div>• 2 strategies must agree</div>
              <div>• 1.5% trailing stop activation</div>
            </div>
            {!settings.hft_mode && <div className="mt-2 text-xs text-blue-400 font-medium">✓ Active</div>}
          </button>
          <button
            onClick={() => setSettings(p => ({ ...p, hft_mode: true }))}
            className={`flex-1 rounded-lg p-4 border transition-all text-left ${
              settings.hft_mode
                ? 'border-yellow-500/40 bg-yellow-500/10'
                : 'border-slate-600/30 hover:border-slate-500/50'
            }`}
          >
            <div className="flex items-center gap-2 mb-1">
              <div className="text-sm font-semibold text-slate-200">HFT / Scalping Mode</div>
              <Zap size={12} className="text-yellow-400" />
            </div>
            <div className="text-xs text-slate-400 space-y-1">
              <div>• 1-minute candles (5× more signals)</div>
              <div>• 5-second scan loop (6× faster)</div>
              <div>• 1 strategy sufficient to trade</div>
              <div>• EMA 3/8, 0.5% stop, 1% target</div>
              <div>• Exit check every 2 seconds</div>
            </div>
            {settings.hft_mode && <div className="mt-2 text-xs text-yellow-400 font-medium">⚡ Active — High Frequency</div>}
          </button>
        </div>
        {settings.hft_mode && (
          <div className="flex items-start gap-2 p-3 rounded-lg text-xs" style={{ backgroundColor: 'rgba(234,179,8,0.06)', border: '1px solid rgba(234,179,8,0.2)' }}>
            <AlertTriangle size={13} className="text-yellow-400 flex-shrink-0 mt-0.5" />
            <span className="text-yellow-300">HFT mode executes many more trades at tighter margins. Test thoroughly in paper mode. Higher trading frequency = higher exchange fee accumulation.</span>
          </div>
        )}
      </div>

      <div className="card space-y-5">
        <h2 className="text-[13px] font-semibold flex items-center gap-2" style={{ color: 'var(--text-primary)' }}>
          <AlertTriangle size={14} style={{ color: '#f59e0b' }} /> Risk Management
        </h2>
        <SliderField
          label="Max Portfolio Risk Per Trade"
          value={settings.max_portfolio_risk_percent}
          min={0.1} max={10} step={0.1} unit="%"
          onChange={v => setSettings(p => ({ ...p, max_portfolio_risk_percent: v }))}
          description="of portfolio per trade"
        />
        <SliderField
          label="Max Drawdown Circuit Breaker"
          value={settings.max_drawdown_percent}
          min={1} max={50} step={0.5} unit="%"
          onChange={v => setSettings(p => ({ ...p, max_drawdown_percent: v }))}
          description="bot stops trading"
        />
        <SliderField
          label="Default Stop Loss"
          value={settings.default_stop_loss_percent}
          min={0.5} max={20} step={0.5} unit="%"
          onChange={v => setSettings(p => ({ ...p, default_stop_loss_percent: v }))}
          description="below entry price"
        />
        <SliderField
          label="Default Take Profit"
          value={settings.default_take_profit_percent}
          min={1} max={50} step={0.5} unit="%"
          onChange={v => setSettings(p => ({ ...p, default_take_profit_percent: v }))}
          description="above entry price"
        />
        <div>
          <div className="flex justify-between items-center mb-1.5">
            <label className="text-sm text-slate-300 font-medium">Max Concurrent Positions</label>
            <span className="text-sm font-bold text-emerald-400">{settings.max_concurrent_positions}</span>
          </div>
          <input
            type="range" min={1} max={20} step={1}
            value={settings.max_concurrent_positions}
            onChange={e => setSettings(p => ({ ...p, max_concurrent_positions: Number(e.target.value) }))}
            className="w-full h-1.5 rounded-full appearance-none cursor-pointer"
            style={{ accentColor: '#22c55e' }}
          />
        </div>
      </div>

      <div className="card space-y-4">
        <h2 className="text-[13px] font-semibold" style={{ color: 'var(--text-primary)' }}>Active Strategies</h2>
        <div className="grid grid-cols-2 gap-3">
          {AVAILABLE_STRATEGIES.map(({ id, label }) => {
            const isActive = settings.active_strategies?.includes(id)
            return (
              <button
                key={id}
                onClick={() => toggleStrategy(id)}
                className={`flex items-center gap-2 px-3 py-2.5 rounded-lg border text-sm font-medium transition-all text-left ${
                  isActive
                    ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-300'
                    : 'border-slate-600/30 text-slate-400 hover:border-slate-500/50'
                }`}
              >
                <div className={`w-2 h-2 rounded-full flex-shrink-0 ${isActive ? 'bg-emerald-400' : 'bg-slate-600'}`} />
                {label}
              </button>
            )
          })}
        </div>
      </div>

      <div className="card space-y-4">
        <h2 className="text-[13px] font-semibold" style={{ color: 'var(--text-primary)' }}>Trading Symbols</h2>
        <div className="flex flex-wrap gap-2">
          {(settings.active_symbols || []).map(sym => (
            <div key={sym} className="flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-medium" style={{ backgroundColor: 'rgba(34,197,94,0.1)', border: '1px solid rgba(34,197,94,0.25)', color: '#4ade80' }}>
              {sym}
              <button onClick={() => removeSymbol(sym)} className="hover:text-red-400 transition-colors">
                <X size={11} />
              </button>
            </div>
          ))}
        </div>
        <div className="flex gap-2">
          <select
            value={newSymbol}
            onChange={e => setNewSymbol(e.target.value)}
            className="px-3 py-2 rounded-lg text-sm text-slate-200 focus:outline-none focus:ring-2 focus:ring-emerald-500/50"
            style={{ backgroundColor: '#1a2540', border: '1px solid rgba(100,116,139,0.35)' }}
          >
            <option value="">Add symbol…</option>
            {AVAILABLE_SYMBOLS.filter(s => !settings.active_symbols?.includes(s)).map(s => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
          <button
            onClick={addSymbol}
            disabled={!newSymbol}
            className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-sm font-medium bg-emerald-600 hover:bg-emerald-500 text-white disabled:opacity-40 transition-colors"
          >
            <Plus size={14} /> Add
          </button>
        </div>
      </div>

      <div className="card space-y-4">
        <h2 className="text-[13px] font-semibold flex items-center gap-2" style={{ color: 'var(--text-primary)' }}>
          <Key size={14} style={{ color: 'var(--text-muted)' }} /> Exchange API Keys
        </h2>
        <div className="rounded-lg p-3 text-xs text-slate-400 leading-relaxed" style={{ backgroundColor: 'rgba(59,130,246,0.06)', border: '1px solid rgba(59,130,246,0.15)' }}>
          API keys are configured via the <code className="text-blue-400 bg-blue-500/10 px-1 rounded">.env</code> file in the <code className="text-blue-400 bg-blue-500/10 px-1 rounded">backend/</code> directory.
          Copy <code className="text-blue-400 bg-blue-500/10 px-1 rounded">.env.example</code> to <code className="text-blue-400 bg-blue-500/10 px-1 rounded">.env</code> and fill in your exchange API credentials.
          Only enable live trading after thoroughly testing in paper mode.
        </div>
        <div className="text-xs text-slate-500 space-y-1">
          <div>• Supported exchanges: Binance, Coinbase, Kraken, OKX, Bybit, and 100+ via CCXT</div>
          <div>• Set <code className="text-slate-400">EXCHANGE_NAME</code>, <code className="text-slate-400">API_KEY</code>, <code className="text-slate-400">API_SECRET</code> in .env</div>
          <div>• Restart the backend after updating .env</div>
        </div>
      </div>

      {hotReloadResult && hotReloadResult.needs_restart?.length > 0 && (
        <div className="rounded-xl border p-4 flex items-start gap-3" style={{ backgroundColor: 'rgba(245,158,11,0.06)', borderColor: 'rgba(245,158,11,0.25)' }}>
          <AlertTriangle size={16} className="text-yellow-400 flex-shrink-0 mt-0.5" />
          <div className="text-xs text-slate-300 leading-relaxed">
            <strong className="text-yellow-400">Restart required.</strong> The following changes will take effect after you stop and restart the bot:{' '}
            <span className="text-yellow-300 font-medium">{hotReloadResult.needs_restart.join(', ')}</span>.
            {hotReloadResult.applied?.length > 0 && (
              <span className="text-emerald-400"> Applied live: {hotReloadResult.applied.join(', ')}.</span>
            )}
          </div>
        </div>
      )}

      {hotReloadResult && hotReloadResult.applied?.length > 0 && !hotReloadResult.needs_restart?.length && (
        <div className="rounded-xl border p-4 flex items-start gap-3" style={{ backgroundColor: 'rgba(16,185,129,0.06)', borderColor: 'rgba(16,185,129,0.25)' }}>
          <Zap size={16} className="text-emerald-400 flex-shrink-0 mt-0.5" />
          <div className="text-xs text-slate-300 leading-relaxed">
            <strong className="text-emerald-400">Hot-reloaded!</strong> Updated live:{' '}
            <span className="text-emerald-300 font-medium">{hotReloadResult.applied.join(', ')}</span>. No restart needed.
          </div>
        </div>
      )}

      {!settings.paper_trading_enabled && (
        <div className="rounded-xl border p-4 flex items-start gap-3" style={{ backgroundColor: 'rgba(239,68,68,0.05)', borderColor: 'rgba(239,68,68,0.2)' }}>
          <AlertTriangle size={16} className="text-red-400 flex-shrink-0 mt-0.5" />
          <div className="text-xs text-slate-400 leading-relaxed">
            <strong className="text-red-400">Live Trading Enabled.</strong> Real funds will be used for all trades.
            Ensure you have tested your strategies thoroughly in paper mode, set appropriate risk limits,
            and understand that no automated system can guarantee profits. Losses can exceed expectations.
          </div>
        </div>
      )}
    </div>
  )
}
