/**
 * The API client.
 *
 * Two things here are load-bearing rather than conveniences:
 *
 * 1. The access token is read from the store at call time, not captured, so a
 *    refresh mid-flight is picked up by the retry.
 * 2. Refresh is single-flighted. The backend treats a replayed refresh token as
 *    theft and revokes the whole family, so two concurrent 401s must await the
 *    same request — otherwise the loser logs the user out.
 */

import type { SessionResponse } from "@/types/api"

/** The Vite proxy strips this prefix; FastAPI serves /auth/login. */
const API_PREFIX = "/api"
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
    detail = (await response.json())?.detail
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

// --- refresh single-flight ---------------------------------------------------

let refreshInFlight: Promise<SessionResponse | null> | null = null

async function performRefresh(): Promise<SessionResponse | null> {
  const response = await fetch(`${API_PREFIX}${REFRESH_PATH}`, {
    method: "POST",
    credentials: "include",
  })
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

/**
 * Drop a shared refresh that can never settle.
 *
 * Only for a page restored from the back/forward cache. That document was
 * frozen, not re-executed, so this module's memory comes back as it was — and
 * a promise parked here belongs to a fetch that was cancelled while the page
 * was away. Every caller shares this slot, so leaving it would make the
 * restored page await a request nobody is going to answer.
 *
 * Not a general-purpose reset: clearing it while a refresh really is in flight
 * re-creates the concurrent-refresh case the single-flight exists to prevent,
 * which the backend reads as token theft and answers by revoking the family.
 */
export function discardRefreshSingleFlight(): void {
  refreshInFlight = null
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

  const send = (token: string | null): Promise<Response> => {
    const finalHeaders = new Headers(headers)
    if (body !== undefined) finalHeaders.set("Content-Type", "application/json")
    if (token) finalHeaders.set("Authorization", `Bearer ${token}`)

    return fetch(`${API_PREFIX}${path}`, {
      ...rest,
      headers: finalHeaders,
      credentials: "include",
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  }

  let response = await send(anonymous ? null : (bridge?.getAccessToken() ?? null))

  // Retry once behind a refresh. Never for the refresh endpoint itself, or a
  // failing refresh would recurse.
  if (response.status === 401 && !anonymous && path !== REFRESH_PATH) {
    const session = await refreshSession()
    if (session) {
      bridge?.onRefreshed(session)
      response = await send(session.access_token)
    } else {
      bridge?.onUnauthenticated()
    }
  }

  if (!response.ok) throw await toApiError(response)
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

const FILENAME_RE = /filename="?([^";]+)"?/

/**
 * GET a binary response (a report PDF/CSV) rather than JSON. Shares
 * apiFetch's auth-header-plus-401-retry logic rather than duplicating it,
 * since a download is otherwise an ordinary authenticated GET.
 */
export async function apiDownload(
  path: string,
  params?: Record<string, string>,
): Promise<{ blob: Blob; filename: string }> {
  const query = params ? `?${new URLSearchParams(params).toString()}` : ""

  const send = (token: string | null): Promise<Response> => {
    const headers = new Headers()
    if (token) headers.set("Authorization", `Bearer ${token}`)
    return fetch(`${API_PREFIX}${path}${query}`, { headers, credentials: "include" })
  }

  let response = await send(bridge?.getAccessToken() ?? null)
  if (response.status === 401) {
    const session = await refreshSession()
    if (session) {
      bridge?.onRefreshed(session)
      response = await send(session.access_token)
    } else {
      bridge?.onUnauthenticated()
    }
  }

  if (!response.ok) throw await toApiError(response)

  const disposition = response.headers.get("Content-Disposition") ?? ""
  const filename = FILENAME_RE.exec(disposition)?.[1] ?? "report"
  return { blob: await response.blob(), filename }
}
