import { describe, expect, it } from "vitest"

import {
  androidInstallSteps,
  buildsFor,
  installSteps,
  isCliInstall,
  unsignedWarning,
  verifyCommand,
} from "@/lib/agentInstall"
import type { AgentBuild } from "@/types/downloads"

function build(overrides: Partial<AgentBuild> = {}): AgentBuild {
  return {
    os: "macos",
    arch: "arm64",
    version: "0.1.0",
    filename: "sentinel-agent-0.1.0-macos-arm64",
    size_bytes: 12_800_000,
    sha256: "a".repeat(64),
    signed: false,
    signing: "unsigned: SENTINEL_MACOS_SIGN_IDENTITY is not set",
    built_at: "2026-08-23T10:00:00+00:00",
    download_url: "/downloads/agent/sentinel-agent-0.1.0-macos-arm64",
    ...overrides,
  }
}

describe("unsignedWarning", () => {
  it("quotes what macOS actually says", () => {
    // The user matches this against the dialog in front of them; paraphrasing
    // it leaves them wondering whether they downloaded something else.
    expect(unsignedWarning(build())?.dialog).toContain("unidentified developer")
  })

  it("quotes what SmartScreen actually says", () => {
    expect(unsignedWarning(build({ os: "windows" }))?.dialog).toContain(
      "Windows protected your PC",
    )
  })

  it("names the file in the quarantine command", () => {
    const warning = unsignedWarning(build())
    expect(warning?.steps.join(" ")).toContain("sentinel-agent-0.1.0-macos-arm64")
  })

  it("says nothing once a build is signed", () => {
    expect(unsignedWarning(build({ signed: true }))).toBeNull()
    expect(unsignedWarning(build({ os: "windows", signed: true }))).toBeNull()
  })

  it("says nothing for Linux, which has no such gate", () => {
    expect(unsignedWarning(build({ os: "linux" }))).toBeNull()
  })
})

describe("verifyCommand", () => {
  it("uses each platform's own tool", () => {
    expect(verifyCommand(build())).toContain("shasum -a 256")
    expect(verifyCommand(build({ os: "linux" }))).toContain("sha256sum")
    expect(verifyCommand(build({ os: "windows" }))).toContain("Get-FileHash")
  })
})

describe("installSteps", () => {
  it("makes a unix binary executable first", () => {
    expect(installSteps(build())[0]).toContain("chmod +x")
  })

  it("does not tell a Windows user to chmod", () => {
    expect(installSteps(build({ os: "windows" })).join(" ")).not.toContain("chmod")
  })

  it("puts the enrollment code where the user can see it", () => {
    expect(installSteps(build(), "X4T9-K2QM-7PDR").join(" ")).toContain("X4T9-K2QM-7PDR")
  })
})

describe("buildsFor", () => {
  const catalog = [
    build({ os: "macos", arch: "arm64", filename: "mac-arm64" }),
    build({ os: "macos", arch: "x64", filename: "mac-x64" }),
    build({ os: "linux", arch: "x64", filename: "linux-x64" }),
  ]

  it("offers both Mac builds when the architecture is unknown", () => {
    // Which is always: Safari and Chrome report Apple Silicon as "Intel".
    // Guessing hands half of all Mac users a binary that will not run.
    expect(buildsFor(catalog, "macos", null, false).map((b) => b.filename)).toEqual([
      "mac-arm64",
      "mac-x64",
    ])
  })

  it("narrows to one once the architecture is actually known", () => {
    expect(buildsFor(catalog, "macos", "arm64", true).map((b) => b.filename)).toEqual([
      "mac-arm64",
    ])
  })

  it("falls back to the OS's other builds rather than showing nothing", () => {
    expect(buildsFor(catalog, "linux", "arm64", true).map((b) => b.filename)).toEqual([
      "linux-x64",
    ])
  })

  it("returns nothing for an OS with no builds", () => {
    expect(buildsFor(catalog, "windows", "x64", true)).toEqual([])
  })

  it("returns nothing when the OS could not be detected at all", () => {
    expect(buildsFor(catalog, null, null, false)).toEqual([])
  })

  it("treats a universal build as matching any architecture", () => {
    const universal = [build({ os: "macos", arch: "universal", filename: "mac-universal" })]
    expect(buildsFor(universal, "macos", "x64", true).map((b) => b.filename)).toEqual([
      "mac-universal",
    ])
  })
})

describe("buildsFor with a platform that has no agent", () => {
  it("returns nothing for iOS rather than throwing on an unknown key", () => {
    // detectPlatform can return "ios", which is not a BuildOs. The page relies
    // on getting an empty list back so it can render the "no agent for iOS"
    // explanation instead of a broken card.
    const catalog = [build({ os: "macos", arch: "arm64", filename: "mac-arm64" })]
    expect(buildsFor(catalog, "ios", null, false)).toEqual([])
  })
})

describe("isCliInstall", () => {
  it("is true for every desktop OS", () => {
    expect(isCliInstall(build({ os: "macos" }))).toBe(true)
    expect(isCliInstall(build({ os: "linux" }))).toBe(true)
    expect(isCliInstall(build({ os: "windows" }))).toBe(true)
  })

  it("is false for Android — there is no shell to run installSteps() in", () => {
    expect(isCliInstall(build({ os: "android" }))).toBe(false)
  })
})

describe("androidInstallSteps", () => {
  it("never tells the user to run a shell command", () => {
    // The bug this guards against: installSteps() rendered
    // "./sentinel-*.apk enroll --code …" for Android, a command that cannot
    // be typed anywhere on a phone.
    const steps = androidInstallSteps().join(" ")
    expect(steps).not.toContain("chmod")
    expect(steps).not.toContain("./sentinel")
    expect(steps).not.toContain("enroll --code")
  })

  it("says enrolment needs no pasted code, matching the app's real one-tap flow", () => {
    expect(androidInstallSteps().join(" ")).toMatch(/one-tap|nothing to type/)
  })
})
