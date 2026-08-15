import Card from '../common/Card.jsx'

function formatPercent(value) {
  return typeof value === 'number' && Number.isFinite(value) ? `${(value * 100).toFixed(1)}%` : '—'
}

export default function ScoreBucketTable({ breakdown }) {
  const buckets = breakdown && typeof breakdown === 'object' ? Object.entries(breakdown) : []

  if (buckets.length === 0) {
    return (
      <Card>
        <p className="text-slate-400">No score bucket breakdown available.</p>
      </Card>
    )
  }

  return (
    <Card>
      <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-slate-400">Score Buckets</h2>
      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead>
            <tr className="border-b border-slate-800 text-xs uppercase tracking-wide text-slate-500">
              <th className="py-2 pr-4">Bucket</th>
              <th className="py-2 pr-4">Trades</th>
              <th className="py-2 pr-4">Win Rate</th>
              <th className="py-2 pr-4">Profit Factor</th>
            </tr>
          </thead>
          <tbody>
            {buckets.map(([bucket, stats]) => (
              <tr key={bucket} className="border-b border-slate-900">
                <td className="py-2 pr-4 font-mono text-slate-200">{bucket}</td>
                <td className="py-2 pr-4 text-slate-300">{stats?.total_trades ?? stats?.trades ?? '—'}</td>
                <td className="py-2 pr-4 text-slate-300">{formatPercent(stats?.win_rate)}</td>
                <td className="py-2 pr-4 text-slate-300">
                  {typeof stats?.profit_factor === 'number' ? stats.profit_factor.toFixed(2) : '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  )
}
