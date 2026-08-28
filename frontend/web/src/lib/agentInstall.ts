/**
 * The per-platform truth about installing an unsigned binary.
 *
 * ARCHITECTURE.md's Known Constraints have said since Phase 0 that unsigned
 * agent binaries trip SmartScreen and Gatekeeper. Phase 11's job is to make
 * the download page say that *before* the click rather than leave the user
 * alone with a scary dialog and no idea whether they have been handed malware.
 *
 * Pure string builders, kept out of the page so they can be tested — the exact
 * OS wording matters, since it is what the user matches against what they see.
 */

import type { AgentBuild, BuildOs } from "@/types/downloads"

export const OS_LABEL: Record<BuildOs, string> = {
  macos: "macOS",
  linux: "Linux",
  windows: "Windows",
  android: "Android",
}

export const ARCH_LABEL: Record<AgentBuild["arch"], string> = {
  arm64: "Apple Silicon / ARM64",
  x64: "Intel / AMD64",
  universal: "Universal",
}

/**
 * The architecture label for one build, which is not the same question as the
 * architecture label in the abstract.
 *
 * ARCH_LABEL is written for the desktop chooser, where "arm64" needs to be
 * recognisable as the thing Apple sells and "x64" as the thing Intel and AMD
 * do — that phrasing is what makes the ambiguous-Mac card readable. Applied to
 * the APK it produced "Android · Apple Silicon / ARM64", which is simply
 * false. A phone's architecture is also not a choice anyone is making here:
 * there is one APK.
 */
export function archLabelFor(build: AgentBuild): string {
  if (build.os === "android") return build.arch === "arm64" ? "ARM64" : ARCH_LABEL[build.arch]
  return ARCH_LABEL[build.arch]
}

export interface UnsignedWarning {
  /** Quoted from the OS, so the user can match it to the dialog in front of them. */
  dialog: string
  steps: string[]
}

/**
 * What the OS will actually say, and how to get past it.
 *
 * Returns null for a signed build and for Linux, which has no equivalent
 * gate — a downloaded ELF simply needs the execute bit.
 */
export function unsignedWarning(build: AgentBuild): UnsignedWarning | null {
  if (build.signed) return null

  if (build.os === "macos") {
    return {
      dialog:
        '"sentinel-agent" cannot be opened because it is from an unidentified developer.',
      steps: [
        "Run it once from Terminal, then approve it in System Settings → Privacy & Security → Open Anyway.",
        `Or clear the quarantine flag yourself: xattr -d com.apple.quarantine ${build.filename}`,
        "Check the SHA-256 above before doing either. Both steps tell macOS to trust a file it cannot vouch for, so the checksum is the only thing standing in for a signature.",
      ],
    }
  }

  if (build.os === "windows") {
    return {
      dialog: "Windows protected your PC — Microsoft Defender SmartScreen prevented an unrecognised app from starting.",
      steps: [
        'Choose "More info", then "Run anyway".',
        "Check the SHA-256 above first — SmartScreen is flagging that this binary has no reputation, and the checksum is what replaces that reputation here.",
      ],
    }
  }

  return null
}

/**
 * The command that checks the download matches what the server published.
 *
 * Keyed on the machine the command will be **typed on**, which is not the same
 * question as which machine the build is for. The download lands in this
 * browser, so the checksum is taken here — and for the Android APK those are
 * never the same machine, since there is no shell on the phone to run it in.
 * Keying it on `build.os` handed a Mac user `sha256sum app-release.apk`, a
 * command macOS does not ship (`shasum -a 256` is its equivalent). Seen on the
 * real page.
 *
 * `hostOs` is the browser's detected platform. It falls back to the build's own
 * OS when detection failed, which is the right guess for a desktop agent — the
 * common case is downloading on the machine you are about to enrol.
 */
export function verifyCommand(build: AgentBuild, hostOs?: BuildOs | null): string {
  const os = hostOs ?? build.os
  if (os === "windows") {
    return `Get-FileHash -Algorithm SHA256 .\\${build.filename}`
  }
  if (os === "macos") {
    return `shasum -a 256 ${build.filename}`
  }
  // Linux, and an Android phone browsing this page — which cannot run any of
  // these, but has no better answer and is not where the file is checked.
  return `sha256sum ${build.filename}`
}

/**
 * True for the platforms that install through a CLI (`enroll`, `install-service`,
 * `status`). Android has none of that — it is an app, not a binary you invoke.
 * Enrolment is a tap inside the app, and there is no shell to chmod anything
 * in or run a status command from. `DownloadPage` branches its install-steps
 * block on this rather than routing Android through `installSteps()`, which
 * would otherwise render `./sentinel-*.apk enroll --code …` — a command that
 * cannot be typed anywhere on a phone.
 */
export function isCliInstall(build: AgentBuild): boolean {
  return build.os !== "android"
}

/**
 * Is this a desktop *agent* — a binary you install on the machine being
 * watched — as opposed to the Android app?
 *
 * The distinction is the whole reason the download page has two sections. A
 * macOS/Linux/Windows build is an agent and nothing else: it has no UI, it is
 * not how you look at anything, and it is useless without this console. The
 * Android APK is the console *and* the agent in one — you sign into it, browse
 * your fleet from it, and optionally let it report itself. Listing it under
 * "Install an agent" alongside the three binaries invited the reasonable
 * conclusion that a phone needs an agent installed onto it, which it does not.
 */
export function isDesktopAgent(build: AgentBuild): boolean {
  return build.os !== "android"
}

/** Getting from a downloaded file to a running, enrolled agent (desktop OSes — see isCliInstall). */
export function installSteps(build: AgentBuild, code = "<YOUR-CODE>"): string[] {
  if (build.os === "windows") {
    return [
      `.\\${build.filename} enroll --code ${code}`,
      `.\\${build.filename} install-service`,
      `.\\${build.filename} status`,
    ]
  }
  return [
    `chmod +x ${build.filename}`,
    `./${build.filename} enroll --code ${code}`,
    `./${build.filename} install-service`,
    `./${build.filename} status`,
  ]
}

/**
 * The Android equivalent of installSteps(): plain steps, not shell commands,
 * because there is no shell. No code is pasted from the Devices page either —
 * the signed-in app mints its own short-lived one internally and hands it
 * straight to the collector, per Phase 10b.
 */
export function androidInstallSteps(): string[] {
  return [
    "Transfer the APK to your phone (email, a cable, or a private link — not the Play Store).",
    "On the phone: allow installs from this source when prompted, then open the APK to install it.",
    "Open the Sentinel app and sign in with your account. That alone makes it a viewer for every machine you have already enrolled.",
    'To have the phone report itself too: More → Settings → "Monitor this phone". The app mints a one-tap code internally and hands it to its own collector — there is nothing to type, and nothing to paste from this page.',
  ]
}

/** What `install-service` (or, on Android, opening the app) will actually register. */
export const SERVICE_MECHANISM: Record<BuildOs, string> = {
  macos: "a per-user launchd LaunchAgent (no admin password needed)",
  linux: "a systemd user unit, or --scope system for a machine-wide one",
  windows: "a Task Scheduler task, not a services.msc entry — see the install docs for why",
  android: "a foreground service the app starts on tap — no separate install-service step",
}

/**
 * Which builds to offer someone on `os`.
 *
 * When the architecture could not be determined — which is *always* the case
 * for a Mac, because Safari and Chrome both report "Intel Mac OS X" on Apple
 * Silicon — every build for the OS is returned rather than a guess. Handing
 * half of all Mac users a binary that cannot run is a worse outcome than
 * asking them which Mac they have.
 */
export function buildsFor(
  builds: AgentBuild[],
  /** A DetectedOs, which includes values no build can ever have — "ios". */
  os: string | null,
  arch: string | null,
  archCertain: boolean,
): AgentBuild[] {
  if (!os) return []
  const forOs = builds.filter((b) => b.os === os)
  if (!archCertain || !arch) return forOs

  const exact = forOs.filter((b) => b.arch === arch || b.arch === "universal")
  // Never return nothing just because the exact arch is missing: the page can
  // say "not this architecture" only if it has the others to show.
  return exact.length > 0 ? exact : forOs
}

/** Where the user has to type, named the way their OS names it. */
export function shellFor(build: AgentBuild): string {
  return build.os === "windows" ? "PowerShell" : "Terminal"
}

export interface EnrollStep {
  command: string
  /** Why this line exists. Shown under it — a command with no explanation is
   * a thing to paste rather than a thing to understand. */
  note: string
}

/** Stands in for a real code before one has been minted. Deliberately not a
 * plausible-looking fake code: somebody who pastes this verbatim should get
 * an obvious error, not a puzzling rejection. */
export const CODE_PLACEHOLDER = "PASTE-YOUR-CODE-HERE"

/**
 * The address an enrolled agent should push to, as seen from another machine.
 *
 * Under `make serve` — the only command that produces something reachable from
 * another machine — the console, the REST API and the viewer socket are one
 * origin, so this is simply where the browser already is.
 *
 * Under `make dev-frontend` it is not: the origin is Vite on :5173, which
 * forwards only `/api` and `/ws` to the backend, and the agent posts to
 * `/enroll` with no prefix (Phase 1's "routes carry no /api prefix"). Printing
 * the origin there would hand out a command that cannot work. The port is
 * swapped for the proxy's own target — a constant read from vite.config.ts,
 * not a guess — so the local case at least stays correct.
 */
export function agentServerOrigin(origin: string, isDev: boolean): string {
  if (!isDev) return origin
  try {
    const url = new URL(origin)
    url.port = "8000"
    // No trailing slash: it is pasted into a shell command, not fetched.
    return url.origin
  } catch {
    return origin
  }
}

/**
 * Mirrors the agent's own `is_loopback()`/`requires_tls()` in config.py:
 * plaintext to this same machine is fine, plaintext to anywhere else needs
 * an explicit opt-in. There is no deployed TLS anywhere in this project
 * (`make serve` is LAN-only http), so this is true for the realistic case —
 * enrolling a *different* machine than the one browsing this page — every
 * time, and false only when the two happen to be the same box.
 */
function needsInsecureFlag(serverOrigin: string): boolean {
  let url: URL
  try {
    url = new URL(serverOrigin)
  } catch {
    return true
  }
  if (url.protocol === "https:") return false
  return url.hostname !== "localhost" && !url.hostname.endsWith(".localhost") &&
    url.hostname !== "127.0.0.1" && url.hostname !== "::1"
}

/**
 * The whole run, from a file in the downloads folder to an enrolled service —
 * as commands the user can copy one at a time, with the real enrollment code
 * already substituted in.
 *
 * This is `installSteps()` plus the two things that made a user's first
 * attempt fail even though every command shown was correct: the binary is in
 * their downloads folder and the shell is not, and on macOS an unsigned,
 * freshly-downloaded file carries a quarantine flag that Gatekeeper enforces
 * before the process ever starts. Both were previously prose in a warning box
 * further down the page rather than a line in the sequence.
 *
 * `serverOrigin` is the address the *enrolled* machine should push to —
 * `window.location.origin`, since `make serve` puts the console and the API
 * on one origin. Without an explicit `--server` the binary defaults to
 * `http://localhost:8000`, which is wrong the moment the machine running the
 * command is not this one — the common case this page exists for. `--insecure`
 * is added alongside it whenever that origin is plaintext and not loopback,
 * which it always is until a deployment puts TLS in front of the backend.
 */
export function enrollmentPlan(
  build: AgentBuild,
  code: string | null,
  serverOrigin: string,
): EnrollStep[] {
  const value = code ?? CODE_PLACEHOLDER
  const file = build.filename
  const insecure = needsInsecureFlag(serverOrigin)
  // `--server` is a **global** option and must precede the subcommand, exactly
  // as `--config` must (see tests/test_service_units.py, which parses rendered
  // service commands back through the real parser for this reason). Placed
  // after `enroll` argparse rejects the whole line with "unrecognized
  // arguments" — verified against build_parser() itself. `--insecure` is the
  // opposite: it belongs to the enroll subparser and must come after.
  const globalFlags = `--server ${serverOrigin}`
  const enrollArgs = `enroll --code ${value}${insecure ? " --insecure" : ""}`
  const enrollNote =
    `Redeems the code for this machine's own revocable token against ${serverOrigin}. ` +
    "The code is single-use and expires; your password is never involved. The address is " +
    "written into the agent's config here, which is why none of the commands below repeat it." +
    (insecure
      ? " --insecure is required because that server has no TLS certificate — the code and the " +
        "token it becomes both travel in cleartext, so only do this on a network you trust."
      : "")
  const rerunNote =
    "Only needed if you skip install-service, or stop it: that already reconnects the agent " +
    "automatically after a dropped connection or a reboot. Run this yourself to bring it back " +
    "up in the foreground — after a connection drop, or once the machine is back on after being " +
    "shut down."

  if (build.os === "windows") {
    return [
      {
        command: `cd $HOME\\Downloads`,
        note: "Wherever your browser put the file — adjust if you saved it elsewhere.",
      },
      {
        command: `.\\${file} ${globalFlags} ${enrollArgs}`,
        note: enrollNote,
      },
      {
        command: `.\\${file} install-service`,
        note: `Registers ${SERVICE_MECHANISM.windows}, so it keeps reporting after a reboot.`,
      },
      {
        command: `.\\${file} status`,
        note: "Confirms it is enrolled and connected. The device appears on Devices within a few seconds.",
      },
      {
        command: `.\\${file} run`,
        note: rerunNote,
      },
    ]
  }

  const quarantine: EnrollStep[] =
    build.os === "macos" && !build.signed
      ? [
          {
            command: `xattr -d com.apple.quarantine ${file}`,
            note:
              "Clears the quarantine flag macOS puts on every download. Without this Gatekeeper " +
              "blocks an unsigned binary before it starts. Check the SHA-256 below first — that " +
              "checksum is what stands in for the signature this build does not have.",
          },
        ]
      : []

  return [
    {
      command: `cd ~/Downloads`,
      note: "Wherever your browser put the file — adjust if you saved it elsewhere.",
    },
    { command: `chmod +x ${file}`, note: "A downloaded binary arrives without the execute bit." },
    ...quarantine,
    {
      command: `./${file} ${globalFlags} ${enrollArgs}`,
      note: enrollNote,
    },
    {
      command: `./${file} install-service`,
      note: `Registers ${SERVICE_MECHANISM[build.os]}, so it keeps reporting after a reboot.`,
    },
    {
      command: `./${file} status`,
      note: "Confirms it is enrolled and connected. The device appears on Devices within a few seconds.",
    },
    {
      command: `./${file} run`,
      note: rerunNote,
    },
  ]
}
