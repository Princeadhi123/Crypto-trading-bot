import axios from 'axios'
import { getAuthToken } from './auth'

const apiClient = axios.create({
  baseURL: '/api',
  timeout: 10000,
})

// Attach Bearer token to every authenticated request using the stored JWT
apiClient.interceptors.request.use((config) => {
  const token = getAuthToken()
  if (token) {
    config.headers['Authorization'] = `Bearer ${token}`
  }
  return config
})

export const botApi = {
  login: (username, password) => axios.post('/api/auth/login', { username, password }),
  authStatus: () => axios.get('/api/auth/status'),
  getStatus: () => apiClient.get('/status'),
  startBot: () => apiClient.post('/bot/start'),
  stopBot: () => apiClient.post('/bot/stop'),
  resetDrawdown: () => apiClient.post('/bot/reset-drawdown'),
  getSettings: () => apiClient.get('/settings'),
  updateSettings: (settings) => apiClient.put('/settings', settings),
  getPortfolio: () => apiClient.get('/portfolio'),
  getPositions: () => apiClient.get('/positions'),
  closePosition: (symbol) => apiClient.post('/positions/close', null, { params: { symbol } }),
  getTrades: (params) => apiClient.get('/trades', { params }),
  getTradeCount: (params) => apiClient.get('/trades/count', { params }),
  getMarketPrices: () => apiClient.get('/market/prices'),
  getRecentSignals: () => apiClient.get('/signals/recent'),
  getStrategies: () => apiClient.get('/strategies'),
  toggleStrategy: (id) => apiClient.patch(`/strategies/${id}/toggle`),
  getPnlChart: (days) => apiClient.get('/analytics/pnl-chart', { params: { days } }),
  getStrategyPerformance: () => apiClient.get('/analytics/strategy-performance'),
  getLivePerformance: () => apiClient.get('/analytics/live-performance'),
  getRegimeInfo: () => apiClient.get('/analytics/regime'),
  getVarReport: () => apiClient.get('/analytics/var'),
  getSentiment: () => apiClient.get('/analytics/sentiment'),
  getFundingRates: () => apiClient.get('/analytics/funding-rates'),
}

export default apiClient
