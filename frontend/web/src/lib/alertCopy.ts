/**
 * How a fired alert's condition reads on screen.
 *
 * This exists because the same conditional was copy-pasted across every
 * surface that lists events — the triage page and an incident's timeline —
 * and each one had to be taught about a new rule type separately. Layer 4's
 * "multivariate" type reached five surfaces and was missed by four more,
 * every time rendering the raw schema key: `novelty_score > 95`.
 *
 * Two rule types cannot use the shared "metric comparison threshold" shape:
 *
 * * An **anomaly** event leaves `comparison`/`threshold` null by design, so
 *   the generic clause renders "None None" (the Phase 17 notification bug).
 * * A **multivariate** event's `metric` is the reserved `novelty_score`, a
 *   schema detail rather than a reading, and its threshold is a percentile
 *   against that machine's own history rather than a value in any unit —
 *   hence "/100". A bare 71 is 71 of nothing.
 *
 * Pure and string-returning so it can be tested without a DOM, the same way
 * lib/deviceNames.ts and lib/forecastOverlay.ts are — the previous misses
 * were all found by looking at a running page, which is exactly the kind of
 * check a pure module makes cheap.
 */

import type { AlertEvent } from "@/types/alerts"

/** The subject and condition of one event, e.g. "cpu_percent > 90" or
 *  "unusual combination of readings > 95/100". */
export function describeEventCondition(event: AlertEvent): string {
  if (event.rule_type === "anomaly") {
    return `${event.metric} anomaly (${event.severity ?? "—"})`
  }

  const clause = `${event.comparison ?? ""} ${event.threshold ?? ""}`.trim()

  if (event.rule_type === "multivariate") {
    return `unusual combination of readings ${clause}/100`
  }
  if (event.rule_type === "forecast") {
    return `${event.metric} predicted ${clause}`
  }
  return `${event.metric} ${clause}`
}

/** The evidence line's leading clause: what was actually observed when it
 *  fired. A multivariate event's `value_at_fire` is the percentile itself,
 *  so "value at fire: 98.8" would read as a measurement in a unit it does
 *  not have. */
export function describeEventEvidence(event: AlertEvent): string {
  if (event.rule_type === "multivariate") {
    return `scored ${event.value_at_fire}/100 for unusualness`
  }
  return `value at fire: ${event.value_at_fire}`
}
