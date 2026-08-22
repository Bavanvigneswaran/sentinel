/**
 * The single place "unavailable" is rendered for a metric.
 *
 * `null` and `undefined` are real, meaningful states here — a platform that
 * cannot measure something reports nothing for it (see CLAUDE.md's hard
 * rules). This component is the enforcement point: it is the only thing
 * allowed to turn "no value" into text, and it always says "unavailable",
 * never "0" and never a blank cell that could be misread as a real zero.
 */
export function MetricValue({
  value,
  format,
  unit,
}: {
  value: number | null | undefined
  format?: (v: number) => string
  unit?: string
}) {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return <span className="text-muted-foreground">unavailable</span>
  }
  const text = format ? format(value) : value.toString()
  return (
    <span>
      {text}
      {unit ? <span className="text-muted-foreground">{unit}</span> : null}
    </span>
  )
}
