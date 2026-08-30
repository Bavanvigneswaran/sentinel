/**
 * Login, signup and requesting a password reset. One screen, three modes —
 * they differ by which fields show and which endpoint they POST to, and
 * web/src/components/AuthForm.tsx already treats sign-in and sign-up as one
 * thing.
 *
 * "forgot" only *requests* the mail. Choosing the new password stays in the
 * browser, on the link the mail carries: the token is single-use and scoped to
 * that URL, and deep-linking it into the app would mean either a second
 * consumption path for the same token or handing the app a credential it has
 * no other reason to hold. The backend builds that link from the request's own
 * origin (see CLAUDE.md's password-reset section), which is the address this
 * app is already talking to, so the link lands somewhere the phone can reach.
 */

import { useState } from "react"
import { KeyboardAvoidingView, Platform, ScrollView, StyleSheet, Text, View } from "react-native"

import { Button, ErrorNote, Field, PasswordField } from "@/components/ui"
import { API_BASE_URL } from "@/config"
import { ApiError, apiFetch } from "@/lib/api"
import { useAuth } from "@/stores/auth"
import { colors, spacing, text } from "@/theme"

type Mode = "login" | "signup" | "forgot"

const DESCRIPTIONS: Record<Mode, string> = {
  login: "Sign in to watch your own machines.",
  signup: "Create an account. You only ever see devices you enrolled yourself.",
  forgot: "We'll email you a link to choose a new password.",
}

export function AuthScreen() {
  const [mode, setMode] = useState<Mode>("login")
  const [email, setEmail] = useState("")
  const [password, setPassword] = useState("")
  const [displayName, setDisplayName] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [sent, setSent] = useState(false)

  const login = useAuth((s) => s.login)
  const signup = useAuth((s) => s.signup)
  const bootstrapError = useAuth((s) => s.bootstrapError)

  /** Switch modes, clearing only what belongs to the mode being left.
   *
   * Deliberately does NOT clear the email or the password: the existing toggle
   * never did, and Appium module 3 asserts the email survives a toggle in both
   * directions. The password is hidden in "forgot" mode rather than discarded,
   * so returning to sign-in leaves the form as it was found. */
  const goTo = (next: Mode) => {
    setMode(next)
    setError(null)
    setSent(false)
  }

  const submit = async () => {
    setBusy(true)
    setError(null)
    try {
      if (mode === "login") {
        await login({ email: email.trim(), password })
      } else if (mode === "signup") {
        await signup({
          email: email.trim(),
          password,
          display_name: displayName.trim() || null,
        })
      } else {
        await apiFetch<void>("/auth/forgot-password", {
          method: "POST",
          body: { email: email.trim() },
          anonymous: true,
        })
        setSent(true)
      }
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 429
          ? "Too many requests. Please wait a few minutes and try again."
          : err instanceof Error
            ? err.message
            : "Something went wrong.",
      )
    } finally {
      setBusy(false)
    }
  }

  return (
    <KeyboardAvoidingView
      behavior={Platform.OS === "ios" ? "padding" : undefined}
      style={styles.flex}
    >
      <ScrollView
        contentContainerStyle={styles.content}
        keyboardShouldPersistTaps="handled"
        keyboardDismissMode="on-drag"
      >
        <View style={{ gap: spacing.xs }}>
          <Text style={styles.brand}>Sentinel</Text>
          <Text style={text.small}>{DESCRIPTIONS[mode]}</Text>
        </View>

        {/* Not an error yet — a hint. The single most common first-run problem
            on a device is EXPO_PUBLIC_API_URL pointing somewhere the phone
            cannot reach, and nothing about a failed login says that. */}
        {bootstrapError && <ErrorNote message={bootstrapError} />}

        <View style={{ gap: spacing.md }}>
          <Field
            testID="auth-email"
            label="Email"
            value={email}
            onChangeText={setEmail}
            autoCapitalize="none"
            autoCorrect={false}
            autoComplete="email"
            keyboardType="email-address"
            textContentType="emailAddress"
            placeholder="you@example.com"
          />
          {mode === "signup" && (
            <Field
              testID="auth-display-name"
              label="Display name (optional)"
              value={displayName}
              onChangeText={setDisplayName}
              autoCapitalize="words"
              placeholder="Bavan"
            />
          )}
          {mode !== "forgot" && (
            <PasswordField
              testID="auth-password"
              label="Password"
              value={password}
              onChangeText={setPassword}
              autoCapitalize="none"
              autoComplete={mode === "login" ? "current-password" : "new-password"}
              textContentType={mode === "login" ? "password" : "newPassword"}
              onSubmitEditing={() => void submit()}
              returnKeyType="go"
            />
          )}
          {mode === "signup" && (
            <Text style={text.tiny}>At least 12 characters.</Text>
          )}
        </View>

        {error && <ErrorNote message={error} />}

        {/* Deliberately says "if", and says the same thing for an address with
            no account: the server answers both identically so this endpoint
            cannot be used to find out who has an account here, and a confident
            "sent!" would give that away where the API does not. */}
        {sent && (
          <View style={styles.sentNote} testID="auth-forgot-sent">
            <Text style={text.small}>
              If an account exists for that address, a reset link is on its way. The link
              works once and expires in 30 minutes. Open it on this phone to choose a new
              password, then come back here and sign in.
            </Text>
          </View>
        )}

        {!sent && (
          <Button
            testID="auth-submit"
            title={
              mode === "login" ? "Sign in" : mode === "signup" ? "Create account" : "Email me a link"
            }
            onPress={() => void submit()}
            busy={busy}
            disabled={!email.trim() || (mode !== "forgot" && !password)}
          />
        )}

        {mode === "login" && (
          <Button
            testID="auth-forgot"
            variant="ghost"
            title="Forgot your password?"
            onPress={() => goTo("forgot")}
          />
        )}

        <Button
          testID="auth-toggle-mode"
          variant="ghost"
          title={
            mode === "login" ? "No account yet? Sign up" : "Already have an account? Sign in"
          }
          onPress={() => goTo(mode === "login" ? "signup" : "login")}
        />

        <Text style={[text.tiny, styles.endpoint]} numberOfLines={1}>
          {API_BASE_URL}
        </Text>
      </ScrollView>
    </KeyboardAvoidingView>
  )
}

const styles = StyleSheet.create({
  flex: { flex: 1, backgroundColor: colors.background },
  content: {
    flexGrow: 1,
    justifyContent: "center",
    padding: spacing.xl,
    gap: spacing.lg,
  },
  brand: { fontSize: 28, fontWeight: "700", color: colors.foreground, letterSpacing: -0.5 },
  endpoint: { textAlign: "center" },
  sentNote: {
    padding: spacing.md,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: colors.border,
    backgroundColor: colors.card,
  },
})
