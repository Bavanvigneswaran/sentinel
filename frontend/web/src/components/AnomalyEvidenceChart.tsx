import { useEffect, useState } from "react"

import { HistoryChart } from "@/components/charts/HistoryChart"
import { apiFetch } from "@/lib/api"
import { pivotByEntity, pivotColumns, type Pivoted } from "@/lib/seriesPivot"
import type { AlertEvent, AnomalyBaseline, Metric } from "@/types/alerts"
import type { Series, SeriesDomain, SystemPoint } from "@/types/series"

const METRIC_DOMAIN: Record<Metric, SeriesDomain> = {
  cpu_percent: "system",
  mem_percent: "system",
  swap_percent: "system",
  cpu_iowait_percent: "system",
  disk_percent: "disk_usage",
  packet_loss_percent: "latency",
}

function observedTrace(series: Series, metric: Metric): Pivoted {
  if (metric === "disk_percent") return pivotByEntity(series.disk_usage, "mount", "percent")
  if (metric === "packet_loss_percent") {
    return pivotByEntity(series.latency, "target", "packet_loss_percent")
  }
  // Narrowed to the remaining four Metric members, all real SystemPoint keys.
  return pivotColumns<SystemPoint>(series.system, [{ key: metric, label: metric }])
}

const percent = (v: number) => `${v.toFixed(1)}%`

/**
 * The real metric trace around one anomaly event, with two flat reference
 * lines for context: the baseline mean as it stood right before the event
 * fired (the authoritative "then", snapshotted on the event itself), and
 * the AnomalyBaseline row's current mean (the "now", which may have drifted
 * since — the baseline keeps adapting after the event resolves). Shown as
 * two distinct lines rather than a full ±band around each, to keep the
 * chart legible.
 */
export function AnomalyEvidenceChart({ event }: { event: AlertEvent }) {
  // `event.metric` is RuleMetric, which admits the reserved `novelty_score`.
  // An anomaly event can never carry it — the backend's multivariate_metric
  // CHECK pairs that metric exclusively with the multivariate rule type, and
  // this chart is only rendered for anomaly events. Narrowing here rather
  // than casting keeps that reasoning checkable: if a combination event ever
  // does reach this component, it renders a sentence instead of indexing a
  // per-metric table with a key that isn't in it.
  const metric = event.metric === "novelty_score" ? null : event.metric
  const [series, setSeries] = useState<Series | null>(null)
  const [currentBaseline, setCurrentBaseline] = useState<AnomalyBaseline | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    if (metric === null) return
    const domain = METRIC_DOMAIN[metric]
    // An hour of context before the fire, through resolution (or now, if
    // still firing) — enough to see the pre-anomaly baseline and the spike.
    const start = new Date(new Date(event.fired_at).getTime() - 3_600_000).toISOString()
    const end = event.resolved_at ?? new Date().toISOString()

    Promise.all([
      apiFetch<Series>(
        `/devices/${event.device_id}/series?domains=${domain}&start=${start}&end=${end}`,
      ),
      apiFetch<AnomalyBaseline[]>(`/alerts/anomaly-baselines?device_id=${event.device_id}`),
    ])
      .then(([seriesData, baselines]) => {
        if (cancelled) return
        setSeries(seriesData)
        setCurrentBaseline(baselines.find((b) => b.metric === event.metric) ?? null)
        setError(null)
      })
      .catch(() => {
        if (!cancelled) setError("Could not load evidence for this event.")
      })
    return () => {
      cancelled = true
    }
  }, [event.id, event.device_id, event.metric, metric, event.fired_at, event.resolved_at])

  if (metric === null) {
    return (
      <p className="text-sm text-muted-foreground">
        This alert is about a combination of metrics, not one of them — there is no single
        trace to draw.
      </p>
    )
  }
  if (error) return <p className="text-sm text-destructive">{error}</p>
  if (series === null) return <p className="text-sm text-muted-foreground">Loading…</p>

  const trace = observedTrace(series, metric)
  if (trace.timestamps.length === 0) {
    return <p className="text-sm text-muted-foreground">No data in this window.</p>
  }

  const referenceLines = [
    event.baseline_mean !== null && {
      label: "baseline (at fire)",
      color: "#94a3b8",
      values: trace.timestamps.map(() => event.baseline_mean as number),
    },
    currentBaseline && {
      label: "baseline (current)",
      color: "#475569",
      values: trace.timestamps.map(() => currentBaseline.mean),
    },
  ].filter((s): s is { label: string; color: string; values: number[] } => Boolean(s))

  return (
    <HistoryChart
      timestamps={trace.timestamps}
      series={[...trace.series, ...referenceLines]}
      height={180}
      valueFormatter={percent}
    />
  )
}
