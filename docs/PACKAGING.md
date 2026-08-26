# Packaging and distribution

Decisions, and the reasoning behind them. `docs/INSTALL.md` is the user-facing
version; this is the one to read before changing how anything is built.

---

## The trap: PyInstaller does not cross-compile

There is no flag for it and there cannot be one. PyInstaller ships a native
**bootloader** executable per platform and freezes the **host's** CPython,
extension modules and shared libraries. A Mac can produce a Mach-O arm64 binary
and nothing else.

That constraint shapes every other decision here, so it is stated once, loudly,
in the three places someone could hit it:

* `agent/build/sentinel-agent.spec` has no target selector, with a comment
  saying why.
* `agent/build/build.py` prints `Target: macos/arm64 — PyInstaller does not
  cross-compile` before it builds anything, and offers no `--target`.
* The Makefile has `agent-build` and deliberately **no** `agent-build-windows`.
  A target that silently only ever works on the machine it was written on is
  worse than no target at all.

### How the other platforms actually get built

**A GitHub Actions matrix, one runner per target** —
`.github/workflows/agent-build.yml`:

| Target | Runner | Why that one |
|---|---|---|
| macos-arm64 | `macos-14` | Apple Silicon |
| macos-x64 | `macos-13` | The last Intel runner |
| linux-x64 | `ubuntu-22.04` | **Pinned to the oldest supported runner.** A PyInstaller binary links against the glibc it was built on and will not run on anything older, so building on `ubuntu-latest` silently drops every user on a stable-release distro. |
| windows-x64 | `windows-2022` | |

Each leg tests, builds, runs `--version` **and `sample`** (only the latter
proves psutil's compiled extension made it into the bundle), and uploads its
binary plus a one-entry `manifest.json`. A final job merges those manifests
with `agent/build/merge_manifests.py`.

`fail-fast: false` and `if: always()` on the merge are deliberate: a three-of-
four matrix publishes three binaries rather than nothing, and the download page
already knows how to say "no Windows build yet".

**Alternatives rejected.** Containers only solve Linux — there is no legitimate
way to run the macOS or Windows toolchains in one. Wine-hosted PyInstaller
produces a Windows binary that is a support nightmare and is not something to
hand a stranger. Neither removes the need for a matrix, so neither is worth
maintaining alongside it.

**Manual fallback**, if CI is unavailable: on each target machine, `pip install
-e "agent[dev,build]"` then `python build/build.py`, and collect the resulting
`dist/` into one directory with `merge_manifests.py`. Same script, same
manifest, no second code path.

### A real bug this uncovered

`sentinel-agent run` called `loop.add_signal_handler(sig, agent.stop)`
unconditionally. Windows's asyncio event loop does not implement that method —
every call raises `NotImplementedError`, unconditionally, on every Windows
install — which would have crashed the agent with a traceback before it ever
connected. Nothing in this session's testing could have caught it: it only
reproduces on a real Windows event loop, and there is no Windows machine here.
It was found by re-reading the code with "would this actually run on Windows"
as the question, not by running it. Fixed with a `try`/`except
NotImplementedError` falling back to `signal.signal()`, which needs its own
care — a signal handler runs outside the event loop and cannot call
`agent.stop` directly, so it hands off via `loop.call_soon_threadsafe()`. This
is the kind of defect this whole document exists to keep from being described
as "should work" instead of "was verified" or "was reasoned through and here
is the reasoning."

### What was actually built and run in this session

One macOS arm64 binary, on this Mac. It was built, run (`--version`, `sample`
against the real machine), served through the API, downloaded, checksum-matched,
installed as a launchd LaunchAgent, observed connecting to the backend and
pushing real telemetry, then uninstalled.

**No Windows or Linux binary has ever been produced.** The spec, the CI matrix
and the systemd/Task Scheduler installers are written and unit-tested — the
renderers are pure precisely so they can be asserted from a Mac — but "the spec
file looks right" is not the same claim as "it builds". Treat the first run of
the matrix as the real test.

---

## The manifest

`agent/build/agent_manifest.py` writes it; `backend/app/services/download_service.py`
reads it. Two programs, two machines, so it carries a `schema_version` and the
reader **validates rather than trusts**: an entry naming an unknown OS or arch,
or a filename that is not a plain filename, is dropped; a bad `schema_version`
takes the whole file out of service with a reason the page can render.

```json
{
  "schema_version": 1,
  "generated_at": "2026-08-23T10:21:01+00:00",
  "builds": [{
    "os": "macos", "arch": "arm64", "version": "0.1.0",
    "filename": "sentinel-agent-0.1.0-macos-arm64",
    "size_bytes": 12841200, "sha256": "1b7828…",
    "signed": false, "signing": "unsigned: SENTINEL_MACOS_SIGN_IDENTITY is not set (Gatekeeper will warn)",
    "built_at": "…", "built_on": "Darwin 25.5.0"
  }]
}
```

`signed: false` is recorded, never omitted. It is what lets the download page
warn the user *before* the click rather than leaving them alone with a
Gatekeeper dialog.

The OS and architecture are in the **filename** as well as the manifest,
because the file outlives the page that served it — somebody will find it in a
Downloads folder in six months and need to know what it is.

---

## Code signing: designed for, not configured

No certificates have been bought (ARCHITECTURE.md's Known Constraints budget an
Apple Developer ID at ~$99/yr and a Windows code-signing certificate). The
design goal was that enabling signing later is **configuration, not a rewrite**:

| Where | Now | When certs exist |
|---|---|---|
| `agent/build/build.py` | already calls `signing.sign()` | unchanged |
| `agent/build/signing.py` | returns `signed=False` with a reason | reads the env vars it already looks for |
| `.github/workflows/agent-build.yml` | passes empty secrets through | populate the repository secrets |
| manifest | `"signed": false` | `true` |
| download page | renders the Gatekeeper/SmartScreen warning | renders nothing |

Build-time environment:

```
SENTINEL_MACOS_SIGN_IDENTITY      "Developer ID Application: … (TEAMID)"
SENTINEL_MACOS_NOTARY_PROFILE     a `notarytool store-credentials` profile
SENTINEL_WINDOWS_SIGN_THUMBPRINT  SHA-1 thumbprint of a cert in the machine store
SENTINEL_WINDOWS_TIMESTAMP_URL    RFC-3161 timestamp server
```

Three decisions inside that are worth keeping:

* **No `SENTINEL_WINDOWS_SIGN_PASSWORD`.** `signtool` will take a `.pfx` and its
  password on the command line, which puts a code-signing key's password into
  the process table and into every CI log that echoes commands. A certificate
  in the machine store addressed by thumbprint — or a cloud signing service —
  is the only form supported.
* **An RFC-3161 timestamp is not optional.** Without it every binary ever
  shipped stops verifying on the day the certificate expires.
* **A notarized bare binary cannot be stapled.** `stapler` only writes into a
  bundle, `.dmg` or `.pkg`. The ticket is real and Gatekeeper checks it
  *online*, so an air-gapped machine still warns. Shipping a `.dmg` later is
  what fixes that, and is a packaging change rather than a signing one.
* **UPX is off.** Packing is a strong heuristic signal to antivirus engines and
  to SmartScreen. Saving 4MB is not worth making a monitoring agent look more
  like malware — especially one that is already unsigned.

---

## Where the config and its token live

The agent token is a long-lived credential on a machine we do not control, so
where the file lands is a security decision, not a tidiness one.

`agent/sentinel_agent/paths.py` resolves it from a **scope**, not from
`Path.home()`. A systemd system unit runs as root and a Windows task registered
`/RU SYSTEM` has its profile under `C:\Windows\System32\config\systemprofile`;
neither is the HOME of the person who typed the enrollment code.

| | user scope | system scope |
|---|---|---|
| macOS | `~/.config/sentinel` | `/Library/Application Support/Sentinel` |
| Linux | `~/.config/sentinel` | `/etc/sentinel` |
| Windows | `%LOCALAPPDATA%\Sentinel` | `%ProgramData%\Sentinel` |

* `SENTINEL_AGENT_HOME` still overrides everything, and `--config` overrides that.
* **Not XDG-aware on Linux/macOS.** `~/.config/sentinel` is what Phase 2 shipped;
  honouring `XDG_CONFIG_HOME` now would silently relocate every existing
  agent's token out from under it.
* **`%LOCALAPPDATA%`, not `%APPDATA%`.** An agent token is scoped to one
  device; roaming it onto another machine would carry a credential that machine
  has no business holding.
* **The installer writes the resolved path into the unit/plist/task**, so the
  running service never re-derives it from an environment it does not have.

### Making it private

`config.save()` creates the file **empty**, locks it down, and only then writes
the token. A failure to restrict must not leave a secret in a world-readable
file — and on Windows that ordering is not merely tidier, it is the only
correct one, because `os.open()` has no meaningful mode argument there.

* POSIX: `chmod 0600`, then re-`stat` and refuse if any group/world bit survived.
* Windows: `icacls <file> /inheritance:r /grant:r *S-1-5-18:(F) *S-1-5-32-544:(F) <user>:(R,W)`.
  `/inheritance:r` is the load-bearing flag — without it the file keeps
  ProgramData's "Users: read" ACE and the token is readable by every account on
  the box. **Well-known SIDs, not the names "SYSTEM" and "Administrators"**,
  which are localised and would not match on a German or French Windows.

`config.save()` writes through the descriptor `paths.open_private()` vetted,
never a path reopened by name — reopening after locking down would let the
entry be swapped for a symlink in between, landing the token on the symlink's
target with the 0600 check having passed against a file no longer there. On
Windows that window is unavoidable (icacls takes a path, not a handle), but the
important half still holds: a failure to restrict never leaves a token on disk,
because the token has not been written yet.

`sentinel-agent status` reports a file that is still group/world readable.
Reading a Windows ACL back properly needs pywin32, so it says nothing there
rather than guessing.

---

## Running as a service

One CLI verb, three mechanisms. Each renderer (`build_plist`, `build_unit`,
`build_task_xml`) is **pure**, so all three are asserted from whatever machine
runs the suite — this project is developed on a Mac, and a Linux unit that is
only inspected by eye is exactly the thing that looks right and fails on the
target. `agent/tests/test_service_units.py` feeds every one of them back
through the real argument parser.

| Platform | Mechanism | Scopes |
|---|---|---|
| macOS | launchd LaunchAgent | user only |
| Linux | systemd unit | user, system |
| Windows | Task Scheduler task | user, system |

### Windows uses Task Scheduler, not the Service Control Manager

This is the one place where the obvious answer is wrong. A Windows *service* is
not merely a background process: the SCM starts it and expects a callback into
`StartServiceCtrlDispatcher` within ~30 seconds. A PyInstaller console binary
does none of that, so `sc.exe create` produces a service that Windows starts,
waits for, declares "did not respond in a timely fashion" (error 1053), and
kills.

Making it a real service means pywin32, a service-entry shim and a second
frozen entry point. That is a legitimate future change — and it must be built
and tested on Windows, which this project cannot do from the machine it is
developed on.

Registration goes through `schtasks /XML`, not `/TR`. `/TR` takes the whole
command as one string and needs nested-quote escaping that breaks on
`C:\Program Files\…`, and more importantly it cannot express the settings that
decide whether the agent keeps running. Task Scheduler's defaults stop a task
when the machine goes on battery and when the idle period ends — an agent that
goes deaf the moment a laptop is unplugged is not an agent. The XML sets
`StopIfGoingOnBatteries=false`, `StopOnIdleEnd=false` and
`ExecutionTimeLimit=PT0S` (the default is three days, after which a healthy
long-running agent is terminated).

The cost is honesty in the docs: `services.msc` will not list the agent, Task
Scheduler will.

Task Scheduler also **discards a task's stdout**. launchd redirects it in the
plist and systemd owns the journal, so Windows would otherwise be the one
platform where a failing agent leaves no record at all — exactly the platform
where it is hardest to debug. The installer therefore passes `--log-file` and
the agent writes its own rotating log (5MB x 3; an unrotated file on a process
that runs forever is its own operational problem).

### macOS has no system scope

Not an oversight. A LaunchDaemon means writing to `/Library/LaunchDaemons` as
root and bootstrapping into the `system` domain — root-requiring code that
cannot be exercised from a test suite or verified without breaking the
developer's own machine. `--scope system` on macOS is an actionable error
pointing at INSTALL.md's hand-written plist instead of blind code shipped as
though it worked.

### systemd hardening

The agent needs no privileges — TCP-connect latency probes instead of raw ICMP
sockets was a Phase 2 decision made exactly so this could be true — so the
system unit runs with an **empty `CapabilityBoundingSet`**. uid 0 without
`CAP_DAC_OVERRIDE` can read its own config and nothing its DAC permissions
forbid, which is what makes `User=root` mean "owns its config file" rather than
"can do anything".

Two entries are load-bearing in the other direction:

* `RestrictAddressFamilies` **must** include `AF_NETLINK`. psutil's per-NIC
  counters go through `getifaddrs()`, which is a netlink socket on Linux.
  Omitting it produces an agent that connects fine and reports no network
  metrics at all.
* `ProtectProc=` is deliberately **not** set. `ProtectProc=invisible` hides
  `/proc/<pid>/` for processes we do not own, which is exactly what the
  top-processes collector reads. A metrics agent is the one service that
  legitimately needs the process table.

A `--scope user` install also needs `loginctl enable-linger`, or the unit stops
when the last session ends — an agent that monitors a machine only while
someone is logged into it is not monitoring. The installer enables it and
**reports whether it worked**, because it can legitimately fail on a headless
box and the user needs to know.

---

## The Android APK

**Decision: it belongs in the distribution story, and no APK is published here.**

Phase 10b's collector is a real agent, so the manifest schema accepts
`os: "android"`, the backend's `KNOWN_OS` includes it, and the download page
renders it like any other platform. `agent/build/register_build.py` adds a
Gradle-built APK to the same manifest — Android is not special-cased anywhere
downstream.

What stops one shipping today is the key. `expo prebuild` generates an
`android/app/build.gradle` whose **release** buildType points at
`signingConfigs.debug` — React Native's *public* debug keystore, the one in
every RN checkout on earth. An APK signed with it lets anybody build an
"update" that Android accepts as the same app and installs straight over it.
That is worse than shipping nothing.

So `plugins/withReleaseSigning.js` (a config plugin, because `frontend/mobile/android/`
is generated and gitignored and hand-edits vanish at the next prebuild) swaps in
a real keystore when one is configured, and `make mobile-apk` **refuses to build
at all** when it is not — never falling back to the debug key with a warning.
Passwords come from the environment, not `-P` gradle properties, for the same
reason `signing.py` refuses a `.pfx` password.

```
keytool -genkeypair -v -keystore sentinel-release.jks -alias sentinel \
        -keyalg RSA -keysize 4096 -validity 10000
export SENTINEL_ANDROID_KEYSTORE=/absolute/path/sentinel-release.jks
export SENTINEL_ANDROID_KEYSTORE_PASSWORD=… SENTINEL_ANDROID_KEY_ALIAS=sentinel
export SENTINEL_ANDROID_KEY_PASSWORD=…
make mobile-apk
```

Note that the app also needs `frontend/mobile/google-services.json` for push, and
`mobile/.env` pointing at a backend the *phone* can reach — neither is in the
repo, and both are already handled by `app.config.ts`'s graceful-absence logic.

---

## Transport

`enroll` **refuses** to send an enrollment code to a remote `http://` server.
Phase 11 is when that stops being advice in ARCHITECTURE.md and becomes a
guard: before it the agent was something you ran against your own dev server;
after it, it is a binary a stranger downloads and points at a URL they typed.
Enrollment is the one exchange carrying both the single-use code and the
long-lived token it becomes.

`--insecure` is the deliberate escape hatch for a LAN server with no
certificate yet. `run` warns rather than refusing, because killing an
already-working monitor over a transport choice its operator has already made
would be the wrong trade — the refusal belongs where the credential is minted.
Loopback is exempt, or every developer would simply turn the guard off.

## Serving the console

`make serve` builds `frontend/web/dist` and the API process serves it — see
`backend/app/webapp.py`. One origin carries the console, the REST API and the
viewer WebSocket, which is what makes "the address in the browser" and
`EXPO_PUBLIC_API_URL` the same string, and removes CORS from a real deployment
entirely.

`make serve` still only binds a LAN interface, though, so its address is only
good on whatever network the host is currently on — and can move without
warning (DHCP renewal, macOS's Private Wi-Fi Address rotating the MAC it
presents per network, a different Wi-Fi entirely). **[Tailscale
Funnel](https://tailscale.com/kb/1223/funnel)** gives the same `make serve`
process a stable public `https://<host>.<tailnet>.ts.net` address without
deploying anywhere: install Tailscale on the one machine running `make serve`
(no client device needs it — Funnel serves ordinary public HTTPS) and run
`tailscale funnel --bg 8000`.

Doing this turns a LAN-local convenience into a public one, so satisfy
`app/config.py`'s `_refuse_insecure_production()` checks for real before
relying on it — set `ENVIRONMENT=prod`, `RATE_LIMIT_ENABLED=true`, a fresh
`JWT_SECRET`, `COOKIE_SECURE=true` (only possible once there is real TLS in
front, which Funnel provides), and rotate the `sentinel_app` role's password
with `ALTER ROLE` directly against Postgres — migration `0001`'s `CREATE ROLE`
is a one-time idempotent bootstrap, so editing `.env` alone leaves the
database still expecting the old password. Also add `--proxy-headers
--forwarded-allow-ips=127.0.0.1` to the `uvicorn` invocation: Funnel proxies
locally to `127.0.0.1`, and without that flag `app/api/ratelimit.py`'s
`_client_ip()` — which deliberately trusts only `request.client.host`, never a
bare `X-Forwarded-For` — would see every visitor as the same loopback address
and rate-limit them all as one shared bucket.

Two decisions there are worth keeping:

* **`/api` is stripped by middleware, not by a second routing convention.**
  Phase 1's invariant is that FastAPI mounts `/auth` and friends at the root and
  that a production reverse proxy strips `/api` exactly as the Vite dev proxy
  does. With no proxy in front, `ApiPrefixMiddleware` *is* that rewrite. It is
  pure ASGI rather than a Starlette HTTP middleware because it must also cover
  the `websocket` scope.
* **The console is served by content negotiation, not a catch-all route.** The
  obvious implementation cannot work: `/devices` is simultaneously a REST
  endpoint (the Android app calls it) and a page in the console's client-side
  router. A catch-all never sees it — the real route matches first, so a
  browser typing that URL gets the API's 401 JSON. Blocking API prefixes from
  the catch-all breaks the mirror image, 404ing a hard refresh on `/devices`.
  `WebConsoleMiddleware` splits on `Sec-Fetch-Mode: navigate` instead, which
  every current browser sends on a top-level navigation and no `fetch`/`XHR`
  ever does. Real files under `dist/` are served whatever asked for them — the
  bundle's own `<script>` is a subresource, not a navigation, and gating that
  on the same header served 404s for the console's JS and CSS and rendered a
  blank page.

`SERVE_WEB_CONSOLE=false` turns it off for a deployment putting a CDN or a real
reverse proxy in front of the static files.

## The download page

`AGENT_DIST_DIR` unset is a fully supported state. The page then says no build
exists and gives the from-source command, rather than offering a link that
404s — the same posture the backend already takes for unset SMTP, VAPID and
FCM.

`AGENT_DOWNLOAD_BASE_URL` moves the binaries to a release host or CDN while the
manifest is still read locally. Streaming multi-megabyte files out of the app
process is fine for a handful of installs and wrong at any scale.

Both endpoints are **authenticated**. The binary is not a secret, but making it
public would add a second unauthenticated route beside `/enroll` — which this
codebase is careful to keep as the only one — for no gain: an agent is useless
without an enrollment code, and minting one requires signing in.

### A Mac's architecture is not detectable from a browser

Safari and Chrome both report `Intel Mac OS X 10_15_7` on Apple Silicon,
permanently, for compatibility. `frontend/web/src/lib/platform.ts` therefore returns
`archCertain: false` and the page offers **both** Mac builds with an
explanation, rather than guessing and handing half of all Mac users a binary
that cannot run. Chromium's `navigator.userAgentData.getHighEntropyValues()`
resolves this when available (it does in Chrome; it does not in Safari or
Firefox), and the page narrows to one build when it does.
