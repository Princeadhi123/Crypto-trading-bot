import React, { useState } from 'react'
import { Zap, Lock, User, Eye, EyeOff, AlertCircle } from 'lucide-react'
import axios from 'axios'
import { setAuthToken } from '../auth'

export default function LoginPage({ onLogin }) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const handleSubmit = async (e) => {
    e.preventDefault()
    setError('')
    if (!username.trim() || !password) {
      setError('Username and password are required.')
      return
    }
    setLoading(true)
    try {
      const res = await axios.post('/api/auth/login', { username: username.trim(), password })
      setAuthToken(res.data.access_token)
      onLogin()
    } catch (err) {
      const detail = err?.response?.data?.detail
      if (err?.response?.status === 503) {
        setError('Login system is not configured. Set ADMIN_PASSWORD_HASH in backend/.env.')
      } else if (err?.response?.status === 401) {
        setError('Invalid username or password.')
      } else {
        setError(detail || 'Login failed. Is the backend running?')
      }
    } finally {
      setLoading(false)
    }
  }

  return (
    <div
      className="flex items-center justify-center min-h-screen"
      style={{ backgroundColor: 'var(--bg-base)' }}
    >
      <div style={{ width: '100%', maxWidth: 380 }}>
        {/* Logo */}
        <div className="flex flex-col items-center mb-8">
          <div
            className="w-14 h-14 rounded-2xl flex items-center justify-center mb-4"
            style={{
              background: 'linear-gradient(135deg, #10b981 0%, #3b82f6 100%)',
              boxShadow: '0 0 32px rgba(16,185,129,0.25)',
            }}
          >
            <Zap size={26} color="white" strokeWidth={2.5} />
          </div>
          <div className="text-[22px] font-bold tracking-tight" style={{ color: 'var(--text-primary)' }}>
            CryptoBot Pro
          </div>
          <div className="text-[12px] mt-1" style={{ color: 'var(--text-muted)' }}>
            Sign in to continue
          </div>
        </div>

        {/* Card */}
        <div className="card" style={{ padding: '28px 24px' }}>
          <form onSubmit={handleSubmit} className="space-y-4">
            {/* Username */}
            <div>
              <label className="block text-[11px] font-semibold mb-1.5" style={{ color: 'var(--text-muted)' }}>
                USERNAME
              </label>
              <div className="relative">
                <User
                  size={13}
                  className="absolute left-3 top-1/2 -translate-y-1/2"
                  style={{ color: 'var(--text-muted)' }}
                />
                <input
                  type="text"
                  value={username}
                  onChange={e => setUsername(e.target.value)}
                  placeholder="admin"
                  autoComplete="username"
                  autoFocus
                  className="input-field w-full"
                  style={{ paddingLeft: 34 }}
                  disabled={loading}
                />
              </div>
            </div>

            {/* Password */}
            <div>
              <label className="block text-[11px] font-semibold mb-1.5" style={{ color: 'var(--text-muted)' }}>
                PASSWORD
              </label>
              <div className="relative">
                <Lock
                  size={13}
                  className="absolute left-3 top-1/2 -translate-y-1/2"
                  style={{ color: 'var(--text-muted)' }}
                />
                <input
                  type={showPassword ? 'text' : 'password'}
                  value={password}
                  onChange={e => setPassword(e.target.value)}
                  placeholder="••••••••"
                  autoComplete="current-password"
                  className="input-field w-full"
                  style={{ paddingLeft: 34, paddingRight: 36 }}
                  disabled={loading}
                />
                <button
                  type="button"
                  onClick={() => setShowPassword(v => !v)}
                  className="absolute right-3 top-1/2 -translate-y-1/2"
                  style={{ color: 'var(--text-muted)', background: 'none', border: 'none', cursor: 'pointer', padding: 0 }}
                  tabIndex={-1}
                >
                  {showPassword ? <EyeOff size={13} /> : <Eye size={13} />}
                </button>
              </div>
            </div>

            {/* Error */}
            {error && (
              <div
                className="flex items-start gap-2 p-3 rounded-lg text-[12px]"
                style={{ background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.2)' }}
              >
                <AlertCircle size={13} style={{ color: '#f87171', flexShrink: 0, marginTop: 1 }} />
                <span style={{ color: '#fca5a5' }}>{error}</span>
              </div>
            )}

            {/* Submit */}
            <button
              type="submit"
              disabled={loading}
              className="w-full"
              style={{
                padding: '10px 0',
                borderRadius: 8,
                fontSize: 13,
                fontWeight: 600,
                background: loading ? 'rgba(16,185,129,0.4)' : 'linear-gradient(135deg, #10b981 0%, #059669 100%)',
                color: 'white',
                border: 'none',
                cursor: loading ? 'not-allowed' : 'pointer',
                transition: 'all 0.15s',
                letterSpacing: '0.02em',
              }}
            >
              {loading ? 'Signing in…' : 'Sign in'}
            </button>
          </form>
        </div>

        <p className="text-center text-[11px] mt-5" style={{ color: 'var(--text-muted)' }}>
          Set <code style={{ color: 'var(--text-secondary)' }}>ADMIN_PASSWORD_HASH</code> in{' '}
          <code style={{ color: 'var(--text-secondary)' }}>backend/.env</code> to enable login.
        </p>
      </div>
    </div>
  )
}
