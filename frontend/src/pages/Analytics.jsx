import React, { useState, useEffect } from 'react'
import { ShieldAlert, Brain, Zap, TrendingUp, AlertTriangle } from 'lucide-react'
import { botApi } from '../api'
import { PageHeader, InfoRow } from '../components/ui'

const SENTIMENT_COLORS = {
  'Extreme Fear': { color: '#f87171', bg: 'rgba(248,113,113,0.1)', bar: '#f87171' },
  'Fear': { color: '#fb923c', bg: 'rgba(251,146,60,0.1)', bar: '#fb923c' },
  'Neutral': { color: '#94a3b8', bg: 'rgba(148,163,184,0.1)', bar: '#94a3b8' },
  'Neutral (not fetched yet)': { color: '#94a3b8', bg: 'rgba(148,163,184,0.1)', bar: '#94a3b8' },
  'Neutral (API unavailable)': { color: '#94a3b8', bg: 'rgba(148,163,184,0.1)', bar: '#94a3b8' },
  'Greed': { color: '#4ade80', bg: 'rgba(74,222,128,0.1)', bar: '#4ade80' },
  'Extreme Greed': { color: '#22c55e', bg: 'rgba(34,197,94,0.1)', bar: '#22c55e' },
}

export default function Analytics() {
  const [varReport, setVarReport] = useState(null)
  const [sentiment, setSentiment] = useState(null)
  const [fundingRates, setFundingRates] = useState([])
  const [loading, setLoading] = useState(true)

  const fetchAll = async () => {
    try {
      const [varRes, sentRes, fundRes] = await Promise.all([
        botApi.getVarReport(),
        botApi.getSentiment(),
        botApi.getFundingRates(),
      ])
      setVarReport(varRes.data)
      setSentiment(sentRes.data)
      setFundingRates(fundRes.data)
    } catch (e) {
      console.error(e)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchAll()
    const interval = setInterval(fetchAll, 60000)
    return () => clearInterval(interval)
  }, [])

  const sentimentStyle = SENTIMENT_COLORS[sentiment?.classification] || SENTIMENT_COLORS['Neutral']

  return (
    <div className="p-6 space-y-5 animate-fade-in">
      <PageHeader
        title="Institutional Analytics"
        subtitle="VaR, sentiment, funding rates — what hedge funds monitor"
        onRefresh={fetchAll}
        loading={loading}
      />

      {/* Fear & Greed Sentiment */}
      <div className="card">
        <h2 className="text-sm font-semibold text-slate-300 mb-4 flex items-center gap-2">
          <Brain size={16} className="text-purple-400" /> Fear & Greed Index
          <span className="text-xs text-slate-500 font-normal ml-1">Contrarian macro filter</span>
        </h2>
        {sentiment ? (
          <div className="space-y-4">
            <div className="flex items-center gap-6">
              <div className="w-24 h-24 rounded-full flex items-center justify-center flex-shrink-0"
                style={{ background: `conic-gradient(${sentimentStyle.bar} ${sentiment.value}%, rgba(100,116,139,0.2) 0%)` }}>
                <div className="w-16 h-16 rounded-full flex items-center justify-center" style={{ backgroundColor: '#141e35' }}>
                  <span className="text-2xl font-bold" style={{ color: sentimentStyle.color }}>{sentiment.value}</span>
                </div>
              </div>
              <div>
                <div className="text-lg font-bold mb-1" style={{ color: sentimentStyle.color }}>
                  {sentiment.classification}
                </div>
                <div className="text-xs text-slate-400 mb-2">{sentiment.reason}</div>
                <div className="flex items-center gap-2">
                  <span className="text-xs font-bold px-2 py-1 rounded-full" style={{ backgroundColor: sentimentStyle.bg, color: sentimentStyle.color }}>
                    Trading Bias: {sentiment.trading_bias}
                  </span>
                </div>
              </div>
            </div>
            <div className="text-xs text-slate-500 leading-relaxed p-3 rounded-lg" style={{ backgroundColor: '#1a2540' }}>
              <strong className="text-slate-400">How this filters trades:</strong> {
                sentiment.trading_bias === 'BUY_ONLY' ? 'Only BUY signals are allowed. Market sentiment is fearful — contrarian buying opportunity.' :
                sentiment.trading_bias === 'SELL_ONLY' ? 'Only SELL signals are allowed. Market is overheated — avoid chasing tops, protect profits.' :
                'All signal directions are permitted. Market sentiment is neutral — no macro override active.'
              }
            </div>
          </div>
        ) : (
          <p className="text-slate-500 text-sm text-center py-4">Start bot to fetch live sentiment data</p>
        )}
      </div>

      {/* VaR / Risk Metrics */}
      <div className="card">
        <h2 className="text-sm font-semibold text-slate-300 mb-4 flex items-center gap-2">
          <ShieldAlert size={16} className="text-red-400" /> Portfolio Risk (VaR / CVaR)
          <span className="text-xs text-slate-500 font-normal ml-1">Basel III standard</span>
        </h2>
        {varReport ? (
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <div>
              <InfoRow label="VaR 95% (daily)" value={`$${varReport.var_95}`} valueColor="#f87171" />
              <InfoRow label="VaR 99% (daily)" value={`$${varReport.var_99}`} valueColor="#ef4444" />
              <InfoRow label="CVaR 95% (Expected Shortfall)" value={`$${varReport.cvar_95}`} valueColor="#fb923c" />
              <InfoRow label="CVaR 99%" value={`$${varReport.cvar_99}`} valueColor="#f97316" />
            </div>
            <div>
              <InfoRow label="Daily Volatility" value={`${varReport.daily_volatility}%`} />
              <InfoRow label="Annualized Volatility" value={`${varReport.annualized_volatility}%`} />
              <InfoRow label="Sharpe Ratio" value={varReport.sharpe_ratio.toFixed(3)} valueColor={varReport.sharpe_ratio > 1 ? '#34d399' : varReport.sharpe_ratio > 0 ? '#94a3b8' : '#f87171'} />
              <InfoRow label="Sortino Ratio" value={varReport.sortino_ratio.toFixed(3)} valueColor={varReport.sortino_ratio > 1 ? '#34d399' : varReport.sortino_ratio > 0 ? '#94a3b8' : '#f87171'} />
              <InfoRow label="Worst Single Trade" value={`-$${varReport.max_observed_loss}`} valueColor="#f87171" />
            </div>
          </div>
        ) : (
          <p className="text-[13px] text-center py-4" style={{ color: 'var(--text-muted)' }}>VaR data populates after first trades close</p>
        )}

        <div className="mt-4 p-3 rounded-lg text-xs text-slate-500 leading-relaxed" style={{ backgroundColor: '#1a2540' }}>
          <strong className="text-slate-400">Sortino vs Sharpe:</strong> Sharpe penalizes all volatility (good and bad). Sortino only penalizes downside deviation — preferred by asymmetric return strategies like this one. Hedge funds target Sortino {'>'} 2.0.
        </div>
      </div>

      {/* Funding Rates */}
      <div className="card">
        <h2 className="text-sm font-semibold text-slate-300 mb-4 flex items-center gap-2">
          <Zap size={16} className="text-yellow-400" /> Perpetual Funding Rates
          <span className="text-xs text-slate-500 font-normal ml-1">Crypto-native alpha signal</span>
        </h2>
        {fundingRates.length > 0 ? (
          <div className="space-y-3">
            {fundingRates.map((fr) => {
              const isExtreme = Math.abs(fr.funding_rate) > 0.0005
              const isModerate = Math.abs(fr.funding_rate) > 0.0003
              const bias = fr.signal_bias
              const biasColor = bias === 'BULLISH_FOR_LONGS' ? '#4ade80' : bias === 'BEARISH_FOR_LONGS' ? '#f87171' : '#94a3b8'
              return (
                <div key={fr.symbol} className="rounded-xl p-3" style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border)' }}>
                  <div className="flex items-center justify-between mb-2">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-semibold text-slate-200">{fr.symbol.replace('/USDT', '')}</span>
                      {fr.is_simulated && <span className="text-xs px-1.5 py-0.5 rounded text-slate-500" style={{ backgroundColor: 'rgba(148,163,184,0.1)' }}>simulated</span>}
                    </div>
                    <span className="text-xs font-bold px-2 py-0.5 rounded-full" style={{ color: biasColor, backgroundColor: `${biasColor}20` }}>
                      {bias.replace(/_/g, ' ')}
                    </span>
                  </div>
                  <div className="grid grid-cols-3 gap-3 text-xs">
                    <div>
                      <div className="text-slate-500 mb-0.5">Rate (8h)</div>
                      <div className={`font-mono font-semibold ${fr.funding_rate_pct > 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                        {fr.funding_rate_pct > 0 ? '+' : ''}{fr.funding_rate_pct.toFixed(4)}%
                      </div>
                    </div>
                    <div>
                      <div className="text-slate-500 mb-0.5">Annualized</div>
                      <div className={`font-mono font-semibold ${fr.annualized_rate_pct > 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                        {fr.annualized_rate_pct > 0 ? '+' : ''}{fr.annualized_rate_pct.toFixed(1)}%
                      </div>
                    </div>
                    <div>
                      <div className="text-slate-500 mb-0.5">Signal</div>
                      <div className="font-semibold" style={{ color: biasColor }}>
                        {(fr.signal_strength * 100).toFixed(0)}% strength
                      </div>
                    </div>
                  </div>
                  {(isExtreme || isModerate) && (
                    <div className="mt-2 flex items-center gap-1.5 text-xs" style={{ color: isExtreme ? '#f87171' : '#fb923c' }}>
                      <AlertTriangle size={10} />
                      {isExtreme ? 'Extreme funding rate — high reversal probability' : 'Elevated funding — crowded trade warning'}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        ) : (
          <p className="text-slate-500 text-sm text-center py-4">Funding rate data populates after bot first runs</p>
        )}

        <div className="mt-4 p-3 rounded-lg text-xs text-slate-500 leading-relaxed" style={{ backgroundColor: '#1a2540' }}>
          <strong className="text-slate-400">Why funding rates matter:</strong> When perp futures trade above spot (positive funding), longs pay shorts — signal that the market is overcrowded long. Extreme positive funding reliably precedes short-term corrections. This bot blocks BUY signals when funding is extreme positive (and vice versa for shorts).
        </div>
      </div>

      {/* What we have vs. top funds */}
      <div className="card">
        <h2 className="text-sm font-semibold text-slate-300 mb-4 flex items-center gap-2">
          <TrendingUp size={16} className="text-blue-400" /> Institutional Feature Checklist
        </h2>
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-2 text-xs">
          {[
            ['✅', 'Multi-strategy signal ensemble (consensus voting)', 'Used by Citadel, Two Sigma'],
            ['✅', 'Market regime detection (ADX + volatility)', 'AQR, Renaissance use regime filters'],
            ['✅', 'Kelly criterion position sizing', 'Optimal bet fraction — Ed Thorp method'],
            ['✅', 'Rolling Sharpe ratio + dynamic strategy weighting', 'Capital allocation by alpha quality'],
            ['✅', 'Anti-martingale (convex) risk scaling', 'Reduces size during drawdowns'],
            ['✅', 'Volatility targeting', 'Bridgewater\'s All Weather core principle'],
            ['✅', 'Correlation guard between positions', 'Prevents fake diversification'],
            ['✅', 'Trailing stop-loss', 'Profit lock-in without manual monitoring'],
            ['✅', 'Drawdown circuit breaker', 'Hard risk-off switch'],
            ['✅', 'VaR / CVaR (historical simulation)', 'Required under Basel III'],
            ['✅', 'Sharpe + Sortino ratio tracking', 'Standard hedge fund risk metrics'],
            ['✅', 'Fear & Greed Index macro filter', 'Contrarian sentiment overlay'],
            ['✅', 'Funding rate signal', 'Crypto-native alpha — Alameda, Jump'],
            ['✅', 'Statistical arbitrage (pairs trading)', 'Core of Renaissance Medallion Fund'],
            ['✅', 'TWAP execution algorithm', 'Minimize market impact on large fills'],
            ['⚠️', 'Live exchange order book depth', 'Need live exchange API connection'],
            ['⚠️', 'On-chain analytics (whale flows)', 'Requires Glassnode/Nansen API'],
            ['⚠️', 'Mean-variance portfolio optimization', 'Markowitz — add with more assets'],
            ['⚠️', 'Walk-forward out-of-sample backtest', 'Validation — add historical data loader'],
          ].map(([status, feature, note]) => (
            <div key={feature} className="flex items-start gap-2 py-1.5 border-b" style={{ borderColor: 'rgba(100,116,139,0.1)' }}>
              <span className="flex-shrink-0 w-5">{status}</span>
              <div>
                <span className={status === '✅' ? 'text-slate-300' : 'text-slate-500'}>{feature}</span>
                <span className="text-slate-600 ml-2">({note})</span>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
