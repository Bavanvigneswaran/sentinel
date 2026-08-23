/**
 * A fixed-capacity, columnar buffer for the streaming charts.
 *
 * Copied from web/src/lib/ringBuffer.ts and unchanged: it is pure data
 * structure with no DOM in it, and the columnar shape a chart wants is the
 * same whether the renderer is uPlot or react-native-svg. Sample data never
 * enters React state — LiveChart reads a RingBuffer's arrays directly from a
 * ref on its own timer tick. Re-rendering five charts through React at 1Hz is
 * the reliable way to make this screen stutter, and a phone has far less
 * headroom for that than a laptop.
 *
 * Series are discovered lazily: a NIC, disk, or latency target that first
 * appears on sample N gets its history before N backfilled with `null` so
 * every series stays aligned with the shared timestamp axis. `null` means a
 * gap, not zero — this is the same no-synthesis rule that governs every
 * other metric in this product.
 */

export class RingBuffer {
  private readonly capacity: number
  private readonly ts: number[] = []
  private readonly seriesData = new Map<string, (number | null)[]>()

  constructor(capacity: number) {
    this.capacity = capacity
  }

  /** Append one time point. `values` maps series key -> value (or absent /
   * null for "not reported this tick"). */
  push(tsSeconds: number, values: Record<string, number | null | undefined>): void {
    this.ts.push(tsSeconds)

    for (const [key, arr] of this.seriesData) {
      const v = values[key]
      arr.push(v === undefined ? null : v)
    }

    for (const key of Object.keys(values)) {
      if (this.seriesData.has(key)) continue
      const backfilled = new Array<number | null>(this.ts.length - 1).fill(null)
      const v = values[key]
      backfilled.push(v === undefined ? null : v)
      this.seriesData.set(key, backfilled)
    }

    if (this.ts.length > this.capacity) {
      this.ts.shift()
      for (const arr of this.seriesData.values()) arr.shift()
    }
  }

  seriesKeys(): string[] {
    return [...this.seriesData.keys()].sort()
  }

  /** Aligned columnar shape: [xs, ...ys], for the given (ordered) keys. */
  toAlignedData(keys: string[]): (number | null)[][] {
    return [this.ts, ...keys.map((k) => this.seriesData.get(k) ?? this.ts.map(() => null))]
  }

  get length(): number {
    return this.ts.length
  }
}
