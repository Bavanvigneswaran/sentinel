/**
 * The app shell.
 *
 * `bootstrapAuth()` is called from index.ts at module scope, not from an
 * effect here — see the comment on it in src/stores/auth.ts. Two concurrent
 * /auth/refresh calls with the same cookie look like token theft to the
 * backend and revoke the whole family, and StrictMode double-invoking an
 * effect is exactly how you get two.
 */

import { NavigationContainer } from "@react-navigation/native"
import { StatusBar } from "expo-status-bar"
import { useEffect } from "react"
import { SafeAreaProvider } from "react-native-safe-area-context"

import { linking } from "@/navigation/linking"
import { RootNavigator } from "@/navigation/RootNavigator"
import { ensureChannel } from "@/lib/push"
import { navigationTheme } from "@/theme"

export default function App() {
  useEffect(() => {
    // Idempotent, and cheap. The channel must exist before the first
    // notification arrives or Android drops it into a default channel the
    // user cannot tune separately.
    void ensureChannel()
  }, [])

  return (
    <SafeAreaProvider>
      <NavigationContainer theme={navigationTheme} linking={linking}>
        <StatusBar style="light" />
        <RootNavigator />
      </NavigationContainer>
    </SafeAreaProvider>
  )
}
