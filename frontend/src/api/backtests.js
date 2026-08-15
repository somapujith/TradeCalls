import { apiGet } from './client.js'

export function getBacktests() {
  return apiGet('/backtests')
}

export function getBacktestMetrics(strategyVersion) {
  return apiGet(`/backtests/${encodeURIComponent(strategyVersion)}/metrics`)
}

export function getBacktestTrades(strategyVersion, filters = {}) {
  const { symbol, scoreMin, setupType, limit, offset } = filters
  return apiGet(`/backtests/${encodeURIComponent(strategyVersion)}/trades`, {
    symbol,
    score_min: scoreMin,
    setup_type: setupType,
    limit,
    offset,
  })
}
