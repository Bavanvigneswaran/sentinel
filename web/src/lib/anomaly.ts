/**
 * Client-side mirror of app/analysis/anomaly.py's constants, needed to draw
 * the evidence chart's baseline band on the Anomalies page. Duplicated
 * across the wire boundary same as the Metric/Comparison types already are —
 * this codebase's accepted pattern for values both sides need, not a new one
 * introduced here.
 */

import type { Sensitivity } from "@/types/notifications"

export const MAD_SCALE = 1.4826
export const MIN_SPREAD = 0.5

export const SENSITIVITY_CUTOFFS: Record<Sensitivity, number> = {
  low: 4.0,
  medium: 3.0,
  high: 2.0,
}

/** Sigma-equivalent spread from an unscaled EWMA absolute deviation. */
export function scaledSpread(mad: number): number {
  return Math.max(mad * MAD_SCALE, MIN_SPREAD)
}

/** The [low, high] band a value would need to leave to count as anomalous
 * under `sensitivity`, given a baseline `mean`/`mad`. */
export function bandFor(mean: number, mad: number, sensitivity: Sensitivity): [number, number] {
  const half = scaledSpread(mad) * SENSITIVITY_CUTOFFS[sensitivity]
  return [mean - half, mean + half]
}
