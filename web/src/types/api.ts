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
