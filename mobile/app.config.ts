import { existsSync } from "node:fs"
import { join } from "node:path"

import type { ExpoConfig } from "expo/config"

/**
 * Expo config as code, not app.json, for exactly one reason: the Firebase
 * service file.
 *
 * `android.googleServicesFile` must point at a file that actually exists or
 * `expo prebuild` fails outright — and this repo does not ship one, because it
 * carries a real Firebase project's identifiers. Naming it unconditionally
 * would mean nobody could build the app at all until they had a Firebase
 * project, which is the same "unconfigured must degrade, not explode" posture
 * the backend already takes for SMTP, VAPID and ANTHROPIC_API_KEY. Drop
 * `google-services.json` in beside this file and FCM lights up; leave it out
 * and everything except push still builds and runs.
 */
const GOOGLE_SERVICES = join(__dirname, "google-services.json")
const hasFirebase = existsSync(GOOGLE_SERVICES)

const config: ExpoConfig = {
  name: "Sentinel",
  slug: "sentinel",
  scheme: "sentinel",
  version: "0.1.0",
  orientation: "portrait",
  icon: "./assets/icon.png",
  // The web console is a dark ops console; the phone should not flash white.
  userInterfaceStyle: "dark",
  backgroundColor: "#09090b",
  ios: {
    supportsTablet: true,
    bundleIdentifier: "com.sentinel.viewer",
  },
  android: {
    package: "com.sentinel.viewer",
    adaptiveIcon: {
      backgroundColor: "#09090b",
      foregroundImage: "./assets/android-icon-foreground.png",
      monochromeImage: "./assets/android-icon-monochrome.png",
    },
    predictiveBackGestureEnabled: false,
    ...(hasFirebase ? { googleServicesFile: "./google-services.json" } : {}),
  },
  plugins: [
    "expo-dev-client",
    // Replaces the generated release buildType's *public* React Native debug
    // keystore with a real one. A no-op unless SENTINEL_ANDROID_KEYSTORE and
    // friends are set — and `make mobile-apk` refuses to build without them,
    // so there is no path that quietly produces a debug-signed distributable.
    "./plugins/withReleaseSigning",
    // Android blocks plain-http:// traffic by default since API 28; the RN
    // template only re-enables it for the *debug* build variant, which is why
    // a release build silently cannot reach EXPO_PUBLIC_API_URL at all until
    // this scopes an exception to exactly that host. A deployed https://
    // backend needs no exception and gets none.
    "./plugins/withDevBackendCleartext",
    // Sharing a downloaded report needs a FileProvider entry in the manifest,
    // which this plugin contributes; without it the share sheet throws on the
    // content:// URI rather than opening.
    "expo-sharing",
    [
      "expo-notifications",
      {
        color: "#3b82f6",
        // Android 13+ gates notifications behind a runtime permission; the
        // plugin adds POST_NOTIFICATIONS to the manifest, src/lib/push.ts asks
        // for it at the moment the user enables the channel rather than on
        // first launch.
        enableBackgroundRemoteNotifications: false,
      },
    ],
  ],
  extra: {
    /** Read by src/config.ts to explain *why* push is unavailable. */
    hasFirebaseConfig: hasFirebase,
  },
}

export default config
