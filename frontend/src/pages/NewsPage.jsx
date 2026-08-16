import { useCallback, useState } from 'react'
import { getNews } from '../api/news.js'
import { usePolling } from '../hooks/usePolling.js'
import NewsCard from '../components/news/NewsCard.jsx'

const POLL_INTERVAL_MS = 120_000 // news doesn't need call-cache-tight freshness; 2min keeps the tab current

const SEVERITY_FILTERS = [
  { label: 'All', minSeverity: undefined },
  { label: 'Severity ≥ 3', minSeverity: 3 },
  { label: 'Severity ≥ 4', minSeverity: 4 },
]

export default function NewsPage() {
  const [minSeverity, setMinSeverity] = useState(undefined)

  const fetchNews = useCallback(() => getNews({ minSeverity }), [minSeverity])
  const { data: articles, error, loading, refetch } = usePolling(fetchNews, POLL_INTERVAL_MS)

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-xl font-semibold text-white">News</h1>

        <div className="flex items-center gap-2">
          <div className="flex overflow-hidden rounded-md border border-slate-700">
            {SEVERITY_FILTERS.map((filter) => {
              const isActive = filter.minSeverity === minSeverity
              return (
                <button
                  key={filter.label}
                  type="button"
                  onClick={() => setMinSeverity(filter.minSeverity)}
                  className={`px-3 py-1.5 text-sm font-medium transition-colors ${
                    isActive ? 'bg-slate-800 text-white' : 'bg-slate-900 text-slate-400 hover:bg-slate-800 hover:text-white'
                  }`}
                >
                  {filter.label}
                </button>
              )
            })}
          </div>

          <button
            type="button"
            onClick={refetch}
            className="rounded-md border border-slate-700 bg-slate-900 px-3 py-1.5 text-sm text-slate-300 hover:bg-slate-800"
          >
            Refresh
          </button>
        </div>
      </div>

      {loading && !articles && <p className="text-slate-400">Loading news…</p>}

      {error && (
        <p className="rounded-md border border-rose-800 bg-rose-950/50 p-3 text-sm text-rose-300">
          Failed to load news: {error.message}
        </p>
      )}

      {!loading && !error && Array.isArray(articles) && articles.length === 0 && (
        <p className="text-slate-400">No news matches this filter right now.</p>
      )}

      {Array.isArray(articles) && articles.length > 0 && (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          {articles.map((article) => (
            <NewsCard key={article.id} article={article} />
          ))}
        </div>
      )}
    </div>
  )
}
