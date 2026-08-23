import type { ForecastOverlay } from "@/components/charts/HistoryChart"
import type { MetricForecast } from "@/types/forecast"

/**
 * Turning a stored forecast row into the dashed-line overlay `HistoryChart`
 * draws, and the one-line caption under it.
 *
 * Shared between DeviceHistoryPage (overlaid on the real series it continues)
 * and ForecastsPage (drawn on its own, as a small per-metric chart) — both
 * read the same `MetricForecast.points`, so the math staying in one place is
 * what keeps a device's own history chart and its fleet-wide summary chart
 * from ever being able to disagree about the same underlying fit.
 */

/**
 * How a forecast's own confidence should be described under its chart.
 *
 * A forecast now appears within minutes of a device enrolling instead of after
 * a full day, because the worker plans its window from when the device started
 * reporting. That is only honest if the projection says how thin the ground
 * under it is — so the horizon is capped to the history behind it, and this
 * puts that in words rather than leaving the user to notice the dashed line is
 * short.
 */
export function forecastCaption(forecast: MetricForecast | undefined): string | undefined {
  if (!forecast || forecast.points.length === 0) return undefined
  const hours = Math.max(1, Math.round((forecast.history_seconds ?? 0) / 3600))
  const span = hours < 48 ? `${hours}h` : `${Math.round(hours / 24)}d`
  if (forecast.confidence === "high") return undefined
  return forecast.confidence === "provisional"
    ? `Provisional forecast — fitted on ${span} of history, so it only projects that far ahead.`
    : `Forecast fitted on ${span} of history; it will lengthen and steady as more accumulates.`
}

/** A forecast continues past the real series' last timestamp, dashed, in the
 * same colour as the real series it extends — see HistoryChart. Undefined
 * (no overlay) when there wasn't enough history to fit one. */
export function toForecastOverlay(
  forecast: MetricForecast | undefined,
  label: string,
  color: string,
): ForecastOverlay | undefined {
  if (!forecast || forecast.points.length === 0) return undefined
  const computedAtSeconds = new Date(forecast.computed_at).getTime() / 1000
  return {
    label,
    color,
    timestamps: forecast.points.map((p) => computedAtSeconds + p.offset_seconds),
    predicted: forecast.points.map((p) => p.predicted),
    lower: forecast.points.map((p) => p.lower),
    upper: forecast.points.map((p) => p.upper),
  }
}
