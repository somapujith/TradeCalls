import { useCallback } from 'react'
import { getCalls } from '../api/calls.js'
import { usePolling } from '../hooks/usePolling.js'
import CallCard from '../components/calls/CallCard.jsx'

const POLL_INTERVAL_MS = 60_000 // matches /api/calls' LTP cache TTL per api.md

export default function CallsPage() {
  const fetchCalls = useCallback(() => getCalls(), [])
  const { data: calls, error, loading, refetch } = usePolling(fetchCalls, POLL_INTERVAL_MS)

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <h1 className="text-xl font-semibold text-white">Active Calls</h1>
        <button
          type="button"
          onClick={refetch}
          className="rounded-md border border-slate-700 bg-slate-900 px-3 py-1.5 text-sm text-slate-300 hover:bg-slate-800"
        >
          Refresh
        </button>
      </div>

      {loading && !calls && <p className="text-slate-400">Loading calls…</p>}

      {error && (
        <p className="rounded-md border border-rose-800 bg-rose-950/50 p-3 text-sm text-rose-300">
          Failed to load calls: {error.message}
        </p>
      )}

      {!loading && !error && Array.isArray(calls) && calls.length === 0 && (
        <p className="text-slate-400">No active calls right now.</p>
      )}

      {Array.isArray(calls) && calls.length > 0 && (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
          {calls.map((call) => (
            <CallCard key={`${call.symbol}-${call.setup_type}`} call={call} />
          ))}
        </div>
      )}
    </div>
  )
}
