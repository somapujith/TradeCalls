const BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000/api'

export class ApiError extends Error {
  constructor(message, status) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

function buildQueryString(params) {
  if (!params) return ''
  const entries = Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== '')
  if (entries.length === 0) return ''
  const search = new URLSearchParams(entries.map(([k, v]) => [k, String(v)]))
  return `?${search.toString()}`
}

export async function apiGet(path, params) {
  const url = `${BASE_URL}${path}${buildQueryString(params)}`

  let response
  try {
    response = await fetch(url)
  } catch (networkError) {
    throw new ApiError('Unable to reach the server. Check your connection and try again.', 0)
  }

  let body = null
  try {
    body = await response.json()
  } catch {
    // Non-JSON or empty body — fall through to status-based handling below.
  }

  if (!response.ok) {
    const detail = body && typeof body.detail === 'string' ? body.detail : `Request failed (${response.status})`
    throw new ApiError(detail, response.status)
  }

  return body
}
