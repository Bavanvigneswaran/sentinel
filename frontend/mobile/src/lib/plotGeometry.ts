/**
 * The two numbers a chart's frame and the things drawn beside it both need.
 *
 * They live here rather than in components/charts/PlotFrame.tsx for the same
 * reason chartFrame.ts and historyChartFrame.ts do: a module that exports a
 * component and also exports constants breaks fast refresh, and these are
 * consumed by three files that must not each keep their own copy — the axis
 * gutter's width is what HistoryChart's x-axis labels are indented by, and a
 * mismatch there is a misaligned axis nobody would notice in review.
 */

/**
 * Width of the y-axis label gutter beside a plot, in points.
 *
 * Sized against the **longest** label the formatters actually produce, which
 * is a three-digit byte rate: "146.1 MB/s" is ten characters at 11pt. 52pt
 * fitted "1.4 MB/s" and "100%" and truncated that one to "146.1 M…" on a real
 * Disk I/O chart — a y-axis whose top value is unreadable is not a y-axis. A
 * 360pt phone still leaves the plot ~280pt, which is more than the ~180 points
 * either chart plots.
 */
export const GUTTER_WIDTH = 64

/** Where the horizontal gridlines go, as fractions of the plot height. The
 * gutter's top and bottom labels name the frame's own edges and its middle
 * label names 0.5, so these three and those three cannot drift. */
export const GRID_FRACTIONS = [0.25, 0.5, 0.75]
