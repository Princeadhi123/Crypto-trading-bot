import React from 'react'
import { RefreshCw, TrendingUp, TrendingDown, Minus } from 'lucide-react'

// ─── Formatters ────────────────────────────────────────────────
export function fmtCurrency(value, decimals = 4) {
  if (value === null || value === undefined) return '—'
  const abs = Math.abs(value)
  if (abs >= 1_000_000) return `$${(value / 1_000_000).toFixed(4)}M`
  if (abs >= 1_000) return `$${value.toLocaleString('en-US', { minimumFractionDigits: decimals, maximumFractionDigits: decimals })}`
  return `$${value.toFixed(4)}`
}

export function fmtPct(value, decimals = 4) {
  if (value === null || value === undefined) return '—'
  const sign = value >= 0 ? '+' : ''
  return `${sign}${value.toFixed(decimals)}%`
}

export function fmtPrice(value) {
  if (!value) return '—'
  if (value >= 10000) return `$${value.toLocaleString('en-US', { minimumFractionDigits: 4, maximumFractionDigits: 4 })}`
  if (value >= 1) return `$${value.toFixed(4)}`
  return `$${value.toFixed(6)}`
}

export function fmtNumber(value) {
  if (value === null || value === undefined) return '—'
  return value.toLocaleString('en-US')
}

// ─── PnlText ──────────────────────────────────────────────────
export function PnlText({ value, showSign = true, prefix = '$', suffix = '', className = '' }) {
  if (value === null || value === undefined) return <span className="pnl-zero">—</span>
  const isPos = value > 0
  const isNeg = value < 0
  const cls = isPos ? 'pnl-positive' : isNeg ? 'pnl-negative' : 'pnl-zero'
  const sign = showSign ? (isPos ? '+' : '') : ''
  return (
    <span className={`${cls} ${className}`}>
      {sign}{prefix}{Math.abs(value).toFixed(4)}{suffix}
    </span>
  )
}

// ─── TrendIcon ────────────────────────────────────────────────
export function TrendIcon({ value, size = 14 }) {
  if (value > 0) return <TrendingUp size={size} color="#34d399" />
  if (value < 0) return <TrendingDown size={size} color="#f87171" />
  return <Minus size={size} color="#475569" />
}

// ─── PageHeader ───────────────────────────────────────────────
export function PageHeader({ title, subtitle, actions, onRefresh, loading }) {
  return (
    <div className="flex items-start justify-between mb-6">
      <div>
        <h1 className="text-[18px] font-bold tracking-tight" style={{ color: 'var(--text-primary)' }}>
          {title}
        </h1>
        {subtitle && (
          <p className="text-[13px] mt-0.5" style={{ color: 'var(--text-muted)' }}>
            {subtitle}
          </p>
        )}
      </div>
      <div className="flex items-center gap-2 flex-shrink-0">
        {actions}
        {onRefresh && (
          <button className="btn-ghost" onClick={onRefresh} disabled={loading} title={loading ? 'Refreshing…' : 'Refresh'}>
            <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
          </button>
        )}
      </div>
    </div>
  )
}

// ─── StatCard ─────────────────────────────────────────────────
export function StatCard({ title, value, subtitle, icon: Icon, accentColor = '#10b981', trend, sparkline }) {
  return (
    <div className="stat-card animate-fade-in">
      {/* accent glow at top matching color */}
      <div style={{
        position: 'absolute', top: 0, left: 0, right: 0, height: 1,
        background: `linear-gradient(90deg, transparent, ${accentColor}55, transparent)`
      }} />
      <div className="flex items-start justify-between mb-3">
        <span
          className="text-[10px] font-semibold uppercase tracking-widest"
          style={{ color: 'var(--text-muted)' }}
        >
          {title}
        </span>
        {Icon && (
          <div
            className="p-1.5 rounded-lg"
            style={{ backgroundColor: `${accentColor}15`, border: `1px solid ${accentColor}25` }}
          >
            <Icon size={13} style={{ color: accentColor }} />
          </div>
        )}
      </div>
      <div
        className="text-[22px] font-bold mono tracking-tight mb-1"
        style={{ color: accentColor }}
      >
        {value}
      </div>
      {subtitle && (
        <div className="text-[11px]" style={{ color: 'var(--text-muted)' }}>
          {subtitle}
        </div>
      )}
      {trend !== undefined && (
        <div
          className="flex items-center gap-1 text-[11px] font-medium mt-1.5"
          style={{ color: trend >= 0 ? '#34d399' : '#f87171' }}
        >
          <TrendIcon value={trend} size={11} />
          {fmtPct(Math.abs(trend))} today
        </div>
      )}
    </div>
  )
}

// ─── Card ─────────────────────────────────────────────────────
export function Card({ children, className = '', title, subtitle, action, noPadding = false }) {
  return (
    <div className={`card ${className}`} style={{ padding: noPadding ? 0 : undefined }}>
      {(title || subtitle || action) && (
        <div className="flex items-center justify-between mb-4" style={{ padding: noPadding ? '16px 20px 0' : undefined }}>
          <div>
            {title && (
              <h2 className="text-[13px] font-semibold" style={{ color: 'var(--text-primary)' }}>
                {title}
              </h2>
            )}
            {subtitle && (
              <p className="text-[11px] mt-0.5" style={{ color: 'var(--text-muted)' }}>
                {subtitle}
              </p>
            )}
          </div>
          {action && <div>{action}</div>}
        </div>
      )}
      {children}
    </div>
  )
}

// ─── Badge ────────────────────────────────────────────────────
export function Badge({ variant = 'default', children }) {
  return <span className={`badge badge-${variant}`}>{children}</span>
}

// ─── Skeleton ─────────────────────────────────────────────────
export function Skeleton({ h = 16, w = '100%', className = '' }) {
  return (
    <div
      className={`skeleton ${className}`}
      style={{ height: h, width: typeof w === 'number' ? `${w}px` : w }}
    />
  )
}

export function SkeletonCard({ rows = 3 }) {
  return (
    <div className="card space-y-3">
      <Skeleton h={14} w="40%" />
      <Skeleton h={28} w="60%" />
      {Array.from({ length: rows - 2 }).map((_, i) => (
        <Skeleton key={i} h={12} w={`${70 - i * 10}%`} />
      ))}
    </div>
  )
}

export function SkeletonTable({ rows = 5 }) {
  return (
    <div className="card" style={{ padding: 0 }}>
      <div className="p-4 space-y-3">
        {Array.from({ length: rows }).map((_, i) => (
          <div key={i} className="flex items-center gap-4">
            <Skeleton h={12} w="15%" />
            <Skeleton h={12} w="8%" />
            <Skeleton h={12} w="12%" />
            <Skeleton h={12} w="12%" />
            <Skeleton h={12} w="10%" />
          </div>
        ))}
      </div>
    </div>
  )
}

// ─── EmptyState ───────────────────────────────────────────────
export function EmptyState({ icon: Icon, title, description, action }) {
  return (
    <div className="flex flex-col items-center justify-center py-14 px-6 text-center animate-fade-in">
      {Icon && (
        <div
          className="w-12 h-12 rounded-2xl flex items-center justify-center mb-4"
          style={{ backgroundColor: 'rgba(255,255,255,0.04)', border: '1px solid var(--border)' }}
        >
          <Icon size={22} style={{ color: 'var(--text-muted)' }} />
        </div>
      )}
      <p className="text-[14px] font-semibold mb-1" style={{ color: 'var(--text-secondary)' }}>
        {title}
      </p>
      {description && (
        <p className="text-[12px] max-w-xs" style={{ color: 'var(--text-muted)' }}>
          {description}
        </p>
      )}
      {action && <div className="mt-4">{action}</div>}
    </div>
  )
}

// ─── SectionLabel ─────────────────────────────────────────────
export function SectionLabel({ children }) {
  return (
    <div
      className="text-[10px] font-semibold uppercase tracking-widest mb-3"
      style={{ color: 'var(--text-muted)' }}
    >
      {children}
    </div>
  )
}

// ─── Divider ──────────────────────────────────────────────────
export function Divider({ className = '' }) {
  return <div className={`divider ${className}`} />
}

// ─── InfoRow ──────────────────────────────────────────────────
export function InfoRow({ label, value, valueColor }) {
  return (
    <div className="flex items-center justify-between py-2" style={{ borderBottom: '1px solid var(--border)' }}>
      <span className="text-[12px]" style={{ color: 'var(--text-muted)' }}>{label}</span>
      <span className="text-[12px] font-semibold mono" style={{ color: valueColor || 'var(--text-primary)' }}>
        {value}
      </span>
    </div>
  )
}

// ─── Tooltip for Recharts ─────────────────────────────────────
export function ChartTooltip({ active, payload, label, formatter }) {
  if (!active || !payload?.length) return null
  return (
    <div
      className="animate-fade-in"
      style={{
        background: 'var(--bg-card)',
        border: '1px solid var(--border)',
        borderRadius: 8,
        padding: '8px 12px',
        boxShadow: '0 8px 24px rgba(0,0,0,0.5)',
        fontSize: 12,
      }}
    >
      <p style={{ color: 'var(--text-muted)', marginBottom: 4 }}>{label}</p>
      {payload.map((entry, i) => (
        <div key={i} style={{ color: entry.value >= 0 ? '#34d399' : '#f87171', fontWeight: 600 }}>
          {formatter ? formatter(entry.value) : entry.value}
        </div>
      ))}
    </div>
  )
}
