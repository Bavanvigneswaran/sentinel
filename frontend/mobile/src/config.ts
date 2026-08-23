/**
 * Where the backend lives, from the phone's point of view.
 *
 * Two differences from the web client, both consequences of there being no
 * Vite dev proxy on a device:
 *
 * 1. **No `/api` prefix.** FastAPI mounts `/auth`, `/devices`, `/fleet` at the
 *    root; the web app's `/api` exists only because the proxy strips it again
 *    (see CLAUDE.md's Phase 1 invariants). Adding it here would 404 everything.
 * 2. **An absolute origin.** `window.location` does not exist, so the WebSocket
 *    URL is derived from this base rather than from the page it was served by.
 */

import Constants from "expo-constants"

const DEFAULT_API_URL = "http://10.0.2.2:8000"

function normalise(url: string): string {
  return url.replace(/\/+$/, "")
}

/** Set in .env as EXPO_PUBLIC_API_URL; inlined at bundle time by Expo. */
export const API_BASE_URL = normalise(process.env.EXPO_PUBLIC_API_URL ?? DEFAULT_API_URL)

/** ws:// for http://, wss:// for https:// — the same rule the web client
 * applies to window.location.protocol. */
export const WS_BASE_URL = API_BASE_URL.replace(/^http/, "ws")

/** True when app.config.ts found a google-services.json to build against.
 * False means FCM cannot produce a token at all, and Settings says so rather
 * than offering a toggle that silently does nothing. */
export const HAS_FIREBASE_CONFIG: boolean =
  (Constants.expoConfig?.extra as { hasFirebaseConfig?: boolean } | undefined)
    ?.hasFirebaseConfig === true
