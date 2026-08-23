/**
 * Turns the API's row-per-entity-per-bucket shape into the column-per-series
 * shape uPlot wants.
 *
 * The server returns `net` as one row per (bucket, NIC), because that is what
 * the database holds and it keeps the response flat. A chart needs one array
 * per NIC, all sharing one timestamp axis. Pivoting is the translation.
 *
 * Buckets a series was absent from become `null`, never `0`. A NIC that was
 * down for ten minutes had no throughput reading — that is a gap in the line,
 * and drawing it as zero would claim the interface was up and idle.
 */

import { colorForIndex } from "@/lib/chartColors"

export interface PivotedSeries {
  label: string
  color: string
  values: (number | null)[]
}

export interface Pivoted {
  /** Unix seconds, ascending — the shared x-axis. */
  timestamps: number[]
  series: PivotedSeries[]
}

const EMPTY: Pivoted = { timestamps: [], series: [] }

function toSeconds(iso: string): number {
  return new Date(iso).getTime() / 1000
}

/** One series per distinct value of `entityKey`. */
export function pivotByEntity<T extends { ts: string }>(
  points: T[],
  entityKey: keyof T,
  valueKey: keyof T,
  labelPrefix = "",
): Pivoted {
  if (points.length === 0) return EMPTY

  const timestamps = [...new Set(points.map((p) => toSeconds(p.ts)))].sort((a, b) => a - b)
  const indexOf = new Map(timestamps.map((t, i) => [t, i]))

  const byEntity = new Map<string, (number | null)[]>()
  for (const point of points) {
    const entity = String(point[entityKey])
    let values = byEntity.get(entity)
    if (!values) {
      // Pre-filled with null so a series that only appears halfway through the
      // window has a hole before it, not a line from zero.
      values = new Array<number | null>(timestamps.length).fill(null)
      byEntity.set(entity, values)
    }
    const index = indexOf.get(toSeconds(point.ts))
    if (index !== undefined) {
      const raw = point[valueKey]
      values[index] = typeof raw === "number" ? raw : null
    }
  }

  return {
    timestamps,
    series: [...byEntity.entries()]
      // Stable order regardless of the order rows arrived in, so a NIC keeps
      // its colour between two refreshes of the same page.
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([entity, values], index) => ({
        label: `${labelPrefix}${entity}`,
        color: colorForIndex(index),
        values,
      })),
  }
}

/** Several columns of one entity-less domain (CPU total/user/system). */
export function pivotColumns<T extends { ts: string }>(
  points: T[],
  columns: { key: keyof T; label: string }[],
): Pivoted {
  if (points.length === 0) return EMPTY

  return {
    timestamps: points.map((p) => toSeconds(p.ts)),
    series: columns.map((column, index) => ({
      label: column.label,
      color: colorForIndex(index),
      values: points.map((p) => {
        const raw = p[column.key]
        return typeof raw === "number" ? raw : null
      }),
    })),
  }
}

/** Merges two pivots that share an x-axis, e.g. read and write per disk. */
export function mergePivots(first: Pivoted, second: Pivoted): Pivoted {
  if (first.series.length === 0) return second
  if (second.series.length === 0) return first
  return {
    timestamps: first.timestamps,
    series: [
      ...first.series,
      ...second.series.map((s, index) => ({
        ...s,
        color: colorForIndex(first.series.length + index),
      })),
    ],
  }
}
