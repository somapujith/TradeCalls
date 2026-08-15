// Tiers per frontend.md's Confidence model, mirroring engine.md's scoring bands.
function tierFor(confidence) {
  if (confidence >= 90) return { label: 'A+', tone: 'bg-emerald-900/60 text-emerald-300 border-emerald-700' }
  if (confidence >= 80) return { label: 'A', tone: 'bg-lime-900/60 text-lime-300 border-lime-700' }
  if (confidence >= 70) return { label: 'B', tone: 'bg-amber-900/60 text-amber-300 border-amber-700' }
  if (confidence >= 60) return { label: 'C', tone: 'bg-orange-900/60 text-orange-300 border-orange-700' }
  return { label: '?', tone: 'bg-rose-900/60 text-rose-300 border-rose-700' }
}

export default function ConfidenceBadge({ confidence }) {
  const hasValue = typeof confidence === 'number' && Number.isFinite(confidence)
  const { label, tone } = hasValue ? tierFor(confidence) : { label: 'N/A', tone: 'bg-slate-800 text-slate-300 border-slate-700' }

  return (
    <div className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-semibold ${tone}`}>
      <span className="uppercase tracking-wide">Confidence</span>
      <span>{hasValue ? Math.round(confidence) : '—'}</span>
      <span className="opacity-80">{label}</span>
    </div>
  )
}
