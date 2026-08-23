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

/** The command that checks the download matches what the server published. */
export function verifyCommand(build: AgentBuild): string {
  if (build.os === "windows") {
    return `Get-FileHash -Algorithm SHA256 .\\${build.filename}`
  }
  if (build.os === "macos") {
    return `shasum -a 256 ${build.filename}`
  }
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
    "Open the Sentinel app and sign in with your account.",
    'From the device list, choose "Monitor this device" — the app enrols itself with a one-tap code it mints internally. There is nothing to type.',
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
