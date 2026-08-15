import { useMemo, useState } from 'react'
import Card from '../common/Card.jsx'
import StatusBadge from '../common/StatusBadge.jsx'

const SETUP_FILTERS = ['ALL', 'BREAKOUT', 'DIP_BUY']

function formatPrice(value) {
  return typeof value === 'number' && Number.isFinite(value) ? value.toFixed(2) : '—'
}

function formatTargets(targets) {
  if (!Array.isArray(targets) || targets.length === 0) return '—'
  return targets.map((t) => formatPrice(t)).join(' / ')
}

function outcomeLabel(outcome) {
  if (!outcome) return { label: 'Open', tone: 'neutral' }
  if (outcome.target_hit) return { label: 'Target Hit', tone: 'success' }
  if (outcome.sl_hit) return { label: 'SL Hit', tone: 'danger' }
  if (outcome.exit_reason) return { label: outcome.exit_reason, tone: 'warning' }
  return { label: 'Closed', tone: 'neutral' }
}

export default function TradeList({ trades }) {
  const [setupFilter, setSetupFilter] = useState('ALL')

  const filteredTrades = useMemo(() => {
    if (!Array.isArray(trades)) return []
    if (setupFilter === 'ALL') return trades
    return trades.filter((t) => t.setup_type === setupFilter)
  }, [trades, setupFilter])

  return (
    <Card>
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-400">Trades</h2>
        <div className="flex gap-1">
          {SETUP_FILTERS.map((option) => (
            <button
              key={option}
              type="button"
              onClick={() => setSetupFilter(option)}
              className={`rounded-md px-2.5 py-1 text-xs font-medium ${
                setupFilter === option
                  ? 'bg-slate-700 text-white'
                  : 'bg-slate-900 text-slate-400 hover:bg-slate-800'
              }`}
            >
              {option === 'ALL' ? 'All' : option === 'DIP_BUY' ? 'Dip Buy' : 'Breakout'}
            </button>
          ))}
        </div>
      </div>

      {filteredTrades.length === 0 && <p className="text-slate-400">No trades match this filter.</p>}

      {filteredTrades.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead>
              <tr className="border-b border-slate-800 text-xs uppercase tracking-wide text-slate-500">
                <th className="py-2 pr-4">Symbol</th>
                <th className="py-2 pr-4">Setup</th>
                <th className="py-2 pr-4">Entry Date</th>
                <th className="py-2 pr-4">Entry</th>
                <th className="py-2 pr-4">SL</th>
                <th className="py-2 pr-4">Targets</th>
                <th className="py-2 pr-4">Score</th>
                <th className="py-2 pr-4">Outcome</th>
              </tr>
            </thead>
            <tbody>
              {filteredTrades.map((trade) => {
                const outcome = outcomeLabel(trade.outcome)
                return (
                  <tr key={trade.trade_setup_id} className="border-b border-slate-900">
                    <td className="py-2 pr-4 font-mono text-slate-200">{trade.symbol}</td>
                    <td className="py-2 pr-4 text-slate-300">{trade.setup_type}</td>
                    <td className="py-2 pr-4 text-slate-300">{trade.entry_date}</td>
                    <td className="py-2 pr-4 font-mono text-slate-300">{formatPrice(trade.entry_price)}</td>
                    <td className="py-2 pr-4 font-mono text-rose-300">{formatPrice(trade.stop_loss)}</td>
                    <td className="py-2 pr-4 font-mono text-emerald-300">{formatTargets(trade.targets)}</td>
                    <td className="py-2 pr-4 text-slate-300">{trade.score ?? '—'}</td>
                    <td className="py-2 pr-4">
                      <StatusBadge label={outcome.label} tone={outcome.tone} />
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  )
}
