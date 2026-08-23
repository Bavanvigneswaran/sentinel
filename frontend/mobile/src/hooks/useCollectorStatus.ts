/**
 * The collector's status, as React state.
 *
 * Two sources on purpose. The event stream is the live one — the Kotlin
 * service pushes every transition it makes — but it only exists while a JS
 * runtime is listening, which is a small fraction of the service's life. So the
 * hook also reads `status()` on mount and whenever the app comes back to the
 * foreground, because after a background stretch the service has been running
 * without anyone to tell.
 *
 * This is the same reflex as Phase 10a's `AppState` handling: the app being
 * suspended is a normal state, not an error, and what it needs on resume is a
 * fresh read rather than a replay of what it missed.
 */

import { useCallback, useEffect, useState } from "react"
import { AppState } from "react-native"

import {
  addStatusListener,
  isSupported,
  status as readStatus,
  UNSUPPORTED_STATUS,
  type CollectorStatus,
} from "sentinel-collector"

export function useCollectorStatus(): {
  status: CollectorStatus
  refresh: () => void
  supported: boolean
} {
  const [status, setStatus] = useState<CollectorStatus>(() =>
    isSupported ? readStatus() : UNSUPPORTED_STATUS,
  )

  const refresh = useCallback(() => {
    if (isSupported) setStatus(readStatus())
  }, [])

  useEffect(() => {
    if (!isSupported) return
    const subscription = addStatusListener(setStatus)
    const appState = AppState.addEventListener("change", (next) => {
      // Coming back from the background: the service has been sampling and
      // pushing with nobody listening for events, so re-read rather than
      // trusting whatever was on screen when we were suspended.
      if (next === "active") refresh()
    })
    // Deliberate, and not redundant with the useState initialiser: that ran
    // during render, and the service can have changed state in the window
    // between it and this subscription. Re-reading once here closes that gap;
    // dropping it would occasionally leave the screen showing a status that was
    // already stale before the first paint.
    // oxlint-disable-next-line react/set-state-in-effect
    refresh()
    return () => {
      subscription?.remove()
      appState.remove()
    }
  }, [refresh])

  return { status, refresh, supported: isSupported }
}
