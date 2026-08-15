import Card from '../common/Card.jsx'
import StatusBadge from '../common/StatusBadge.jsx'
import ConfidenceBadge from './ConfidenceBadge.jsx'
import ReasonBreakdown from './ReasonBreakdown.jsx'

const SETUP_TYPE_TONE = {
  BREAKOUT: 'success',
  DIP_BUY: 'info',
}

function formatPrice(value) {
  return typeof value === 'number' && Number.isFinite(value) ? value.toFixed(2) : '—'
}

function formatTargets(targets) {
  if (!Array.isArray(targets) || targets.length === 0) return '—'
  return targets.map((t) => formatPrice(t)).join(' / ')
}

export default function CallCard({ call }) {
  const { symbol, setup_type: setupType, state, entry_price: entryPrice, stop_loss: stopLoss, targets, confidence, reason, ltp } = call

  const ltpUnavailable = ltp === null || ltp === undefined
  const lastUpdatedLabel = ltpUnavailable ? null : new Date().toLocaleTimeString()

  return (
    <Card>
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <h3 className="text-lg font-semibold text-white">{symbol}</h3>
            <StatusBadge
              label={setupType === 'DIP_BUY' ? 'DIP BUY' : 'BREAKOUT'}
              tone={SETUP_TYPE_TONE[setupType] || 'neutral'}
            />
            {state && <StatusBadge label={state} tone="neutral" />}
          </div>
          <div className="mt-1 text-sm text-slate-400">
            {ltpUnavailable ? (
              <span className="text-slate-500">LTP unavailable</span>
            ) : (
              <span>
                LTP <span className="font-mono text-slate-200">{formatPrice(ltp)}</span>
                <span className="ml-2 text-xs text-slate-500">as of {lastUpdatedLabel}</span>
              </span>
            )}
          </div>
        </div>
        <ConfidenceBadge confidence={confidence} />
      </div>

      <div className="mt-4 grid grid-cols-3 gap-3 text-sm">
        <div>
          <div className="text-xs uppercase tracking-wide text-slate-500">Entry</div>
          <div className="font-mono text-slate-200">{formatPrice(entryPrice)}</div>
        </div>
        <div>
          <div className="text-xs uppercase tracking-wide text-slate-500">Stop Loss</div>
          <div className="font-mono text-rose-300">{formatPrice(stopLoss)}</div>
        </div>
        <div>
          <div className="text-xs uppercase tracking-wide text-slate-500">Targets</div>
          <div className="font-mono text-emerald-300">{formatTargets(targets)}</div>
        </div>
      </div>

      <ReasonBreakdown reason={reason} />
    </Card>
  )
}
