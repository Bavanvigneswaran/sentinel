/**
 * The single place "unavailable" is rendered for a metric. Ported verbatim in
 * spirit from web/src/components/MetricValue.tsx.
 *
 * `null` and `undefined` are real, meaningful states here — a platform that
 * cannot measure something reports nothing for it (see CLAUDE.md's hard
 * rules). This component is the enforcement point: it is the only thing
 * allowed to turn "no value" into text, and it always says "unavailable",
 * never "0" and never a blank cell that could be misread as a real zero.
 */

import { Text, type StyleProp, type TextStyle } from "react-native"

import { colors, text } from "@/theme"

export function MetricValue({
  value,
  format,
  unit,
  style,
}: {
  value: number | null | undefined
  format?: (v: number) => string
  unit?: string
  style?: StyleProp<TextStyle>
}) {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return <Text style={[text.body, { color: colors.mutedDeep }, style]}>unavailable</Text>
  }
  const body = format ? format(value) : value.toString()
  return (
    <Text style={[text.body, style]}>
      {body}
      {unit ? <Text style={{ color: colors.muted }}>{unit}</Text> : null}
    </Text>
  )
}
