import { useCallback, useEffect, useRef, useState } from 'react'

/**
 * Generic interval-based refetch hook. `fetchFn` must be stable across renders
 * (wrap in useCallback at the call site) or this will re-arm the interval every render.
 */
export function usePolling(fetchFn, intervalMs) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)
  const isMountedRef = useRef(true)

  const load = useCallback(async () => {
    try {
      const result = await fetchFn()
      if (!isMountedRef.current) return
      setData(result)
      setError(null)
    } catch (err) {
      if (!isMountedRef.current) return
      setError(err)
    } finally {
      if (isMountedRef.current) setLoading(false)
    }
  }, [fetchFn])

  useEffect(() => {
    isMountedRef.current = true
    load()

    if (!intervalMs) return undefined

    const id = setInterval(load, intervalMs)
    return () => {
      isMountedRef.current = false
      clearInterval(id)
    }
  }, [load, intervalMs])

  return { data, error, loading, refetch: load }
}
