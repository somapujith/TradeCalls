import { useCallback, useEffect, useState } from 'react'
import { getBacktests, getBacktestMetrics, getBacktestTrades } from '../api/backtests.js'
import { usePolling } from '../hooks/usePolling.js'
import StrategyVersionPicker from '../components/backtest/StrategyVersionPicker.jsx'
import ScoreBucketTable from '../components/backtest/ScoreBucketTable.jsx'
import TradeList from '../components/backtest/TradeList.jsx'
import EquityCurveChart from '../components/backtest/EquityCurveChart.jsx'

// No interval passed to usePolling — backtest results are static once a run completes
// (per frontend.md), so this page only refetches on mount or manual refresh.
export default function BacktestResultsPage() {
  const fetchBacktests = useCallback(() => getBacktests(), [])
  const { data: versions, error: versionsError, loading: versionsLoading, refetch: refetchVersions } =
    usePolling(fetchBacktests, null)

  const [selectedVersion, setSelectedVersion] = useState(null)
  const [metrics, setMetrics] = useState(null)
  const [trades, setTrades] = useState(null)
  const [detailError, setDetailError] = useState(null)
  const [detailLoading, setDetailLoading] = useState(false)

  useEffect(() => {
    if (Array.isArray(versions) && versions.length > 0 && !selectedVersion) {
      setSelectedVersion(versions[0].strategy_version)
    }
  }, [versions, selectedVersion])

  const loadDetail = useCallback(async (version) => {
    if (!version) return
    setDetailLoading(true)
    setDetailError(null)
    try {
      const [metricsResult, tradesResult] = await Promise.all([
        getBacktestMetrics(version),
        getBacktestTrades(version),
      ])
      setMetrics(metricsResult)
      setTrades(tradesResult)
    } catch (err) {
      setDetailError(err)
    } finally {
      setDetailLoading(false)
    }
  }, [])

  useEffect(() => {
    loadDetail(selectedVersion)
  }, [selectedVersion, loadDetail])

  return (
    <div>
      <div className="mb-4 flex items-center justify-between">
        <h1 className="text-xl font-semibold text-white">Backtest Results</h1>
        <button
          type="button"
          onClick={() => {
            refetchVersions()
            loadDetail(selectedVersion)
          }}
          className="rounded-md border border-slate-700 bg-slate-900 px-3 py-1.5 text-sm text-slate-300 hover:bg-slate-800"
        >
          Refresh
        </button>
      </div>

      {versionsLoading && !versions && <p className="text-slate-400">Loading backtest runs…</p>}

      {versionsError && (
        <p className="rounded-md border border-rose-800 bg-rose-950/50 p-3 text-sm text-rose-300">
          Failed to load backtest runs: {versionsError.message}
        </p>
      )}

      {Array.isArray(versions) && versions.length > 0 && (
        <StrategyVersionPicker versions={versions} selected={selectedVersion} onSelect={setSelectedVersion} />
      )}

      {detailLoading && <p className="text-slate-400">Loading run details…</p>}

      {detailError && (
        <p className="rounded-md border border-rose-800 bg-rose-950/50 p-3 text-sm text-rose-300">
          Failed to load run details: {detailError.message}
        </p>
      )}

      {!detailLoading && !detailError && metrics && (
        <div className="space-y-4">
          <ScoreBucketTable breakdown={metrics.breakdown_by_score_bucket} />
          <EquityCurveChart trades={trades} />
          <TradeList trades={trades} />
        </div>
      )}
    </div>
  )
}
