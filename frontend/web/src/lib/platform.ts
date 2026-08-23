/**
 * Working out which agent build a visitor needs.
 *
 * The honest part of this module is `archCertain`. A browser on an Apple
 * Silicon Mac reports `Intel Mac OS X 10_15_7` in its user agent — Safari and
 * Chrome both do, permanently, for compatibility — so the architecture of a
 * Mac is not derivable from the string at all. Rather than guessing and
 * handing half of all Mac users a binary that will not run, detection says so
 * and the page offers both.
 *
 * Pure and string-in/object-out so it can be tested without a DOM; see
 * `__tests__/platform.test.ts`.
 */

export type DetectedOs = "macos" | "linux" | "windows" | "android" | "ios"

export type DetectedArch = "x64" | "arm64"

export interface DetectedPlatform {
  /** null when the user agent is one we have no agent for and cannot name. */
  os: DetectedOs | null
  arch: DetectedArch | null
  /**
   * False when `arch` is a default rather than a reading. The page must offer
   * every architecture for this OS when this is false.
   */
  archCertain: boolean
  /** Shown to the user, so they can tell whether we guessed right. */
  label: string
}

/** The subset of `navigator.userAgentData` we can use, if the browser has it. */
export interface UserAgentDataLike {
  platform?: string
  architecture?: string
}

const UNKNOWN: DetectedPlatform = {
  os: null,
  arch: null,
  archCertain: false,
  label: "an unrecognised system",
}

function archFromUaData(uaData?: UserAgentDataLike): DetectedArch | null {
  // Chromium's high-entropy hints are the only reliable source for a Mac's
  // architecture, and they are only present after getHighEntropyValues()
  // resolves. Firefox and Safari never provide them.
  switch (uaData?.architecture) {
    case "arm":
      return "arm64"
    case "x86":
      return "x64"
    default:
      return null
  }
}

export function detectPlatform(
  userAgent: string,
  uaData?: UserAgentDataLike,
): DetectedPlatform {
  const ua = userAgent ?? ""
  const hinted = archFromUaData(uaData)

  // Android before Linux: every Android user agent also contains "Linux".
  if (/Android/i.test(ua)) {
    return { os: "android", arch: "arm64", archCertain: true, label: "Android" }
  }

  if (/iPhone|iPad|iPod/i.test(ua) || (/Macintosh/.test(ua) && /Mobile/.test(ua))) {
    // iPadOS reports itself as a Macintosh with "Mobile" in the string.
    return { os: "ios", arch: null, archCertain: false, label: "iOS or iPadOS" }
  }

  if (/Windows NT/i.test(ua)) {
    const arm = /ARM64|aarch64/i.test(ua)
    const arch = hinted ?? (arm ? "arm64" : "x64")
    return {
      os: "windows",
      arch,
      // Windows on ARM usually reports x64 for compatibility too, so an
      // unhinted reading is a default, not a measurement.
      archCertain: hinted !== null || arm,
      label: "Windows",
    }
  }

  if (/Mac OS X|Macintosh/i.test(ua)) {
    return {
      os: "macos",
      arch: hinted,
      archCertain: hinted !== null,
      label: "macOS",
    }
  }

  if (/Linux|X11|CrOS/i.test(ua)) {
    const arm = /aarch64|arm64/i.test(ua)
    const intel = /x86_64|Win64|x64/i.test(ua)
    const arch = hinted ?? (arm ? "arm64" : intel ? "x64" : null)
    return {
      os: "linux",
      arch,
      archCertain: hinted !== null || arm || intel,
      label: "Linux",
    }
  }

  return UNKNOWN
}

/** Read the current browser. Separated so `detectPlatform` stays pure. */
export function detectCurrentPlatform(uaData?: UserAgentDataLike): DetectedPlatform {
  if (typeof navigator === "undefined") return UNKNOWN
  return detectPlatform(navigator.userAgent, uaData)
}

/**
 * Ask Chromium for the architecture it will not put in the user agent.
 *
 * Resolves to undefined everywhere else, which is the whole reason
 * `archCertain` exists rather than this being a hard requirement.
 */
export async function readArchitectureHint(): Promise<UserAgentDataLike | undefined> {
  const uaData = (
    navigator as Navigator & {
      userAgentData?: {
        getHighEntropyValues?: (hints: string[]) => Promise<UserAgentDataLike>
      }
    }
  ).userAgentData

  if (!uaData?.getHighEntropyValues) return undefined
  try {
    return await uaData.getHighEntropyValues(["architecture"])
  } catch {
    // Permissions-Policy can block this. Not knowing is a supported state.
    return undefined
  }
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  const mb = bytes / 1_048_576
  return mb >= 1 ? `${mb.toFixed(1)} MB` : `${(bytes / 1024).toFixed(0)} KB`
}
