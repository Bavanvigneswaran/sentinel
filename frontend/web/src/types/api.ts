/**
 * Wire types for the Sentinel API.
 *
 * Hand-written for Phase 1. These mirror app/schemas/auth.py, which is the
 * source of truth; a later phase generates this file from the OpenAPI schema.
 */

export interface User {
  id: string
  email: string
  display_name: string | null
  created_at: string
  last_login_at: string | null
}

export interface SessionResponse {
  access_token: string
  token_type: "bearer"
  /** Seconds until the access token expires; drives the proactive refresh. */
  expires_in: number
  user: User
}

export interface SignupRequest {
  email: string
  password: string
  display_name?: string | null
}

export interface LoginRequest {
  email: string
  password: string
}

/** Mirrors app/schemas/devices.py:DeviceOut. `status` is server-derived —
 * see DEVICE_STALE_AFTER_SECONDS in the backend — never trust a stored
 * "online" without checking `last_seen_at` yourself if you bypass this type. */
export interface Device {
  id: string
  name: string
  hostname: string | null
  os: string | null
  os_version: string | null
  kernel_version: string | null
  arch: string | null
  cpu_cores: number | null
  total_memory_bytes: number | null
  agent_version: string | null
  platform: "desktop" | "android"
  status: "pending" | "online" | "offline"
  last_seen_at: string | null
  enrolled_at: string | null
  created_at: string
  /** Set on a device the user removed. Only ever populated by
   * `GET /devices?include_removed=true`, which the history surfaces use so a
   * removed device can still be named rather than shown as a bare UUID. */
  deleted_at?: string | null
}

/** Mirrors app/schemas/devices.py:EnrollmentCodeOut. `code` is the plaintext,
 * returned exactly once — only its sha256 is stored server-side, so a code the
 * user did not copy is gone and a new one has to be minted. */
export interface EnrollmentCode {
  id: string
  code: string
  expires_at: string
}
