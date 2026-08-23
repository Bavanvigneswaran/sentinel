const PALETTE = [
  "#3b82f6", // blue
  "#f59e0b", // amber
  "#10b981", // emerald
  "#ef4444", // red
  "#8b5cf6", // violet
  "#06b6d4", // cyan
  "#ec4899", // pink
  "#84cc16", // lime
]

/** Deterministic color assignment so a NIC/disk/target keeps the same color
 * across renders even as other series come and go. */
export function colorForIndex(index: number): string {
  return PALETTE[index % PALETTE.length]
}
