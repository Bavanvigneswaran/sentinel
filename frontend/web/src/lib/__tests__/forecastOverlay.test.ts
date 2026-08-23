import { describe, expect, it } from "vitest"

import { forecastCaption, toForecastOverlay } from "@/lib/forecastOverlay"
import type { MetricForecast } from "@/types/forecast"

function forecast(overrides: Partial<MetricForecast> = {}): MetricForecast {
  return {
    device_id: "d",
    metric: "mem_percent",
    entity: null,
    computed_at: "2026-08-23T12:00:00Z",
    horizon_seconds: 7200,
    bucket_seconds: 40,
    history_seconds: 7200,
    confidence: "provisional",
    points: [{ offset_seconds: 40, predicted: 1, lower: 0, upper: 2 }],
    ...overrides,
  } as MetricForecast
}

describe("forecastCaption", () => {
  it("says a thin forecast is provisional, and how thin", () => {
    // A forecast now appears within minutes of enrolling instead of after a
    // day. That is only honest if it says how little is under it.
    const caption = forecastCaption(forecast({ confidence: "provisional", history_seconds: 7200 }))
    expect(caption).toContain("Provisional")
    expect(caption).toContain("2h")
  })

  it("stays quiet once a forecast has earned its length", () => {
    expect(forecastCaption(forecast({ confidence: "high", history_seconds: 5 * 86400 }))).toBeUndefined()
  })

  it("says a medium forecast will steady rather than calling it provisional", () => {
    const caption = forecastCaption(forecast({ confidence: "medium", history_seconds: 12 * 3600 }))
    expect(caption).toContain("12h")
    expect(caption).not.toContain("Provisional")
  })

  it("switches to days once hours stop reading well", () => {
    expect(forecastCaption(forecast({ confidence: "medium", history_seconds: 3 * 86400 }))).toContain("3d")
  })

  it("says nothing when there is no forecast at all", () => {
    expect(forecastCaption(undefined)).toBeUndefined()
    expect(forecastCaption(forecast({ points: [] }))).toBeUndefined()
  })

  it("treats an unrecorded history as the smallest, not the largest", () => {
    // A pre-migration row must not be flattered into looking well-founded.
    expect(forecastCaption(forecast({ history_seconds: null }))).toContain("1h")
  })
})

describe("toForecastOverlay", () => {
  it("returns undefined when there is nothing to draw", () => {
    expect(toForecastOverlay(undefined, "Memory", "#fff")).toBeUndefined()
    expect(toForecastOverlay(forecast({ points: [] }), "Memory", "#fff")).toBeUndefined()
  })

  it("anchors every point after the forecast's own computed_at, not the caller's clock", () => {
    // ForecastsPage draws this chart with no real series alongside it, so
    // computed_at is the only anchor a caller has for "when is this from" —
    // using Date.now() instead would silently drift the x-axis every time the
    // page re-renders.
    const overlay = toForecastOverlay(
      forecast({
        computed_at: "2026-08-23T12:00:00Z",
        points: [
          { offset_seconds: 0, predicted: 10, lower: 8, upper: 12 },
          { offset_seconds: 60, predicted: 11, lower: 9, upper: 13 },
        ],
      }),
      "Memory",
      "#fff",
    )
    const computedAtSeconds = new Date("2026-08-23T12:00:00Z").getTime() / 1000
    expect(overlay?.timestamps).toEqual([computedAtSeconds, computedAtSeconds + 60])
    expect(overlay?.predicted).toEqual([10, 11])
    expect(overlay?.lower).toEqual([8, 9])
    expect(overlay?.upper).toEqual([12, 13])
    expect(overlay?.label).toBe("Memory")
    expect(overlay?.color).toBe("#fff")
  })
})
