// Copied from web/src/types/ — the backend's Pydantic schemas are the single
// source of truth and both clients hand-write against them (see CLAUDE.md's
// Conventions). Kept byte-identical to the web copy so a schema change is one
// diff applied twice, not two divergent guesses.
/** Wire types for notification settings and web push. Mirrors
 * app/schemas/notifications.py. */

export type Sensitivity = "low" | "medium" | "high"

export interface NotificationSettings {
  email_enabled: boolean
  /** null falls back to the account's own email at send time. */
  email_address: string | null
  web_push_enabled: boolean
  /** Cutoff the evaluator uses for every anomaly rule on this account — see
   * lib/anomaly.ts. */
  anomaly_sensitivity: Sensitivity
  updated_at: string
}

export interface NotificationSettingsUpdate {
  email_enabled?: boolean
  /** Explicit null clears the override back to the account email. */
  email_address?: string | null
  web_push_enabled?: boolean
  anomaly_sensitivity?: Sensitivity
}

export interface VapidPublicKey {
  /** null when the server has no VAPID keypair configured. */
  public_key: string | null
}

// --- Phase 10a: FCM ----------------------------------------------------------
// Mirrors app/schemas/notifications.py's FcmRegisterRequest. Android-only; the
// web app has no counterpart, which is why this block is not in web/src.

export interface FcmRegisterRequest {
  /** The FCM registration token from expo-notifications'
   * getDevicePushTokenAsync(). Opaque, device-scoped, and rotatable by FCM. */
  token: string
  /** Free-form label shown nowhere yet; stored so a user can eventually tell
   * two phones apart in settings. */
  device_label?: string | null
}
