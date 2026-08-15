import { useState } from 'react'

function formatLabel(key) {
  return key.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}

function formatValue(value) {
  if (value === null || value === undefined) return 'pending'
  if (typeof value === 'number') return `+${value}`
  if (typeof value === 'boolean') return value ? 'yes' : 'no'
  return String(value)
}

export default function ReasonBreakdown({ reason }) {
  const [expanded, setExpanded] = useState(false)

  const entries = reason && typeof reason === 'object' ? Object.entries(reason) : []

  return (
    <div className="mt-2">
      <button
        type="button"
        onClick={() => setExpanded((prev) => !prev)}
        className="text-xs font-medium text-sky-400 hover:text-sky-300"
      >
        {expanded ? 'Hide reason breakdown' : 'Show reason breakdown'}
      </button>

      {expanded && (
        <ul className="mt-2 space-y-1 rounded-md border border-slate-800 bg-slate-950 p-3 text-sm">
          {entries.length === 0 && <li className="text-slate-500">No reason data available.</li>}
          {entries.map(([key, value]) => (
            <li key={key} className="flex items-center justify-between gap-4">
              <span className="text-slate-400">{formatLabel(key)}</span>
              <span className="font-mono text-slate-200">{formatValue(value)}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
