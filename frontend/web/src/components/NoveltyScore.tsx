/**
 * The layer-4 multivariate novelty score: how unusual this machine's current
 * reading is against a model trained on its own history
 * (app/analysis/multivariate.py, GET /devices/{id}/novelty).
 *
 * Distinct from the Anomalies page, and the distinction is the whole point:
 * that page shows per-metric EWMA/MAD deviations, each metric judged alone.
 * This is one number for the *combination*, which is what catches a machine
 * whose every individual reading is unremarkable.
 *
 * `available: false` renders the backend's own reason verbatim rather than a
 * dash. There are four distinct ways to have no score and they have four
 * different fixes — "no model trained yet" is a thing the operator can go and
 * do something about, and hiding that behind a null would make the feature
 * look broken rather than un-run.
 */

import type { DeviceNovelty } from "@/types/novelty"

/** Where the score stops being ordinary. Both thresholds are presentation
 *  only — the model emits a continuous percentile and nothing downstream
 *  branches on these. */
const UNUSUAL_AT = 95
const NOTABLE_AT = 80

function toneFor(score: number): string {
  if (score >= UNUSUAL_AT) return "text-destructive"
  if (score >= NOTABLE_AT) return "text-foreground"
  return "text-muted-foreground"
}

function verdictFor(score: number): string {
  if (score >= UNUSUAL_AT) return "unusual for this machine"
  if (score >= NOTABLE_AT) return "busier than usual"
  return "ordinary for this machine"
}

export function NoveltyScore({ novelty }: { novelty: DeviceNovelty }) {
  if (!novelty.available || novelty.score === null) {
    return (
      <div className="flex flex-col gap-1">
        <span className="text-xs text-muted-foreground">Novelty</span>
        <span className="max-w-56 text-sm text-muted-foreground">
          {novelty.reason ?? "No score available."}
        </span>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-1">
      <span className="text-xs text-muted-foreground">Novelty</span>
      <span className={`text-2xl font-semibold tabular-nums ${toneFor(novelty.score)}`}>
        {novelty.score.toFixed(0)}
        <span className="text-sm font-normal text-muted-foreground">/100</span>
      </span>
      <span className="text-sm">{verdictFor(novelty.score)}</span>
      {novelty.trained_on_samples !== null && (
        <span className="text-xs text-muted-foreground">
          vs {novelty.trained_on_samples.toLocaleString()} of its own readings
        </span>
      )}
    </div>
  )
}
