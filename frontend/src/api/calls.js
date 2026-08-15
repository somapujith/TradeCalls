import { apiGet } from './client.js'

export function getCalls() {
  return apiGet('/calls')
}

export function getLtp(symbol) {
  return apiGet(`/ltp/${encodeURIComponent(symbol)}`)
}
