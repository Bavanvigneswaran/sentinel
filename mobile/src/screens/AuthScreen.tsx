/**
 * Login and signup. One screen, two modes — the two forms differ by a single
 * optional field and which endpoint they POST to, and web/src/components/
 * AuthForm.tsx already treats them as one thing.
 */

import { useState } from "react"
import { KeyboardAvoidingView, Platform, ScrollView, StyleSheet, Text, View } from "react-native"

import { Button, ErrorNote, Field } from "@/components/ui"
import { API_BASE_URL } from "@/config"
import { useAuth } from "@/stores/auth"
import { colors, spacing, text } from "@/theme"

type Mode = "login" | "signup"

export function AuthScreen() {
  const [mode, setMode] = useState<Mode>("login")
  const [email, setEmail] = useState("")
  const [password, setPassword] = useState("")
  const [displayName, setDisplayName] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const login = useAuth((s) => s.login)
  const signup = useAuth((s) => s.signup)
  const bootstrapError = useAuth((s) => s.bootstrapError)

  const submit = async () => {
    setBusy(true)
    setError(null)
    try {
      if (mode === "login") {
        await login({ email: email.trim(), password })
      } else {
        await signup({
          email: email.trim(),
          password,
          display_name: displayName.trim() || null,
        })
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong.")
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
          <Text style={text.small}>
            {mode === "login"
              ? "Sign in to watch your own machines."
              : "Create an account. You only ever see devices you enrolled yourself."}
          </Text>
        </View>

        {/* Not an error yet — a hint. The single most common first-run problem
            on a device is EXPO_PUBLIC_API_URL pointing somewhere the phone
            cannot reach, and nothing about a failed login says that. */}
        {bootstrapError && <ErrorNote message={bootstrapError} />}

        <View style={{ gap: spacing.md }}>
          <Field
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
              label="Display name (optional)"
              value={displayName}
              onChangeText={setDisplayName}
              autoCapitalize="words"
              placeholder="Bavan"
            />
          )}
          <Field
            label="Password"
            value={password}
            onChangeText={setPassword}
            secureTextEntry
            autoCapitalize="none"
            autoComplete={mode === "login" ? "current-password" : "new-password"}
            textContentType={mode === "login" ? "password" : "newPassword"}
            onSubmitEditing={() => void submit()}
            returnKeyType="go"
          />
          {mode === "signup" && (
            <Text style={text.tiny}>At least 12 characters.</Text>
          )}
        </View>

        {error && <ErrorNote message={error} />}

        <Button
          title={mode === "login" ? "Sign in" : "Create account"}
          onPress={() => void submit()}
          busy={busy}
          disabled={!email.trim() || !password}
        />

        <Button
          variant="ghost"
          title={
            mode === "login" ? "No account yet? Sign up" : "Already have an account? Sign in"
          }
          onPress={() => {
            setMode(mode === "login" ? "signup" : "login")
            setError(null)
          }}
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
})
