/**
 * The box a chart is drawn in, plus its y-axis labels — placed in a gutter
 * **beside** the plot rather than floating on top of it.
 *
 * Both charts used to render their high and low values in an
 * `StyleSheet.absoluteFill` overlay pinned to the plot's own corners. On a
 * quiet chart that looks fine, which is why it survived. On a real one it does
 * not: this app's own Network panel draws twelve series, several of which sit
 * near zero, and the trace runs straight through the "0 B/s" label in the
 * bottom-left corner. An axis label that is only readable when the data is
 * boring is not an axis label.
 *
 * A gutter costs horizontal space, which is the scarce dimension on a phone —
 * hence a tight fixed width, right-aligned so the numbers line up with the
 * plot edge, and `numberOfLines={1}` so a long formatted value shrinks the
 * plot by exactly nothing.
 */

import type { ReactNode } from "react"
import { useState } from "react"
import { StyleSheet, Text, View, type LayoutChangeEvent } from "react-native"

import { GUTTER_WIDTH } from "@/lib/plotGeometry"
import { colors, radius, spacing, text } from "@/theme"

/** Below this a third label in the middle is more crowding than information. */
const MID_LABEL_MIN_HEIGHT = 110

interface PlotFrameProps {
  height: number
  /** Top and bottom of the y-scale, in data units. */
  hi: number
  lo: number
  valueFormatter: (v: number) => string
  /** Nothing to draw: the gutter is suppressed rather than labelled with the
   * placeholder scale, which would be a y-axis for data that does not exist. */
  empty?: boolean
  emptyLabel?: string
  /** The measured plot width, which is the card's width minus the gutter.
   * A caller that computes its own geometry needs this rather than its own
   * onLayout — measuring the card would place points under the labels. */
  onWidth?: (width: number) => void
  /** The SVG, built once the plot's own width has been measured. */
  children: (width: number) => ReactNode
}

export function PlotFrame({
  height,
  hi,
  lo,
  valueFormatter,
  empty = false,
  emptyLabel = "No data yet.",
  onWidth,
  children,
}: PlotFrameProps) {
  const [width, setWidth] = useState(0)

  const onLayout = (event: LayoutChangeEvent) => {
    const next = event.nativeEvent.layout.width
    setWidth(next)
    onWidth?.(next)
  }

  const showMid = !empty && height >= MID_LABEL_MIN_HEIGHT

  return (
    <View style={styles.row}>
      <View style={[styles.gutter, { height }]}>
        {!empty && (
          <>
            <Text style={[text.tiny, styles.label]} numberOfLines={1}>
              {valueFormatter(hi)}
            </Text>
            {showMid && (
              <Text style={[text.tiny, styles.label]} numberOfLines={1}>
                {valueFormatter(lo + (hi - lo) / 2)}
              </Text>
            )}
            <Text style={[text.tiny, styles.label]} numberOfLines={1}>
              {valueFormatter(lo)}
            </Text>
          </>
        )}
      </View>

      <View onLayout={onLayout} style={[styles.plot, { height }]}>
        {width > 0 && children(width)}
        {empty && (
          <View style={styles.overlay} pointerEvents="none">
            <Text style={text.small}>{emptyLabel}</Text>
          </View>
        )}
      </View>
    </View>
  )
}

const styles = StyleSheet.create({
  row: { flexDirection: "row", gap: spacing.xs },
  gutter: {
    width: GUTTER_WIDTH,
    justifyContent: "space-between",
    alignItems: "flex-end",
    // The first and last labels name the frame's own top and bottom edges, so
    // they are nudged onto those lines rather than sitting inside them.
    paddingVertical: 0,
  },
  label: { fontVariant: ["tabular-nums"] },
  plot: {
    flex: 1,
    backgroundColor: colors.background,
    borderRadius: radius.sm,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.border,
    overflow: "hidden",
  },
  overlay: { ...StyleSheet.absoluteFill, alignItems: "center", justifyContent: "center" },
})
