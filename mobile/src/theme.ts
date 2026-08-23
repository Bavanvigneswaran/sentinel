/**
 * The palette, as plain values.
 *
 * The web app is Tailwind + shadcn; there is no Tailwind here, so the same
 * dark ops-console colours are spelled out once and imported. The hex values
 * are shadcn's `dark` theme tokens and the chart palette in
 * web/src/lib/chartColors.ts, so the two clients look like one product.
 */

export const colors = {
  background: "#09090b",
  card: "#111113",
  cardElevated: "#17171a",
  border: "#27272a",
  foreground: "#fafafa",
  muted: "#a1a1aa",
  mutedDeep: "#71717a",
  primary: "#3b82f6",
  primaryForeground: "#f8fafc",
  destructive: "#ef4444",

  // Health / status bands. Same three colours the web HealthScore uses.
  healthy: "#10b981",
  degraded: "#f59e0b",
  critical: "#ef4444",
  unknown: "#71717a",
} as const

export const spacing = {
  xs: 4,
  sm: 8,
  md: 12,
  lg: 16,
  xl: 24,
} as const

export const radius = {
  sm: 6,
  md: 10,
  full: 999,
} as const

export const text = {
  title: { fontSize: 20, fontWeight: "600" as const, color: colors.foreground },
  heading: { fontSize: 16, fontWeight: "600" as const, color: colors.foreground },
  body: { fontSize: 14, color: colors.foreground },
  small: { fontSize: 12, color: colors.muted },
  tiny: { fontSize: 11, color: colors.mutedDeep },
} as const

/** react-navigation wants its own theme object shape. */
export const navigationTheme = {
  dark: true,
  colors: {
    primary: colors.primary,
    background: colors.background,
    card: colors.card,
    text: colors.foreground,
    border: colors.border,
    notification: colors.primary,
  },
  fonts: {
    regular: { fontFamily: "System", fontWeight: "400" as const },
    medium: { fontFamily: "System", fontWeight: "500" as const },
    bold: { fontFamily: "System", fontWeight: "700" as const },
    heavy: { fontFamily: "System", fontWeight: "800" as const },
  },
}
