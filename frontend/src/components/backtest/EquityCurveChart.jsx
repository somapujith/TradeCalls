import { useEffect, useRef } from 'react'
import { createChart, ColorType } from 'lightweight-charts'
import Card from '../common/Card.jsx'

// Simplifying assumption: each trade contributes (mfe if target_hit, -mae if sl_hit, else 0)
// as a percent-return proxy, cumulatively summed in entry_date order. This is NOT a real
// portfolio simulation (no position sizing, compounding, or overlapping-trade handling) —
// it's a directional "is this strategy net positive over time" curve only.
function buildEquitySeries(trades) {
  if (!Array.isArray(trades)) return []

  const closed = trades
    .filter((t) => t.entry_date && t.outcome)
    .slice()
    .sort((a, b) => (a.entry_date < b.entry_date ? -1 : a.entry_date > b.entry_date ? 1 : 0))

  let cumulative = 0
  const points = []
  const seenDates = new Set()

  for (const trade of closed) {
    const { outcome } = trade
    let returnProxy = 0
    if (outcome.target_hit) returnProxy = typeof outcome.mfe === 'number' ? outcome.mfe : 0
    else if (outcome.sl_hit) returnProxy = typeof outcome.mae === 'number' ? -Math.abs(outcome.mae) : 0

    cumulative += returnProxy

    // Lightweight Charts requires strictly increasing unique time values per series.
    let time = trade.entry_date
    while (seenDates.has(time)) {
      time = new Date(new Date(time).getTime() + 86_400_000).toISOString().slice(0, 10)
    }
    seenDates.add(time)

    points.push({ time, value: Number(cumulative.toFixed(4)) })
  }

  return points
}

export default function EquityCurveChart({ trades }) {
  const containerRef = useRef(null)
  const chartRef = useRef(null)
  const seriesRef = useRef(null)

  useEffect(() => {
    if (!containerRef.current) return undefined

    const chart = createChart(containerRef.current, {
      layout: {
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor: '#cbd5e1',
      },
      grid: {
        vertLines: { color: '#1e293b' },
        horzLines: { color: '#1e293b' },
      },
      width: containerRef.current.clientWidth,
      height: 300,
    })

    const series = chart.addLineSeries({ color: '#38bdf8', lineWidth: 2 })

    chartRef.current = chart
    seriesRef.current = series

    const handleResize = () => {
      if (containerRef.current) {
        chart.resize(containerRef.current.clientWidth, 300)
      }
    }
    window.addEventListener('resize', handleResize)

    return () => {
      window.removeEventListener('resize', handleResize)
      chart.remove()
      chartRef.current = null
      seriesRef.current = null
    }
  }, [])

  useEffect(() => {
    if (!seriesRef.current) return
    seriesRef.current.setData(buildEquitySeries(trades))
    chartRef.current?.timeScale().fitContent()
  }, [trades])

  const hasData = Array.isArray(trades) && trades.some((t) => t.outcome)

  return (
    <Card>
      <h2 className="mb-1 text-sm font-semibold uppercase tracking-wide text-slate-400">Equity Curve</h2>
      <p className="mb-3 text-xs text-slate-500">
        Cumulative return proxy from closed trade outcomes, sorted by entry date. Not a real portfolio
        simulation (no position sizing or compounding) — directional only.
      </p>
      {!hasData && <p className="text-slate-400">No closed trades to chart yet.</p>}
      <div ref={containerRef} className={hasData ? '' : 'hidden'} />
    </Card>
  )
}
