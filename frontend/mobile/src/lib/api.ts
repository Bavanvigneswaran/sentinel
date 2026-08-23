/**
 * The API client. Ported from web/src/lib/api.ts, and deliberately kept
 * line-for-line recognisable against it: the two load-bearing properties
 * CLAUDE.md's Phase 1 invariants call out are properties of the *protocol*,
 * not of the browser, so they apply identically here.
 *
 * 1. The access token is read from the store at call time, not captured, so a
 *    refresh mid-flight is picked up by the retry.
 * 2. Refresh is single-flighted. The backend treats a replayed refresh token as
 *    theft and revokes the whole family, so two concurrent 401s must await the
 *    same request — otherwise the loser logs the user out. On a phone this is
 *    if anything easier to trip than in a browser: a screen mount and an
 *    AppState → active transition can land in the same tick.
 *
 * Two things differ from the web copy, both from src/config.ts: requests go to
 * an absolute origin, and there is no `/api` prefix to strip.
 *
 * The refresh token itself is never touched by this file. It rides in the
 * HttpOnly cookie the backend sets, stored by the platform's own cookie jar
 * (Android: OkHttp → android.webkit.CookieManager) and replayed automatically.
 * JS cannot read it here any more than it can in a browser.
 */

import { API_BASE_URL } from "@/config"
import type { SessionResponse } from "@/types/api"

const REFRESH_PATH = "/auth/refresh"

export class ApiError extends Error {
  readonly status: number
  constructor(status: number, message: string) {
    super(message)
    this.name = "ApiError"
    this.status = status
  }
}

interface FastApiValidationIssue {
  loc?: unknown[]
  msg?: string
  type?: string
}

/**
 * FastAPI returns `detail` as a string for most errors but as an array of
 * objects for 422. Rendering the raw value would print "[object Object]".
 */
async function toApiError(response: Response): Promise<ApiError> {
  let detail: unknown
  try {
    detail = ((await response.json()) as { detail?: unknown } | null)?.detail
  } catch {
    detail = undefined
  }

  if (typeof detail === "string") return new ApiError(response.status, detail)

  if (Array.isArray(detail)) {
    const messages = (detail as FastApiValidationIssue[])
      .map((issue) => issue?.msg)
      .filter((m): m is string => typeof m === "string")
    if (messages.length > 0) return new ApiError(response.status, messages.join(". "))
  }

  return new ApiError(response.status, `Request failed (${response.status})`)
}

/** A network failure (backend unreachable, wrong EXPO_PUBLIC_API_URL, phone
 * offline) surfaces as a TypeError from fetch with a useless message. On a
 * device this is the single most common failure, so it gets its own text. */
export class NetworkError extends Error {
  constructor() {
    super(`Could not reach the backend at ${API_BASE_URL}.`)
    this.name = "NetworkError"
  }
}

async function send(path: string, init: RequestInit): Promise<Response> {
  try {
    return await fetch(`${API_BASE_URL}${path}`, init)
  } catch {
    throw new NetworkError()
  }
}

// --- refresh single-flight ---------------------------------------------------

let refreshInFlight: Promise<SessionResponse | null> | null = null

async function performRefresh(): Promise<SessionResponse | null> {
  // `send` turns an unreachable backend into a thrown NetworkError rather than
  // a null. That distinction matters: null means the server rejected the
  // cookie and the user really is logged out, while a throw means the phone
  // lost signal and the session should be left exactly as it was.
  const response = await send(REFRESH_PATH, { method: "POST", credentials: "include" })
  if (!response.ok) return null
  return (await response.json()) as SessionResponse
}

/**
 * Exchange the HttpOnly refresh cookie for a new session.
 *
 * Every caller shares one in-flight request. This is mandatory, not an
 * optimisation: a second concurrent call would present the same cookie and the
 * backend would correctly read it as reuse and revoke the family.
 */
export function refreshSession(): Promise<SessionResponse | null> {
  refreshInFlight ??= performRefresh().finally(() => {
    refreshInFlight = null
  })
  return refreshInFlight
}

// --- request -----------------------------------------------------------------

// Set by the auth store at module init. A plain function reference rather than
// a direct import, to keep api.ts free of a circular dependency on the store.
interface AuthBridge {
  getAccessToken: () => string | null
  onRefreshed: (session: SessionResponse) => void
  onUnauthenticated: () => void
}

let bridge: AuthBridge | null = null

export function connectAuthBridge(next: AuthBridge): void {
  bridge = next
}

interface RequestOptions extends Omit<RequestInit, "body"> {
  body?: unknown
  /** Skip the Authorization header and the 401-retry (used by login/signup). */
  anonymous?: boolean
}

export async function apiFetch<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { body, anonymous = false, headers, ...rest } = options

  const attempt = (token: string | null): Promise<Response> => {
    const finalHeaders = new Headers(headers)
    if (body !== undefined) finalHeaders.set("Content-Type", "application/json")
    if (token) finalHeaders.set("Authorization", `Bearer ${token}`)

    return send(path, {
      ...rest,
      headers: finalHeaders,
      credentials: "include",
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  }

  let response = await attempt(anonymous ? null : (bridge?.getAccessToken() ?? null))

  // Retry once behind a refresh. Never for the refresh endpoint itself, or a
  // failing refresh would recurse.
  if (response.status === 401 && !anonymous && path !== REFRESH_PATH) {
    const session = await refreshSession()
    if (session) {
      bridge?.onRefreshed(session)
      response = await attempt(session.access_token)
    } else {
      bridge?.onUnauthenticated()
    }
  }

  if (!response.ok) throw await toApiError(response)
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}
