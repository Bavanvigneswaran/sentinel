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
}: {
  title?: string
  subtitle?: string
  right?: ReactNode
  children: ReactNode
  refreshing?: boolean
  onRefresh?: () => void
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
            {title && <Text style={text.title}>{title}</Text>}
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
export function EmptyState({ title, body }: { title: string; body: string }) {
  return (
    <View style={styles.empty}>
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
