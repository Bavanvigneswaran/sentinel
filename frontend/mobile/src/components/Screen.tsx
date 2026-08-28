/** Page chrome: safe-area padding, the dark ground, a scroll view wired for
 * pull-to-refresh, and the standard header. Every screen uses it so none of
 * them re-derive the same three paddings. */

import { HeaderHeightContext } from "@react-navigation/elements"
import { useContext, type ReactNode } from "react"
import { RefreshControl, ScrollView, StyleSheet, Text, View } from "react-native"
import { useSafeAreaInsets } from "react-native-safe-area-context"

import { colors, spacing, text } from "@/theme"

export function Screen({
  title,
  subtitle,
  right,
  children,
  refreshing,
  onRefresh,
  testID,
}: {
  title?: string
  subtitle?: string
  right?: ReactNode
  children: ReactNode
  refreshing?: boolean
  onRefresh?: () => void
  /**
   * Required, not optional, and that is the point: every screen goes through
   * this component, so a required prop is the one place a new screen can be
   * made to arrive with a locator already on it. Optional would mean the
   * hooks decay back to XPath-over-visible-text one screen at a time, which
   * is the state this replaced. `screen-fleet`, `screen-alerts`, and so on.
   *
   * Surfaces as Android `resource-id`. The title gets `${testID}-title`.
   */
  testID: string
}) {
  const insets = useSafeAreaInsets()

  // The tab screens run with `headerShown: false` and must clear the status
  // bar themselves. The pushed stack screens sit under a native header that
  // has already done it — and react-navigation still reports the *window*
  // inset to them, so adding it again pads twice (caught on the emulator as a
  // finger-wide gap under the Device screen's header). Deriving it from the
  // header height makes this automatic rather than a prop every new screen
  // has to remember to pass.
  //
  // Truthiness, not `=== undefined`: the bottom-tab navigator provides this
  // context as 0 when its header is hidden rather than leaving it unset, so an
  // undefined check silently un-padded all four tab screens. Also caught on
  // the emulator, by the Alerts title landing on top of the clock.
  const headerHeight = useContext(HeaderHeightContext)
  const topInset = headerHeight ? 0 : insets.top

  return (
    <ScrollView
      testID={testID}
      style={styles.root}
      contentContainerStyle={[
        styles.content,
        { paddingTop: topInset + spacing.lg, paddingBottom: insets.bottom + spacing.xl },
      ]}
      refreshControl={
        onRefresh ? (
          <RefreshControl
            refreshing={refreshing ?? false}
            onRefresh={onRefresh}
            tintColor={colors.muted}
            colors={[colors.primary]}
            progressBackgroundColor={colors.card}
          />
        ) : undefined
      }
    >
      {(title || subtitle || right) && (
        <View style={styles.header}>
          <View style={styles.headerText}>
            {title && (
              <Text style={text.title} testID={`${testID}-title`}>
                {title}
              </Text>
            )}
            {subtitle && <Text style={text.small}>{subtitle}</Text>}
          </View>
          {right}
        </View>
      )}
      {children}
    </ScrollView>
  )
}

/** The "nothing here yet" state. Distinct from a loading state on purpose —
 * an empty fleet and an unloaded fleet are different facts. */
export function EmptyState({
  title,
  body,
  testID = "empty-state",
}: {
  title: string
  body: string
  testID?: string
}) {
  return (
    <View style={styles.empty} testID={testID}>
      <Text style={text.heading}>{title}</Text>
      <Text style={text.small}>{body}</Text>
    </View>
  )
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.background },
  content: { paddingHorizontal: spacing.lg, gap: spacing.md },
  header: {
    flexDirection: "row",
    alignItems: "flex-start",
    justifyContent: "space-between",
    gap: spacing.md,
    marginBottom: spacing.xs,
  },
  headerText: { flex: 1, gap: 2 },
  empty: {
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.border,
    borderRadius: 10,
    padding: spacing.xl,
    gap: spacing.xs,
    alignItems: "center",
  },
})
