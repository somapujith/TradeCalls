import Card from '../common/Card.jsx'
import StatusBadge from '../common/StatusBadge.jsx'

// Event types the user explicitly called out as the "court case" class of risk —
// legal/regulatory/balance-sheet/insider events that deserve a strong visual flag
// even before weighing severity.
const CRITICAL_EVENT_TYPES = new Set(['LEGAL', 'REGULATORY', 'DEBT', 'INSIDER_ACTIVITY'])

const SENTIMENT_TONE = {
  POSITIVE: 'success',
  NEGATIVE: 'danger',
  NEUTRAL: 'neutral',
}

function titleCase(value) {
  if (!value) return 'Unclassified'
  return value
    .split('_')
    .map((word) => word.charAt(0) + word.slice(1).toLowerCase())
    .join(' ')
}

function formatConfidence(confidence) {
  return typeof confidence === 'number' && Number.isFinite(confidence) ? `${Math.round(confidence * 100)}%` : '—'
}

function formatPublishedAt(isoString) {
  const date = new Date(isoString)
  if (Number.isNaN(date.getTime())) return { relative: '—', absolute: '—' }

  const diffMs = Date.now() - date.getTime()
  const diffMin = Math.floor(diffMs / 60_000)

  let relative
  if (diffMin < 1) relative = 'just now'
  else if (diffMin < 60) relative = `${diffMin}m ago`
  else if (diffMin < 24 * 60) relative = `${Math.floor(diffMin / 60)}h ago`
  else if (diffMin < 7 * 24 * 60) relative = `${Math.floor(diffMin / (24 * 60))}d ago`
  else relative = null

  const absolute = date.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
  return { relative: relative ?? absolute, absolute }
}

export default function NewsCard({ article }) {
  const { symbol, headline, source, url, summary, published_at: publishedAt, event_type: eventType, sentiment, severity, confidence } = article

  const isNegativeHighSeverity = sentiment === 'NEGATIVE' && severity >= 3
  const isCriticalEvent = CRITICAL_EVENT_TYPES.has(eventType)
  const isHighAlert = isNegativeHighSeverity && isCriticalEvent

  const { relative, absolute } = formatPublishedAt(publishedAt)

  const cardClassName = isHighAlert
    ? 'border-rose-600 bg-rose-950/30 ring-1 ring-rose-700/60'
    : isNegativeHighSeverity
      ? 'border-rose-800/80 bg-rose-950/10'
      : ''

  return (
    <Card className={cardClassName}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <StatusBadge label={symbol || '—'} tone={symbol ? 'info' : 'neutral'} />
            <StatusBadge label={titleCase(eventType)} tone={isCriticalEvent ? 'danger' : 'neutral'} />
            <StatusBadge label={sentiment} tone={SENTIMENT_TONE[sentiment] || 'neutral'} />
            {isHighAlert && (
              <span className="inline-flex items-center rounded-full border border-rose-500 bg-rose-600/90 px-2.5 py-0.5 text-xs font-bold uppercase tracking-wide text-white">
                High Severity
              </span>
            )}
          </div>

          <h3 className="mt-2 text-base font-semibold leading-snug text-white">
            {url ? (
              <a href={url} target="_blank" rel="noreferrer" className="hover:underline">
                {headline}
              </a>
            ) : (
              headline
            )}
          </h3>

          {summary && <p className="mt-1 text-sm text-slate-400">{summary}</p>}

          <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-slate-500">
            <span>{source}</span>
            <span aria-hidden="true">·</span>
            <span title={absolute}>{relative}</span>
          </div>
        </div>

        <div className="flex flex-col items-end gap-1 text-right">
          <div className="text-xs uppercase tracking-wide text-slate-500">Confidence</div>
          <div className={`text-lg font-mono font-semibold ${isHighAlert ? 'text-rose-300' : 'text-slate-200'}`}>
            {formatConfidence(confidence)}
          </div>
          <div className="text-xs text-slate-500">Severity {severity}/5</div>
        </div>
      </div>
    </Card>
  )
}
