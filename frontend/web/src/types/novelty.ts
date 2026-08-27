/**
 * GET /devices/{id}/novelty — the layer-4 multivariate novelty score.
 *
 * Mirrors app/schemas/novelty.py. One shape for both outcomes: `available`
 * discriminates, and exactly one of `score` / `reason` is ever populated.
 */

export interface DeviceNovelty {
  available: boolean
  /** 0-100, a percentile against this device's own training history. */
  score: number | null
  /** Why there is no score. Populated only when `available` is false. */
  reason: string | null
  trained_on_samples: number | null
  feature_names: string[] | null
  reading_ts: string | null
}
