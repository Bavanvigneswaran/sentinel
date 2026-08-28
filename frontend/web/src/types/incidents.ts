/**
 * Wire types for incidents: a device-level grouping of correlated alerts,
 * plus the cached summary/root-cause. Mirrors app/schemas/incidents.py.
 */

import type { AlertEvent } from "@/types/alerts"

export type IncidentStatus = "open" | "resolved"

export interface Incident {
  id: string
  device_id: string
  status: IncidentStatus
  opened_at: string
  closed_at: string | null
  /** Haiku's short plain-English summary. Null until the insights worker (or
   * a manual regenerate) has produced one. */
  summary_text: string | null
  summary_model: string | null
  summary_generated_at: string | null
  /** Sonnet's deeper root-cause analysis, same caching posture as summary_*. */
  root_cause_text: string | null
  root_cause_model: string | null
  root_cause_generated_at: string | null
}

export interface IncidentDetail extends Incident {
  events: AlertEvent[]
}
