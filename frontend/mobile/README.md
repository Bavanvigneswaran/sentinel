# Sentinel — Android viewer (Phase 10a)

The phone half of Sentinel: sign in, see your fleet's health, watch one machine
stream live, and triage alerts. It talks to the **same backend API and viewer
WebSocket the web console uses**, with no endpoints of its own beyond the FCM
token registration Phase 10a added.

This is the **viewer** only. Android-as-a-monitored-device — the Kotlin
foreground-service collector — is Phase 10b and is not started.

Expo SDK 57 / React Native 0.86, a **dev build** (not Expo Go): `expo-dev-client`
is what lets Phase 10b add a native module without changing how this is run.

## What's here

| Screen | Backend it renders |
|---|---|
| Sign in / sign up | `POST /auth/login`, `/auth/signup`, `/auth/refresh` |
| Fleet | `GET /fleet/overview` (30s poll) |
| Devices | `GET /devices` (10s poll) |
| Device | `GET /devices/{id}/summary` (15s poll) |
| Live | `POST /ws/tickets` → `WS /ws/viewer`, primed by `GET /devices/{id}/samples/recent` |
| Alerts | `GET /alerts/events`, `POST /alerts/silences` (10s poll) |
| Settings | `POST`/`DELETE /notifications/fcm/register` |

Alert rules, silence windows beyond a one-hour mute, email and Web Push
channels, reports, incidents, forecasts and the history view all stay in the web
console. This app is deliberately the subset you want *on a phone*.

## Running it

```bash
make install          # from the repo root; includes `cd mobile && npm install`
```

Point the app at a backend the **device** can reach. There is no Vite proxy
here, so the URL is absolute and carries **no `/api` prefix** — FastAPI mounts
`/auth`, `/devices` and `/fleet` at the root, and the web app's `/api` exists
only because the dev proxy strips it again.

```bash
cp frontend/mobile/.env.example frontend/mobile/.env
```

| Where you're running | `EXPO_PUBLIC_API_URL` |
|---|---|
| Android emulator | `http://10.0.2.2:8000` (the default) |
| Physical phone on your LAN | `http://192.168.x.x:8000` |
| Phone hotspot (see below) | `http://10.x.x.x:8000` |
| Deployed | `https://sentinel.example.com` |

### The address is baked in, so pin it

This value is **compiled into the APK**, twice: Expo inlines it into the JS
bundle, and `withDevBackendCleartext.js` (below) writes its host into a
Network Security Config. Neither can be changed on the phone — there is no
server-URL field in Settings, deliberately, because one would fix only the
first of those two and the OS would still block the request.

The practical consequence is that a release APK is only valid for as long as
the backend keeps the address it was built against, and **a laptop's LAN IP
changes every time it joins a different network**. An APK built at the office
simply cannot reach the same laptop at home, and the symptom is the app's own
"Could not reach the backend at ..." — accurate, and easy to misread as the
build being out of date when it is the *address* that went stale.

The cheapest fix for local development is to stop letting the address move:
run the laptop on the **phone's own hotspot** and build against whatever
address it hands out. That address comes from the hotspot's DHCP server, which
runs on the phone, so it does not depend on how the phone reaches the internet
— cellular data or a bridged WiFi network both give the laptop the same
address. Check it with `ipconfig getifaddr en0` (macOS) while connected, then
put it in `.env` and rebuild once.

It also sidesteps a second problem: a phone and a laptop on the same hotspot
are alone on a small private network, whereas home and campus WiFi frequently
block device-to-device traffic outright, which no amount of correct
configuration will get around.

The real answer, when there is one, is to stop using a bare IP: an `https://`
backend on a real domain never moves and needs no cleartext exception at all.

Over plain `http`, the backend must be running with `COOKIE_SECURE=false` — the
refresh cookie is otherwise never stored and every app restart logs you out.
The backend also needs to be listening on more than loopback for a physical
phone (or the emulator, over its real network path rather than the `10.0.2.2`
alias) to reach it at all: `uvicorn app.main:app --host 0.0.0.0 --port 8000`,
not the bare `--port 8000` `make dev-backend` runs by default.

**A *release* build additionally needs a cleartext exception for that host.**
Android has blocked plaintext HTTP by default since API 28. The RN/Expo
template only re-enables it for the **debug** build variant
(`android/app/src/debug/AndroidManifest.xml`'s `usesCleartextTraffic="true"`),
so `expo run:android`/`make mobile-android` have always worked over `http://`
and silently said nothing about why a *release* APK — built by
`make mobile-apk` — would not. `plugins/withDevBackendCleartext.js` fixes this
by generating a Network Security Config scoped to exactly
`EXPO_PUBLIC_API_URL`'s host, read at `expo prebuild` time — never a blanket
`usesCleartextTraffic="true"`, which would silently accept plaintext to *any*
host the app ever talks to. A `https://` backend needs no exception and gets
none. This means **`EXPO_PUBLIC_API_URL` must be correct at prebuild time**,
not just at runtime: changing it means `npx expo prebuild --platform android
--clean` again before `assembleRelease`, or the exception is scoped to the
wrong host and the release build cannot reach the (new) one.

Then, with an emulator running or a phone attached:

```bash
make mobile-android
```

That builds, installs and launches the dev build (first run compiles the Android
project and takes several minutes). Afterwards, `make mobile` just starts Metro
against the already-installed build.

`make mobile-prebuild` regenerates `android/` from `app.config.ts` — needed
after changing a config plugin, the package name, or adding
`google-services.json`. `android/` is generated and gitignored; do not edit it
by hand.

## Push notifications (FCM)

The app obtains a **native FCM registration token** via `expo-notifications`'
`getDevicePushTokenAsync()` and registers it with the backend; the backend sends
through FCM HTTP v1. Nothing goes through Expo's push service.

Three things must line up, and each one missing degrades rather than breaks:

1. **`frontend/mobile/google-services.json`** — from the Firebase console, for an Android
   app whose package name is `com.sentinel.viewer`. Without it, `app.config.ts`
   omits `googleServicesFile` entirely (so the build still works) and Settings
   says push is unavailable instead of offering a dead toggle.
2. **`FCM_PROJECT_ID` and `FCM_SERVICE_ACCOUNT_FILE`** in the backend's `.env` —
   Firebase console → Project settings → Service accounts → Generate new private
   key. Without them the backend logs "fcm suppressed" and sends nothing, the
   same posture it takes for unset SMTP or VAPID.
3. **A device with Google Play services.** A bare emulator image cannot obtain a
   token at all; use a `google_apis_playstore` image or a real phone.

Registration *is* the opt-in — there is no `fcm_enabled` setting beside the
web-push one, because a flag saying "on" with no device token registered would
be a lie the UI would happily render. Signing out unregisters this phone.

## Tests and checks

```bash
npm test        # node --test over the pure logic (ring buffer, chart frame, deep links,
                # per-platform readings grid)
npm run lint    # oxlint, same config as frontend/web/
npm run typecheck
```

The tests deliberately cover the pure modules only — the parts where a silent
wrong answer is plausible: that a `null` reading becomes a **gap** in a chart
and never a zero, that the y-scale follows the visible window, and that a push
payload's `url` is matched against an allow-list rather than navigated to on
trust. They run in plain Node with no React Native runtime, no jest, and no
transform.

## Relationship to `frontend/web/`

`src/types/` is copied byte-for-byte from `frontend/web/src/types/`; the backend's
Pydantic schemas are the single source of truth and both clients hand-write
against them. `lib/api.ts`, `lib/liveSocket.ts`, `stores/auth.ts`,
`lib/ringBuffer.ts` and `lib/streamBuffers.ts` are ports kept deliberately
recognisable against their web counterparts, so a fix to one is obviously
applicable to the other. What genuinely differs is documented in each file's
header — mostly: no `window`, no DOM, and an app that gets suspended.


## `modules/sentinel-collector` — the phone as a monitored device

Everything above is the app as a **viewer** of other machines. Phase 10b added
the other half: a Kotlin foreground service that reports *this phone's own*
metrics over the same agent protocol as the Python desktop agent.

It is a local Expo module, autolinked from `modules/` on the native side and
mapped by name in `metro.config.js` + `tsconfig.json` on the JS side. The
TypeScript surface is a remote control only — `enroll`, `start`, `stop`,
`status` — and nothing in it is on the data path. That is the whole point: the
JS runtime is torn down when the app is backgrounded and gone entirely when it
is closed, which is exactly when monitoring has to continue.

```bash
make mobile-collector-test   # JVM unit tests; no emulator needed
make mobile-collector-logs   # follow the service on a connected device
```

The unit tests cover the parts where a wrong answer would be silent rather than
loud: window aggregation and its null handling, the batching rule that stops a
10s sample being relabelled a 1s one across a live upshift, counter
differentiation across a reboot, `/proc` parsing (including rejecting the
emulator's stub CPU frequency), and the JSON encoding rule that an unmeasurable
field is an explicit `null` and not a missing key.

**What the phone can honestly measure is decided in `docs/ANDROID_METRICS.md`,
not here.** Read it before touching a collector. The short version: memory,
storage, battery, temperature, network throughput, latency and uptime are real;
CPU of any kind is not readable by any app since API 26 and is never attempted.

To try it: sign in, then **Settings → Monitor this phone**. The app mints a
short-lived enrollment code with your own session and hands it to the native
module, which redeems it for an opaque device-scoped agent token sealed in the
Android Keystore. The collector never sees your access token.
