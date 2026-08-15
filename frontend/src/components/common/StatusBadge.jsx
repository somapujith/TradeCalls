const TONE_CLASSES = {
  neutral: 'bg-slate-800 text-slate-200 border-slate-700',
  success: 'bg-emerald-900/60 text-emerald-300 border-emerald-700',
  warning: 'bg-amber-900/60 text-amber-300 border-amber-700',
  danger: 'bg-rose-900/60 text-rose-300 border-rose-700',
  info: 'bg-sky-900/60 text-sky-300 border-sky-700',
}

export default function StatusBadge({ label, tone = 'neutral', className = '' }) {
  const toneClasses = TONE_CLASSES[tone] || TONE_CLASSES.neutral
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-medium ${toneClasses} ${className}`}
    >
      {label}
    </span>
  )
}
