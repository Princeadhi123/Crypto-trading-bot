import React, { useState, useEffect, useCallback } from 'react'
import { History, ChevronLeft, ChevronRight, Search, Filter } from 'lucide-react'
import { botApi } from '../api'
import { PageHeader, EmptyState, fmtPrice, fmtCurrency, fmtPct } from '../components/ui'

function TradeRow({ trade }) {
  const pnl = trade.profit_loss
  const pnlPct = trade.profit_loss_percent
  const isClosed = trade.status === 'closed'
  return (
    <tr>
      <td>
        <div className="font-semibold" style={{ color: 'var(--text-primary)', fontSize: 13 }}>{trade.symbol}</div>
        <div className="text-[10px] mt-0.5 truncate max-w-[120px]" style={{ color: 'var(--text-muted)' }}>{trade.strategy?.split('+')[0].trim()}</div>
      </td>
      <td><span className={`badge badge-${trade.side.toLowerCase()}`}>{trade.side}</span></td>
      <td className="mono" style={{ color: 'var(--text-secondary)', fontSize: 12 }}>{fmtPrice(trade.entry_price)}</td>
      <td className="mono" style={{ color: 'var(--text-secondary)', fontSize: 12 }}>{trade.exit_price ? fmtPrice(trade.exit_price) : '—'}</td>
      <td className="mono" style={{ color: 'var(--text-muted)', fontSize: 12 }}>{Number(trade.quantity).toFixed(5)}</td>
      <td>
        {isClosed && pnl !== null && pnl !== undefined ? (
          <div>
            <span className={pnl >= 0 ? 'pnl-positive mono' : 'pnl-negative mono'} style={{ fontSize: 12 }}>
              {pnl >= 0 ? '+' : ''}{fmtCurrency(pnl)}
            </span>
            <div className={`text-[10px] ${pnlPct >= 0 ? 'pnl-positive' : 'pnl-negative'}`}>
              {fmtPct(pnlPct)}
            </div>
          </div>
        ) : <span style={{ color: 'var(--text-muted)' }}>—</span>}
      </td>
      <td><span className={`badge badge-${trade.status}`}>{trade.status}</span></td>
      <td className="text-[11px]" style={{ color: 'var(--text-muted)' }}>{new Date(trade.opened_at).toLocaleString()}</td>
    </tr>
  )
}

const PAGE_SIZE = 20

export default function Trades() {
  const [trades, setTrades] = useState([])
  const [totalCount, setTotalCount] = useState(0)
  const [page, setPage] = useState(0)
  const [filterStatus, setFilterStatus] = useState('')
  const [filterSymbol, setFilterSymbol] = useState('')
  const [loading, setLoading] = useState(true)

  const fetchTrades = useCallback(async () => {
    setLoading(true)
    try {
      const params = {
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
      }
      if (filterStatus) params.status = filterStatus
      if (filterSymbol) params.symbol = filterSymbol.toUpperCase()

      const countParams = {}
      if (filterStatus) countParams.status = filterStatus
      if (filterSymbol) countParams.symbol = filterSymbol.toUpperCase()
      const [tradesRes, countRes] = await Promise.all([
        botApi.getTrades(params),
        botApi.getTradeCount(countParams),
      ])
      setTrades(tradesRes.data)
      setTotalCount(countRes.data.count)
    } catch (e) {
      console.error(e)
    } finally {
      setLoading(false)
    }
  }, [page, filterStatus, filterSymbol])

  useEffect(() => {
    fetchTrades()
  }, [fetchTrades])

  useEffect(() => {
    setPage(0)
  }, [filterStatus, filterSymbol])

  const totalPages = Math.ceil(totalCount / PAGE_SIZE)

  return (
    <div className="p-6 space-y-5 animate-fade-in">
      <PageHeader
        title="Trade History"
        subtitle={`${totalCount.toLocaleString()} trades recorded`}
        onRefresh={fetchTrades}
        loading={loading}
      />

      <div className="flex gap-3 flex-wrap">
        <div className="relative flex-1" style={{ minWidth: 200, maxWidth: 280 }}>
          <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2" style={{ color: 'var(--text-muted)' }} />
          <input
            type="text"
            placeholder="Symbol (e.g. BTC/USDT)"
            value={filterSymbol}
            onChange={e => setFilterSymbol(e.target.value)}
            className="input-field"
            style={{ paddingLeft: 36 }}
          />
        </div>
        <div className="relative">
          <Filter size={13} className="absolute left-3 top-1/2 -translate-y-1/2" style={{ color: 'var(--text-muted)' }} />
          <select
            value={filterStatus}
            onChange={e => setFilterStatus(e.target.value)}
            className="input-field appearance-none cursor-pointer"
            style={{ paddingLeft: 36, paddingRight: 32, width: 'auto' }}
          >
            <option value="">All Status</option>
            <option value="open">Open</option>
            <option value="closed">Closed</option>
          </select>
        </div>
      </div>

      <div className="card" style={{ padding: 0, overflow: 'hidden' }}>
        {trades.length === 0 && !loading ? (
          <EmptyState icon={History} title="No trades found" description="Trades appear here once the bot starts executing" />
        ) : (
          <div className="overflow-x-auto">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th>Side</th>
                  <th>Entry</th>
                  <th>Exit</th>
                  <th>Qty</th>
                  <th>P&amp;L</th>
                  <th>Status</th>
                  <th>Opened At</th>
                </tr>
              </thead>
              <tbody>
                {trades.map(trade => <TradeRow key={trade.id} trade={trade} />)}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {totalPages > 1 && (
        <div className="flex items-center justify-between">
          <span className="text-[12px]" style={{ color: 'var(--text-muted)' }}>
            Page {page + 1} of {totalPages} &middot; {totalCount.toLocaleString()} trades
          </span>
          <div className="flex items-center gap-1.5">
            <button className="btn-ghost" style={{ padding: '5px 8px' }} onClick={() => setPage(p => Math.max(0, p - 1))} disabled={page === 0}>
              <ChevronLeft size={13} />
            </button>
            {Array.from({ length: Math.min(5, totalPages) }, (_, i) => {
              const pageNum = Math.max(0, Math.min(page - 2, totalPages - 5)) + i
              return (
                <button
                  key={pageNum}
                  onClick={() => setPage(pageNum)}
                  style={{
                    width: 28, height: 28, borderRadius: 6, fontSize: 12, fontWeight: 500,
                    background: pageNum === page ? '#10b981' : 'transparent',
                    color: pageNum === page ? 'white' : 'var(--text-muted)',
                    border: pageNum === page ? 'none' : '1px solid var(--border)',
                    cursor: 'pointer', transition: 'all 0.15s',
                  }}
                >
                  {pageNum + 1}
                </button>
              )
            })}
            <button className="btn-ghost" style={{ padding: '5px 8px' }} onClick={() => setPage(p => Math.min(totalPages - 1, p + 1))} disabled={page >= totalPages - 1}>
              <ChevronRight size={13} />
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
