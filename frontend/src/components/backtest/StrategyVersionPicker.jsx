function formatPercent(value) {
  return typeof value === 'number' && Number.isFinite(value) ? `${(value * 100).toFixed(1)}%` : '—'
}

function formatNumber(value) {
  return typeof value === 'number' && Number.isFinite(value) ? value.toFixed(2) : '—'
}

export default function StrategyVersionPicker({ versions, selected, onSelect }) {
  if (!Array.isArray(versions) || versions.length === 0) {
    return <p className="text-slate-400">No backtest runs available.</p>
  }

  return (
    <div className="mb-6">
      <label htmlFor="strategy-version" className="mb-1 block text-xs uppercase tracking-wide text-slate-500">
        Strategy Version
      </label>
      <select
        id="strategy-version"
        value={selected || ''}
        onChange={(e) => onSelect(e.target.value)}
        className="mb-3 w-full max-w-sm rounded-md border border-slate-700 bg-slate-900 px-3 py-2 text-sm text-slate-200"
      >
        {versions.map((v) => (
          <option key={v.strategy_version} value={v.strategy_version}>
            {v.strategy_version} — {v.run_date}
          </option>
        ))}
      </select>

      <div className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
        {versions
          .filter((v) => v.strategy_version === selected)
          .map((v) => (
            <div key={v.strategy_version} className="contents">
              <div>
                <div className="text-xs uppercase tracking-wide text-slate-500">Run Date</div>
                <div className="text-slate-200">{v.run_date}</div>
              </div>
              <div>
                <div className="text-xs uppercase tracking-wide text-slate-500">Total Trades</div>
                <div className="text-slate-200">{v.total_trades ?? '—'}</div>
              </div>
              <div>
                <div className="text-xs uppercase tracking-wide text-slate-500">Win Rate</div>
                <div className="text-slate-200">{formatPercent(v.win_rate)}</div>
              </div>
              <div>
                <div className="text-xs uppercase tracking-wide text-slate-500">Profit Factor</div>
                <div className="text-slate-200">{formatNumber(v.profit_factor)}</div>
              </div>
            </div>
          ))}
      </div>
    </div>
  )
}
