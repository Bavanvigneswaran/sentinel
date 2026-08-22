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
