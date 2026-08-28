/**
 * The handful of primitives every screen uses. The web app gets these from
 * shadcn/ui; React Native has no equivalent, so they are spelled out here —
 * deliberately small, and deliberately not a design system.
 */

import { useState } from "react"
import type { ReactNode } from "react"
import {
  ActivityIndicator,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
  type StyleProp,
  type TextInputProps,
  type ViewStyle,
} from "react-native"

import { colors, radius, spacing, text } from "@/theme"

export function Card({
  children,
  style,
  onPress,
  testID,
}: {
  children: ReactNode
  style?: StyleProp<ViewStyle>
  onPress?: () => void
  testID?: string
}) {
  if (onPress) {
    return (
      <Pressable
        onPress={onPress}
        testID={testID}
        style={({ pressed }) => [styles.card, pressed && styles.cardPressed, style]}
      >
        {children}
      </Pressable>
    )
  }
  return (
    <View style={[styles.card, style]} testID={testID}>
      {children}
    </View>
  )
}

export function CardTitle({ children }: { children: ReactNode }) {
  return <Text style={styles.cardTitle}>{children}</Text>
}

export function Muted({ children, style }: { children: ReactNode; style?: StyleProp<ViewStyle> }) {
  return <Text style={[text.small, style as never]}>{children}</Text>
}

export function Button({
  title,
  onPress,
  variant = "primary",
  size = "md",
  disabled = false,
  busy = false,
  testID,
}: {
  title: string
  onPress: () => void
  variant?: "primary" | "outline" | "ghost" | "destructive"
  size?: "sm" | "md"
  disabled?: boolean
  busy?: boolean
  /** Surfaces as Android `resource-id`, which is what Appium locates by. */
  testID?: string
}) {
  const isDisabled = disabled || busy
  return (
    <Pressable
      accessibilityRole="button"
      // Without this the Pressable has a role and no name: the title is a child
      // Text, so the a11y tree carries it as a separate node and Android sets
      // no content-desc on the button itself. TalkBack coped by reading the
      // child; a content-desc lookup found nothing. Same string either way.
      accessibilityLabel={title}
      accessibilityState={{ disabled: isDisabled, busy }}
      disabled={isDisabled}
      onPress={onPress}
      testID={testID}
      style={({ pressed }) => [
        styles.button,
        size === "sm" && styles.buttonSm,
        variant === "primary" && styles.buttonPrimary,
        variant === "outline" && styles.buttonOutline,
        variant === "ghost" && styles.buttonGhost,
        variant === "destructive" && styles.buttonDestructive,
        pressed && styles.buttonPressed,
        isDisabled && styles.buttonDisabled,
      ]}
    >
      {busy ? (
        <ActivityIndicator color={variant === "primary" ? colors.primaryForeground : colors.foreground} />
      ) : (
        <Text
          style={[
            styles.buttonText,
            size === "sm" && styles.buttonTextSm,
            variant === "primary" && { color: colors.primaryForeground },
            variant === "destructive" && { color: colors.primaryForeground },
          ]}
        >
          {title}
        </Text>
      )}
    </Pressable>
  )
}

export function Field({
  label,
  ...props
}: TextInputProps & { label: string }) {
  return (
    <View style={{ gap: spacing.xs }}>
      <Text style={text.small}>{label}</Text>
      <TextInput
        placeholderTextColor={colors.mutedDeep}
        style={styles.input}
        accessibilityLabel={label}
        {...props}
      />
    </View>
  )
}

/** A password Field with a trailing eye toggle between masked and plain text. */
export function PasswordField({
  label,
  ...props
}: Omit<TextInputProps, "secureTextEntry"> & { label: string }) {
  const [visible, setVisible] = useState(false)
  return (
    <View style={{ gap: spacing.xs }}>
      <Text style={text.small}>{label}</Text>
      <View style={styles.passwordRow}>
        <TextInput
          placeholderTextColor={colors.mutedDeep}
          style={[styles.input, styles.passwordInput]}
          accessibilityLabel={label}
          secureTextEntry={!visible}
          {...props}
        />
        <Pressable
          accessibilityRole="button"
          accessibilityLabel={visible ? "Hide password" : "Show password"}
          accessibilityState={{ selected: visible }}
          onPress={() => setVisible((v) => !v)}
          style={styles.passwordToggle}
          hitSlop={8}
        >
          <Text style={styles.passwordToggleText}>{visible ? "🙈" : "👁"}</Text>
        </Pressable>
      </View>
    </View>
  )
}

/** An inline error strip. Never a modal: an error on a monitoring screen
 * should not hide the numbers behind it. */
export function ErrorNote({ message }: { message: string }) {
  return (
    <View style={styles.errorNote}>
      <Text style={{ ...text.small, color: colors.destructive }}>{message}</Text>
    </View>
  )
}

export function Segmented<T extends string>({
  options,
  value,
  onChange,
}: {
  options: { value: T; label: string }[]
  value: T
  onChange: (next: T) => void
}) {
  return (
    <View style={styles.segmented}>
      {options.map((option) => {
        const active = option.value === value
        return (
          <Pressable
            key={option.value}
            accessibilityRole="button"
            accessibilityState={{ selected: active }}
            onPress={() => onChange(option.value)}
            style={[styles.segment, active && styles.segmentActive]}
          >
            <Text style={[styles.segmentText, active && styles.segmentTextActive]}>
              {option.label}
            </Text>
          </Pressable>
        )
      })}
    </View>
  )
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: colors.card,
    borderColor: colors.border,
    borderWidth: StyleSheet.hairlineWidth,
    borderRadius: radius.md,
    padding: spacing.lg,
    gap: spacing.md,
  },
  cardPressed: { backgroundColor: colors.cardElevated },
  cardTitle: { ...text.small, textTransform: "uppercase", letterSpacing: 0.6 },
  button: {
    borderRadius: radius.sm,
    paddingVertical: 11,
    paddingHorizontal: spacing.lg,
    alignItems: "center",
    justifyContent: "center",
    minHeight: 44,
  },
  buttonSm: { paddingVertical: 7, paddingHorizontal: spacing.md, minHeight: 34 },
  buttonPrimary: { backgroundColor: colors.primary },
  buttonOutline: {
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.border,
    backgroundColor: "transparent",
  },
  buttonGhost: { backgroundColor: "transparent" },
  buttonDestructive: { backgroundColor: colors.destructive },
  buttonPressed: { opacity: 0.75 },
  buttonDisabled: { opacity: 0.5 },
  buttonText: { ...text.body, fontWeight: "600" },
  buttonTextSm: { fontSize: 13 },
  input: {
    backgroundColor: colors.background,
    borderColor: colors.border,
    borderWidth: StyleSheet.hairlineWidth,
    borderRadius: radius.sm,
    paddingHorizontal: spacing.md,
    paddingVertical: 11,
    color: colors.foreground,
    fontSize: 15,
  },
  passwordRow: { justifyContent: "center" },
  passwordInput: { paddingRight: spacing.xl + spacing.md },
  passwordToggle: {
    position: "absolute",
    right: spacing.sm,
    height: 44,
    width: 32,
    alignItems: "center",
    justifyContent: "center",
  },
  passwordToggleText: { fontSize: 16 },
  errorNote: {
    backgroundColor: "rgba(239, 68, 68, 0.08)",
    borderColor: "rgba(239, 68, 68, 0.35)",
    borderWidth: StyleSheet.hairlineWidth,
    borderRadius: radius.sm,
    padding: spacing.md,
  },
  // Wraps: a metric picker has six options and a phone is ~330pt wide. A
  // non-wrapping row would push the last two off-screen with no scroll
  // affordance to find them by.
  segmented: { flexDirection: "row", flexWrap: "wrap", gap: spacing.sm },
  segment: {
    paddingVertical: 6,
    paddingHorizontal: spacing.md,
    borderRadius: radius.full,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.border,
  },
  segmentActive: { backgroundColor: colors.primary, borderColor: colors.primary },
  segmentText: { ...text.small },
  segmentTextActive: { color: colors.primaryForeground, fontWeight: "600" },
})
