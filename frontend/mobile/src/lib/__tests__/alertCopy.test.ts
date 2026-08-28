import assert from "node:assert/strict"
import { describe, it } from "node:test"

import { describeEventCondition, describeEventEvidence } from "../alertCopy.ts"
import type { AlertEvent } from "../../types/alerts.ts"

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
    assert.equal(describeEventCondition(event()), "cpu_percent > 90")
  })

  it("marks a forecast event as predicted, so it is not read as already breached", () => {
    assert.equal(
      describeEventCondition(event({ rule_type: "forecast" })),
      "cpu_percent predicted > 90",
    )
  })

  it("never renders an anomaly event's null comparison and threshold", () => {
    const text = describeEventCondition(
      event({ rule_type: "anomaly", comparison: null, threshold: null, severity: "warning" }),
    )
    assert.equal(text, "cpu_percent anomaly (warning)")
    assert.ok(!text.includes("null"))
  })

  it("never shows a multivariate event's reserved metric key", () => {
    const text = describeEventCondition(
      event({ rule_type: "multivariate", metric: "novelty_score", threshold: 95 }),
    )
    assert.equal(text, "unusual combination of readings > 95/100")
    assert.ok(!text.includes("novelty_score"))
  })

  it("qualifies a multivariate threshold as a percentile, not a bare number", () => {
    assert.ok(
      describeEventCondition(event({ rule_type: "multivariate", threshold: 95 })).includes("/100"),
    )
  })
})

describe("describeEventEvidence", () => {
  it("reports a measured event's value at fire plainly", () => {
    assert.equal(describeEventEvidence(event()), "value at fire: 93.4")
  })

  it("does not present a multivariate score as a reading in some unit", () => {
    const text = describeEventEvidence(event({ rule_type: "multivariate", value_at_fire: 98.8 }))
    assert.equal(text, "scored 98.8/100 for unusualness")
    assert.ok(!text.includes("value at fire"))
  })
})
