/**
 * Auth state.
 *
 * The access token lives ONLY in this module's memory — never localStorage or
 * sessionStorage (XSS-exfiltratable), never a cookie (would need CSRF
 * defences). A hard refresh therefore loses it, and the only way back is to
 * exchange the HttpOnly refresh cookie. That round trip is what makes
 * "hard-refresh and stay logged in" work.
 */

import { create } from "zustand"

import {
  apiFetch,
  connectAuthBridge,
  discardRefreshSingleFlight,
  refreshSession,
} from "@/lib/api"
import type { LoginRequest, SessionResponse, SignupRequest, User } from "@/types/api"

/** A string-literal union, not a TS enum — erasableSyntaxOnly forbids enums. */
export type AuthStatus = "idle" | "bootstrapping" | "authenticated" | "anonymous"

/** Refresh this many seconds before the access token actually expires. */
const REFRESH_SKEW_SECONDS = 60

interface AuthState {
  status: AuthStatus
  user: User | null
  accessToken: string | null

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

export const useAuth = create<AuthState>((set) => ({
  status: "idle",
  user: null,
  accessToken: null,

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
    try {
      await apiFetch<void>("/auth/logout", { method: "POST" })
    } catch {
      // Logout is best-effort on the client: the cookie is cleared server-side
      // and we drop local state regardless.
    }
    clearSession()
    set({ status: "anonymous" })
  },
}))

function applySession(session: SessionResponse): void {
  useAuth.setState({
    status: "authenticated",
    user: session.user,
    accessToken: session.access_token,
  })
  scheduleRefresh(session.expires_in)
}

function clearSession(): void {
  clearRefreshTimer()
  expiresAtMs = 0
  useAuth.setState({ status: "anonymous", user: null, accessToken: null })
}

/**
 * Refresh shortly before expiry so long-lived pages (Phase 3's live monitoring)
 * never sit on a dead token.
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
  const session = await refreshSession()
  if (session) {
    applySession(session)
    return true
  }
  clearSession()
  return false
}

// --- boot --------------------------------------------------------------------

let bootstrapPromise: Promise<void> | null = null

/**
 * Restore the session on page load.
 *
 * Deliberately a module-scope single-flight promise called from main.tsx, NOT a
 * useEffect: React 19 StrictMode double-invokes effects in development, which
 * would fire two concurrent /auth/refresh calls with the same cookie — and the
 * backend would correctly read the second as token reuse and revoke the entire
 * family, logging the user out on every page load.
 */
export function bootstrapAuth(): Promise<void> {
  bootstrapPromise ??= (async () => {
    useAuth.setState({ status: "bootstrapping" })
    const session = await refreshSession()
    if (session) {
      applySession(session)
    } else {
      useAuth.setState({ status: "anonymous", user: null, accessToken: null })
    }
  })()
  return bootstrapPromise
}

connectAuthBridge({
  getAccessToken: () => useAuth.getState().accessToken,
  onRefreshed: applySession,
  onUnauthenticated: clearSession,
})

/**
 * Revalidate a page restored from the back/forward cache.
 *
 * A bfcache restore replays the document instead of re-executing it: main.tsx
 * does not run, bootstrapAuth() is never called again, and this module's memory
 * — status, user and the access token — comes back exactly as it was when the
 * page was frozen. So a page cached while signed in still renders as signed in,
 * however the session ended in the meantime.
 *
 * Signing out is the case that matters, because the sign-out happens in a
 * *different* document. Navigate to a second page, sign out there, press Back,
 * and the first page is restored whole: the refresh cookie is gone, but the
 * previous account's screen is on display with a still-valid access token
 * behind it, and nothing in the restored page knows any of that has happened.
 * `visibilitychange` below does not cover it — that fires for a tab coming
 * forward, which a restore is not.
 *
 * The answer is to treat the restore as what it should have been, a load:
 * bootstrapAuth() sets `bootstrapping` synchronously, so the route guards
 * replace the stale content with the loader before anything is painted, and the
 * refresh exchange decides what happens next — signed in again, or bounced to
 * /login. Its own single-flight promise is reset first because the memoised one
 * belongs to the page's earlier life and would resolve instantly with an answer
 * that is exactly what is in doubt.
 */
if (typeof window !== "undefined") {
  window.addEventListener("pageshow", (event) => {
    if (!event.persisted) return
    discardRefreshSingleFlight()
    bootstrapPromise = null
    void bootstrapAuth()
  })
}

// Timers are throttled or suspended in background tabs, so a tab restored after
// a long sleep can hold an already-expired token. Re-check on the way back.
if (typeof document !== "undefined") {
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState !== "visible") return
    if (useAuth.getState().status !== "authenticated") return
    if (Date.now() < expiresAtMs - REFRESH_SKEW_SECONDS * 1000) return
    void renew()
  })
}
