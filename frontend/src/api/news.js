import { apiGet } from './client.js'

export function getNews({ symbol, minSeverity, limit } = {}) {
  return apiGet('/news', { symbol, min_severity: minSeverity, limit })
}
