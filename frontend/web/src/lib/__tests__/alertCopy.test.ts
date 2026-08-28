import { describe, expect, it } from "vitest"

import { describeEventCondition, describeEventEvidence } from "@/lib/alertCopy"
import type { AlertEvent } from "@/types/alerts"

function event(overrides: Partial<AlertEvent> = {}): AlertEvent {
  return {
    id: "e1",
    rule_id: "r1",
    device_id: "d1",
    incident_id: null,
    rule_name: "A rule",
    rule_type: "threshold",
    metric: "cpu_percent",
    comparison: ">",
    threshold: 90,
    status: "firing",
    value_at_fire: 93.4,
    last_value: 93.4,
    fired_at: "2026-08-27T10:00:00Z",
    resolved_at: null,
    resolved_value: null,
    notified_at: null,
    observed_value: null,
    baseline_mean: null,
    baseline_mad: null,
    z_score: null,
    severity: null,
    predicted_breach_at: null,
    predicted_value: null,
    ...overrides,
  }
}

describe("describeEventCondition", () => {
  it("reads a threshold event as the metric against its own unit", () => {
    expect(describeEventCondition(event())).toBe("cpu_percent > 90")
  })

  it("marks a forecast event as predicted, so it is not read as already breached", () => {
    expect(describeEventCondition(event({ rule_type: "forecast" }))).toBe(
      "cpu_percent predicted > 90",
    )
  })

  it("never renders an anomaly event's null comparison and threshold", () => {
    // The Phase 17 "(None None)" bug, in its frontend form: an anomaly event
    // leaves both null by design, so the generic clause has nothing to say.
    const text = describeEventCondition(
      event({ rule_type: "anomaly", comparison: null, threshold: null, severity: "warning" }),
    )
    expect(text).toBe("cpu_percent anomaly (warning)")
    expect(text).not.toContain("null")
  })

  it("never shows a multivariate event's reserved metric key", () => {
    // `novelty_score` is a schema detail. Four surfaces printed it verbatim.
    const text = describeEventCondition(
      event({ rule_type: "multivariate", metric: "novelty_score", threshold: 95 }),
    )
    expect(text).toBe("unusual combination of readings > 95/100")
    expect(text).not.toContain("novelty_score")
  })

  it("qualifies a multivariate threshold as a percentile, not a bare number", () => {
    // Every other rule type's threshold is a reading in the metric's own
    // unit; this one is a rank against the machine's own history, so a bare
    // 95 is 95 of nothing.
    expect(describeEventCondition(event({ rule_type: "multivariate", threshold: 95 }))).toContain(
      "/100",
    )
  })
})

describe("describeEventEvidence", () => {
  it("reports a measured event's value at fire plainly", () => {
    expect(describeEventEvidence(event())).toBe("value at fire: 93.4")
  })

  it("does not present a multivariate score as a reading in some unit", () => {
    const text = describeEventEvidence(event({ rule_type: "multivariate", value_at_fire: 98.8 }))
    expect(text).toBe("scored 98.8/100 for unusualness")
    expect(text).not.toContain("value at fire")
  })
})
