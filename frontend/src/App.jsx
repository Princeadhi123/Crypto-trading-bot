import React, { useState, useCallback } from 'react'
import { BrowserRouter, Routes, Route, NavLink } from 'react-router-dom'
import {
  LayoutDashboard, TrendingUp, Briefcase, History,
  Settings, Zap, BarChart3, Circle, Radio
} from 'lucide-react'
import { useWebSocket } from './hooks/useWebSocket'
import Dashboard from './pages/Dashboard'
import Strategies from './pages/Strategies'
import Portfolio from './pages/Portfolio'
import Trades from './pages/Trades'
import SettingsPage from './pages/SettingsPage'
import Analytics from './pages/Analytics'

const NAV_GROUPS = [
  {
    label: 'Overview',
    items: [
      { path: '/', label: 'Dashboard', icon: LayoutDashboard },
      { path: '/portfolio', label: 'Portfolio', icon: Briefcase },
    ],
  },
  {
    label: 'Trading',
    items: [
      { path: '/strategies', label: 'Strategies', icon: TrendingUp },
      { path: '/trades', label: 'Trade History', icon: History },
    ],
  },
  {
    label: 'Intelligence',
    items: [
      { path: '/analytics', label: 'Inst. Analytics', icon: BarChart3 },
    ],
  },
  {
    label: 'System',
    items: [
      { path: '/settings', label: 'Settings', icon: Settings },
    ],
  },
]

function NavItem({ path, label, icon: Icon }) {
  return (
    <NavLink
      to={path}
      end={path === '/'}
      className={({ isActive }) =>
        `flex items-center gap-3 px-3 py-2 rounded-lg text-[13px] font-medium transition-all duration-150 ${
          isActive ? 'nav-active' : 'nav-item'
        }`
      }
    >
      <Icon size={15} strokeWidth={1.8} />
      {label}
    </NavLink>
  )
}

export default function App() {
  const [wsEvents, setWsEvents] = useState([])

  const handleWsMessage = useCallback((message) => {
    setWsEvents(prev => [message, ...prev].slice(0, 100))
  }, [])

  const { isConnected } = useWebSocket(handleWsMessage)

  return (
    <BrowserRouter>
      <div className="flex h-screen overflow-hidden" style={{ backgroundColor: 'var(--bg-base)' }}>

        {/* ── Sidebar ─────────────────────────────────────────── */}
        <aside
          className="w-56 flex-shrink-0 flex flex-col"
          style={{
            backgroundColor: 'var(--bg-surface)',
            borderRight: '1px solid var(--border)',
          }}
        >
          {/* Logo */}
          <div className="px-4 py-5" style={{ borderBottom: '1px solid var(--border)' }}>
            <div className="flex items-center gap-3">
              <div
                className="w-8 h-8 rounded-xl flex items-center justify-center flex-shrink-0"
                style={{
                  background: 'linear-gradient(135deg, #10b981 0%, #3b82f6 100%)',
                  boxShadow: '0 0 16px rgba(16,185,129,0.3)',
                }}
              >
                <Zap size={15} color="white" strokeWidth={2.5} />
              </div>
              <div>
                <div className="text-[13px] font-bold tracking-tight" style={{ color: 'var(--text-primary)' }}>
                  CryptoBot Pro
                </div>
                <div className="text-[10px] font-medium" style={{ color: 'var(--text-muted)' }}>
                  v2.0 · Quant Edition
                </div>
              </div>
            </div>
          </div>

          {/* Nav */}
          <nav className="flex-1 overflow-y-auto px-3 py-3 space-y-4">
            {NAV_GROUPS.map(({ label, items }) => (
              <div key={label}>
                <div
                  className="text-[9px] font-semibold uppercase tracking-widest px-3 mb-1.5"
                  style={{ color: 'var(--text-muted)' }}
                >
                  {label}
                </div>
                <div className="space-y-0.5">
                  {items.map((item) => <NavItem key={item.path} {...item} />)}
                </div>
              </div>
            ))}
          </nav>

          {/* Status footer */}
          <div className="px-4 pb-4 pt-3" style={{ borderTop: '1px solid var(--border)' }}>
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                {isConnected ? (
                  <>
                    <div className="live-dot" />
                    <span className="text-[11px] font-medium" style={{ color: '#34d399' }}>
                      Live
                    </span>
                  </>
                ) : (
                  <>
                    <Circle size={7} style={{ color: '#ef4444', fill: '#ef4444' }} />
                    <span className="text-[11px] font-medium" style={{ color: '#f87171' }}>
                      Offline
                    </span>
                  </>
                )}
              </div>
              <Radio size={11} style={{ color: isConnected ? '#34d399' : 'var(--text-muted)' }} />
            </div>
          </div>
        </aside>

        {/* ── Main ──────────────────────────────────────────────── */}
        <main className="flex-1 overflow-auto" style={{ backgroundColor: 'var(--bg-base)' }}>
          <Routes>
            <Route path="/" element={<Dashboard wsEvents={wsEvents} />} />
            <Route path="/strategies" element={<Strategies />} />
            <Route path="/portfolio" element={<Portfolio />} />
            <Route path="/trades" element={<Trades />} />
            <Route path="/analytics" element={<Analytics />} />
            <Route path="/settings" element={<SettingsPage />} />
          </Routes>
        </main>
      </div>
    </BrowserRouter>
  )
}
