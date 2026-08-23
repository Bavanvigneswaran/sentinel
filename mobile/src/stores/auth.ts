/**
 * Auth state. Ported from web/src/stores/auth.ts.
 *
 * The access token lives ONLY in this module's memory — never AsyncStorage,
 * never SecureStore, never on disk in any form. That is the same Phase 1
 * invariant the web client follows, and it is not weaker on a phone: an app
 * restart loses it, and the only way back is to exchange the HttpOnly refresh
 * cookie the platform cookie jar replayed for us. That round trip is what makes
 * "kill the app and stay logged in" work, exactly as it makes "hard-refresh and
 * stay logged in" work in a browser.
 *
 * Deliberately NOT persisted with zustand/middleware/persist: persisting this
 * store would write the access token to disk, which is the one thing the
 * invariant forbids.
 */

import { AppState } from "react-native"
import { create } from "zustand"

import { NetworkError, apiFetch, connectAuthBridge, refreshSession } from "@/lib/api"
import type { LoginRequest, SessionResponse, SignupRequest, User } from "@/types/api"

export type AuthStatus = "idle" | "bootstrapping" | "authenticated" | "anonymous"

/** Refresh this many seconds before the access token actually expires. */
const REFRESH_SKEW_SECONDS = 60

interface AuthState {
  status: AuthStatus
  user: User | null
  accessToken: string | null
  /** Set when bootstrap could not reach the backend. Not the same as being
   * logged out: the login screen shows it as a connectivity problem so nobody
   * retypes a password at an unreachable server. */
  bootstrapError: string | null

  login: (credentials: LoginRequest) => Promise<void>
  signup: (payload: SignupRequest) => Promise<void>
  logout: () => Promise<void>
}

let refreshTimer: ReturnType<typeof setTimeout> | undefined
let expiresAtMs = 0

function clearRefreshTimer(): void {
  if (refreshTimer !== undefined) {
    clearTimeout(refreshTimer)
    refreshTimer = undefined
  }
}

export const useAuth = create<AuthState>(() => ({
  status: "idle",
  user: null,
  accessToken: null,
  bootstrapError: null,

  login: async (credentials) => {
    const session = await apiFetch<SessionResponse>("/auth/login", {
      method: "POST",
      body: credentials,
      anonymous: true,
    })
    applySession(session)
  },

  signup: async (payload) => {
    const session = await apiFetch<SessionResponse>("/auth/signup", {
      method: "POST",
      body: payload,
      anonymous: true,
    })
    applySession(session)
  },

  logout: async () => {
    // Unregister the phone's FCM token *before* dropping the session, while
    // there is still an access token to authenticate the call. Two reasons,
    // both real: this account should stop receiving alerts on a phone it no
    // longer owns, and the token row is uniquely keyed on the device, so
    // leaving it behind makes the next account's registration a 409 (see the
    // register route in app/api/routes/notifications.py).
    //
    // Imported lazily to keep the auth store free of a dependency on
    // expo-notifications, which pulls native modules in at import time.
    try {
      const { disablePush } = await import("@/lib/push")
      await disablePush()
    } catch {
      // Best-effort. A phone with no token, no permission, or no network still
      // gets to sign out.
    }

    try {
      await apiFetch<void>("/auth/logout", { method: "POST" })
    } catch {
      // Logout is best-effort on the client: the cookie is cleared server-side
      // and we drop local state regardless.
    }
    clearSession()
    useAuth.setState({ status: "anonymous" })
  },
}))

function applySession(session: SessionResponse): void {
  useAuth.setState({
    status: "authenticated",
    user: session.user,
    accessToken: session.access_token,
    bootstrapError: null,
  })
  scheduleRefresh(session.expires_in)
}

function clearSession(): void {
  clearRefreshTimer()
  expiresAtMs = 0
  useAuth.setState({ status: "anonymous", user: null, accessToken: null })
}

/**
 * Refresh shortly before expiry so a long-lived screen (Live Monitoring) never
 * sits on a dead token.
 */
function scheduleRefresh(expiresInSeconds: number): void {
  clearRefreshTimer()
  expiresAtMs = Date.now() + expiresInSeconds * 1000

  const delayMs = Math.max(expiresInSeconds - REFRESH_SKEW_SECONDS, 1) * 1000
  refreshTimer = setTimeout(() => {
    void renew()
  }, delayMs)
}

async function renew(): Promise<boolean> {
  try {
    const session = await refreshSession()
    if (session) {
      applySession(session)
      return true
    }
  } catch (error) {
    // Unreachable backend. Keep the session; the next foreground or the next
    // 401 retries. Logging out here would sign the user out of a perfectly
    // valid session every time they walked through a tunnel.
    if (error instanceof NetworkError) return false
    throw error
  }
  clearSession()
  return false
}

// --- boot --------------------------------------------------------------------

let bootstrapPromise: Promise<void> | null = null

/**
 * Restore the session on app launch.
 *
 * Deliberately a module-scope single-flight promise called from index.ts, NOT a
 * useEffect: React StrictMode double-invokes effects in development, which
 * would fire two concurrent /auth/refresh calls with the same cookie — and the
 * backend would correctly read the second as token reuse and revoke the entire
 * family, logging the user out on every launch. Same reasoning, same shape, as
 * the web client's bootstrapAuth().
 */
export function bootstrapAuth(): Promise<void> {
  bootstrapPromise ??= (async () => {
    useAuth.setState({ status: "bootstrapping", bootstrapError: null })
    try {
      const session = await refreshSession()
      if (session) {
        applySession(session)
        return
      }
      useAuth.setState({ status: "anonymous", user: null, accessToken: null })
    } catch (error) {
      useAuth.setState({
        status: "anonymous",
        user: null,
        accessToken: null,
        bootstrapError: error instanceof Error ? error.message : "Could not reach the backend.",
      })
    }
  })()
  return bootstrapPromise
}

connectAuthBridge({
  getAccessToken: () => useAuth.getState().accessToken,
  onRefreshed: applySession,
  onUnauthenticated: clearSession,
})

// A backgrounded app's timers are throttled or suspended outright, far more
// aggressively than a background browser tab's, so an app resumed after a long
// sleep can easily hold an already-expired token. Re-check on the way back —
// the AppState equivalent of the web client's visibilitychange listener.
AppState.addEventListener("change", (state) => {
  if (state !== "active") return
  if (useAuth.getState().status !== "authenticated") return
  if (Date.now() < expiresAtMs - REFRESH_SKEW_SECONDS * 1000) return
  void renew()
})
