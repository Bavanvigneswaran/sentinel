/**
 * Deep links, and the one thing they exist for: tapping an alert notification
 * should land on the Alerts tab, not on whatever screen the app was last left
 * on.
 *
 * The backend's FCM payload carries `data.url` (see app/alerts/notify.py's
 * `_send_fcm`) in exactly the same shape the Web Push payload already used —
 * "/alerts" — so one notification builder serves both channels. Mapping it to
 * a route goes through lib/deepLinks.ts's allow-list rather than being trusted
 * as-is; the backend knows nothing about this app's URL scheme.
 */

import * as Notifications from "expo-notifications"
import type { LinkingOptions } from "@react-navigation/native"

import { APP_SCHEME, appUrlForPath } from "@/lib/deepLinks"
import type { RootStackParamList } from "@/navigation/types"

function toAppUrl(response: Notifications.NotificationResponse | null): string | null {
  const data = response?.notification.request.content.data as { url?: unknown } | undefined
  return appUrlForPath(data?.url)
}

export const linking: LinkingOptions<RootStackParamList> = {
  prefixes: [APP_SCHEME],
  config: {
    screens: {
      Tabs: {
        screens: {
          Fleet: "fleet",
          Devices: "devices",
          Alerts: "alerts",
          More: "more",
        },
      },
      Device: "devices/:deviceId",
      Live: "devices/:deviceId/live",
      // Settings moved off the tab bar behind More, so its mapping moved with
      // it. Left under Tabs it would resolve to nothing and a push tapped from
      // the notification shade would land on whatever was already open.
      Settings: "settings",
      Anomalies: "anomalies",
      Forecasts: "forecasts",
      Incidents: "incidents",
      Reports: "reports",
    },
  },

  /** Cold start: the app was launched by tapping a notification. */
  async getInitialURL() {
    return toAppUrl(await Notifications.getLastNotificationResponseAsync())
  },

  /** Warm: a notification tapped while the app was already running. */
  subscribe(listener) {
    const subscription = Notifications.addNotificationResponseReceivedListener((response) => {
      const url = toAppUrl(response)
      if (url) listener(url)
    })
    return () => subscription.remove()
  },
}
