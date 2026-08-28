/**
 * How the collector's sample backlog reads on the collector screen.
 *
 * The TypeScript half of the collector module's `BufferPressure.kt`, which does
 * the same job for the persistent notification. Neither can import the other,
 * so the rule is that both render the same three *facts* the service reports —
 * buffered, capacity, dropped — and neither invents a number the other does not
 * have.
 *
 * What this exists to prevent: the ring buffer is bounded (400 samples, about
 * 66 minutes at the 10s cadence) and at the cap it discards the oldest sample
 * for every new one. That is the right behaviour, but it is *loss*, not delay,
 * and a plain count cannot tell the two apart — a phone left idle overnight had
 * been dropping history for hours behind a number that read as healthy.
 *
 * Pure, so the wording is pinned by a test rather than by whichever screen
 * happened to render it, the same reason `chartFrame.ts` and `deviceReadings.ts`
 * live here instead of inside their components.
 */

export interface BufferPressure {
  /** The row value, always something — this row is not conditionally rendered. */
  text: string
  /** True once samples have actually been discarded: the screen calls this out
   *  rather than leaving it as one grey row among several. */
  losing: boolean
}

/** At or above this fraction of capacity, name the cap. The warning is worth
 *  having while there is still something to be done about it. */
export const NEAR_FULL_FRACTION = 0.9

export function describeBuffer(status: {
  bufferedSamples: number
  bufferCapacity: number
  droppedSamples: number
}): BufferPressure {
  const { bufferedSamples: buffered, bufferCapacity: capacity, droppedSamples: dropped } = status
  const lost = dropped > 0 ? ` · ${dropped} lost` : ""

  if (capacity > 0 && buffered >= capacity) {
    return { text: `${buffered} — full, dropping oldest${lost}`, losing: true }
  }
  if (dropped > 0) {
    // The backlog has drained, or is draining, and the hole in the history is
    // still there. Reporting only the current size here would read as a full
    // recovery.
    return {
      text: buffered === 0 ? `${dropped} lost while full` : `${buffered}${lost}`,
      losing: true,
    }
  }
  if (capacity > 0 && buffered >= capacity * NEAR_FULL_FRACTION) {
    return { text: `${buffered} of ${capacity} — nearly full`, losing: false }
  }
  if (buffered === 0) {
    return { text: "nothing waiting", losing: false }
  }
  return { text: `${buffered} sample${buffered === 1 ? "" : "s"}`, losing: false }
}
