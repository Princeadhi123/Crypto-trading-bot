import axios from 'axios'

const apiClient = axios.create({
  baseURL: '/api',
  timeout: 10000,
})

export const botApi = {
  getStatus: () => apiClient.get('/status'),
  startBot: () => apiClient.post('/bot/start'),
  stopBot: () => apiClient.post('/bot/stop'),
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
