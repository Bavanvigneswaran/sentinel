/**
 * The poll-and-keep-the-last-good-payload pattern the fleet, devices and
 * alerts screens all share.
 *
 * The web app repeats this useEffect on three pages; it is factored out here
 * because a phone adds two behaviours to every one of them:
 *
 * - **Polling stops when the app is backgrounded.** A dashboard refreshing
 *   every 30s in someone's pocket is battery spend that buys nothing, and the
 *   OS would throttle it unpredictably anyway. Foregrounding refetches
 *   immediately, so the first thing seen on resume is current rather than
 *   however stale the last poll left it.
 * - **Pull to refresh**, which is the gesture people reach for on a phone
 *   before they look for a button.
 *
 * A failed poll keeps the previous payload on screen behind an error note. A
 * transient failure must not blank a screen someone is reading — and the note
 * says the numbers are the last successful reading, which is the honest
 * framing rather than pretending they are current.
 */

import { useCallback, useEffect, useRef, useState } from "react"
import { AppState } from "react-native"

import { apiFetch } from "@/lib/api"

interface PolledResource<T> {
  data: T | null
  error: string | null
  /** True until the first attempt settles — "loading" as distinct from
   * "loaded and empty". */
  loading: boolean
  refreshing: boolean
  /** Pull-to-refresh: shows the spinner, because the user asked for it. */
  refresh: () => void
  /** Refetch without the spinner — for a refresh the user did not gesture
   * for, such as a screen regaining focus. Same request, no chrome. */
  reload: () => void
}

export function usePolledResource<T>(
  path: string,
  intervalMs: number,
  errorMessage: string,
): PolledResource<T> {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const cancelled = useRef(false)

  const load = useCallback(
    async (viaGesture: boolean) => {
      if (viaGesture) setRefreshing(true)
      try {
        const next = await apiFetch<T>(path)
        if (cancelled.current) return
        setData(next)
        setError(null)
      } catch {
        if (!cancelled.current) setError(errorMessage)
      } finally {
        if (!cancelled.current) {
          setLoading(false)
          if (viaGesture) setRefreshing(false)
        }
      }
    },
    [path, errorMessage],
  )

  useEffect(() => {
    cancelled.current = false
    let timer: ReturnType<typeof setInterval> | undefined

    const start = () => {
      if (timer !== undefined) return
      timer = setInterval(() => void load(false), intervalMs)
    }
    const stop = () => {
      if (timer === undefined) return
      clearInterval(timer)
      timer = undefined
    }

    // The first fetch. `load` is async, so every setState inside it lands
    // after an await, on the HTTP response — the network IS the external
    // system this effect exists to synchronise with.
    // oxlint-disable-next-line react/set-state-in-effect
    void load(false)
    start()

    const subscription = AppState.addEventListener("change", (state) => {
      if (state === "active") {
        void load(false)
        start()
      } else {
        stop()
      }
    })

    return () => {
      cancelled.current = true
      stop()
      subscription.remove()
    }
  }, [load, intervalMs])

  const refresh = useCallback(() => {
    void load(true)
  }, [load])

  const reload = useCallback(() => {
    void load(false)
  }, [load])

  return { data, error, loading, refreshing, refresh, reload }
}
