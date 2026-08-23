/**
 * The navigator, plus the auth gate. React Navigation's equivalent of
 * web/src/routes.tsx together with ProtectedRoute/PublicOnlyRoute.
 *
 * The gate is structural rather than a redirect: when `status` is not
 * "authenticated" the authenticated tree is not mounted at all, so no screen
 * can ever fire a request that needs a token it does not have. That is the
 * same property ProtectedRoute gives the web app, expressed the way a native
 * navigator wants it.
 */

import { createBottomTabNavigator } from "@react-navigation/bottom-tabs"
import { createNativeStackNavigator } from "@react-navigation/native-stack"
import { ActivityIndicator, StyleSheet, Text, View } from "react-native"

import { AlertsScreen } from "@/screens/AlertsScreen"
import { AuthScreen } from "@/screens/AuthScreen"
import { CollectorScreen } from "@/screens/CollectorScreen"
import { DeviceScreen } from "@/screens/DeviceScreen"
import { DevicesScreen } from "@/screens/DevicesScreen"
import { FleetScreen } from "@/screens/FleetScreen"
import { LiveScreen } from "@/screens/LiveScreen"
import { SettingsScreen } from "@/screens/SettingsScreen"
import { useAuth } from "@/stores/auth"
import { colors, spacing } from "@/theme"
import type { RootStackParamList, TabParamList } from "@/navigation/types"

const Stack = createNativeStackNavigator<RootStackParamList>()
const Tabs = createBottomTabNavigator<TabParamList>()

/** No icon-font dependency: a glyph reads fine at tab size and keeps the
 * bundle to what the app actually needs. Restricted to characters Roboto
 * actually covers — an uncovered one renders as a tofu box, which is how
 * "◧" was caught on the emulator. */
const TAB_GLYPHS: Record<keyof TabParamList, string> = {
  Fleet: "●",
  Devices: "■",
  Alerts: "▲",
  Settings: "⚙",
}

function TabsNavigator() {
  return (
    <Tabs.Navigator
      screenOptions={({ route }) => ({
        headerShown: false,
        tabBarActiveTintColor: colors.primary,
        tabBarInactiveTintColor: colors.mutedDeep,
        tabBarStyle: {
          backgroundColor: colors.card,
          borderTopColor: colors.border,
        },
        tabBarIcon: ({ color }) => (
          <Text style={{ color, fontSize: 16 }}>{TAB_GLYPHS[route.name]}</Text>
        ),
      })}
    >
      <Tabs.Screen name="Fleet" component={FleetScreen} />
      <Tabs.Screen name="Devices" component={DevicesScreen} />
      <Tabs.Screen name="Alerts" component={AlertsScreen} />
      <Tabs.Screen name="Settings" component={SettingsScreen} />
    </Tabs.Navigator>
  )
}

export function RootNavigator() {
  const status = useAuth((s) => s.status)

  if (status === "idle" || status === "bootstrapping") {
    // The refresh-cookie exchange. Rendering the login form during it would
    // flash a sign-in screen at somebody who is already signed in.
    return (
      <View style={styles.loader}>
        <ActivityIndicator color={colors.muted} />
      </View>
    )
  }

  if (status !== "authenticated") return <AuthScreen />

  return (
    <Stack.Navigator
      screenOptions={{
        headerStyle: { backgroundColor: colors.card },
        headerTintColor: colors.foreground,
        headerTitleStyle: { fontSize: 16 },
        contentStyle: { backgroundColor: colors.background },
      }}
    >
      <Stack.Screen name="Tabs" component={TabsNavigator} options={{ headerShown: false }} />
      <Stack.Screen
        name="Device"
        component={DeviceScreen}
        options={({ route }) => ({ title: route.params.deviceName ?? "Device" })}
      />
      <Stack.Screen
        name="Collector"
        component={CollectorScreen}
        options={{ title: "Monitor this phone" }}
      />
      <Stack.Screen
        name="Live"
        component={LiveScreen}
        options={({ route }) => ({ title: route.params.deviceName ?? "Live" })}
      />
    </Stack.Navigator>
  )
}

const styles = StyleSheet.create({
  loader: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: colors.background,
    padding: spacing.xl,
  },
})
