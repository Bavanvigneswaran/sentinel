import { describe, expect, it } from "vitest"

import {
  agentServerOrigin,
  androidInstallSteps,
  buildsFor,
  CODE_PLACEHOLDER,
  enrollmentPlan,
  installSteps,
  shellFor,
  isCliInstall,
  archLabelFor,
  isDesktopAgent,
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

describe("isDesktopAgent", () => {
  it("separates the three headless binaries from the Android app", () => {
    // The download page renders two sections off this. Everything the
    // "Desktop agents" heading claims — headless, installed onto the machine
    // you then watch from elsewhere — is false of the APK, and presenting it
    // as a fourth agent implied a phone needs one installed onto it.
    for (const os of ["macos", "linux", "windows"] as const) {
      expect(isDesktopAgent(build({ os }))).toBe(true)
    }
    expect(isDesktopAgent(build({ os: "android" }))).toBe(false)
  })
})

describe("archLabelFor", () => {
  it("does not call an Android phone Apple Silicon", () => {
    // ARCH_LABEL is written for the desktop chooser, where "arm64" has to read
    // as the thing Apple sells. Applied to the APK it rendered
    // "Android · Apple Silicon / ARM64".
    expect(archLabelFor(build({ os: "android", arch: "arm64" }))).toBe("ARM64")
  })

  it("leaves the desktop wording alone, which the ambiguous-Mac card depends on", () => {
    expect(archLabelFor(build({ os: "macos", arch: "arm64" }))).toBe("Apple Silicon / ARM64")
    expect(archLabelFor(build({ os: "linux", arch: "x64" }))).toBe("Intel / AMD64")
  })
})

// The realistic origin: `make serve` binds the console and the API to one
// plaintext LAN address, which is what every enrolled machine has to be
// pointed at and is exactly what the agent's requires_tls() refuses silently.
const LAN = "http://10.233.129.19:8000"

describe("enrollmentPlan", () => {
  it("substitutes the real code, so nothing has to be typed by hand", () => {
    const commands = enrollmentPlan(build(), "X4T9-K2QM-7PDR", LAN).map((s) => s.command)
    expect(commands.join("\n")).toContain("enroll --code X4T9-K2QM-7PDR")
    expect(commands.join("\n")).not.toContain(CODE_PLACEHOLDER)
  })

  it("shows an obviously fake placeholder before a code has been minted", () => {
    // Not a plausible-looking code: somebody who pastes this should get an
    // obvious error rather than a puzzling rejection.
    expect(enrollmentPlan(build(), null, LAN).map((s) => s.command).join("\n")).toContain(
      CODE_PLACEHOLDER,
    )
  })

  it("starts in the folder the browser actually saved the file to", () => {
    // The step that was missing: every command shown was correct and the
    // first one still failed, because the shell opens in $HOME.
    expect(enrollmentPlan(build(), null, LAN)[0].command).toBe("cd ~/Downloads")
    expect(enrollmentPlan(build({ os: "windows" }), null, LAN)[0].command).toBe(
      "cd $HOME\\Downloads",
    )
  })

  it("clears the quarantine flag on an unsigned macOS build", () => {
    // Gatekeeper enforces this before the process starts, so `enroll` fails
    // with no output of its own. It was prose in a warning box; it is a step.
    const commands = enrollmentPlan(build(), null, LAN).map((s) => s.command)
    expect(commands).toContain("xattr -d com.apple.quarantine sentinel-agent-0.1.0-macos-arm64")
    expect(commands.indexOf("xattr -d com.apple.quarantine sentinel-agent-0.1.0-macos-arm64")).
      toBeLessThan(commands.findIndex((c) => c.includes("enroll")))
  })

  it("does not clear quarantine on a signed build, or on Linux", () => {
    expect(
      enrollmentPlan(build({ signed: true }), null, LAN).map((s) => s.command).join(" "),
    ).not.toContain("xattr")
    expect(
      enrollmentPlan(build({ os: "linux", filename: "sentinel-agent-linux" }), null, LAN)
        .map((s) => s.command)
        .join(" "),
    ).not.toContain("xattr")
  })

  it("never tells a Windows user to chmod", () => {
    expect(
      enrollmentPlan(build({ os: "windows", filename: "sentinel-agent.exe" }), null, LAN)
        .map((s) => s.command)
        .join(" "),
    ).not.toContain("chmod")
  })

  it("explains every command it shows", () => {
    // A command with no explanation is a thing to paste rather than a thing
    // to understand, and this page's whole point is that the user is running
    // an unsigned binary on a machine they care about.
    for (const step of enrollmentPlan(build(), "X4T9-K2QM-7PDR", LAN)) {
      expect(step.note.length).toBeGreaterThan(0)
    }
  })
})

describe("enrollmentPlan's server address", () => {
  it("points the agent at this console, not at its own localhost default", () => {
    // Without --server the binary defaults to http://localhost:8000, which is
    // wrong on every machine except the one running the backend — i.e. every
    // machine this page exists to enrol.
    for (const os of ["macos", "linux", "windows"] as const) {
      const enrol = enrollmentPlan(build({ os }), "X4T9-K2QM-7PDR", LAN).find((s) =>
        s.command.includes("enroll"),
      )
      expect(enrol?.command).toContain(`--server ${LAN}`)
    }
  })

  it("puts --server before the subcommand and --insecure after it", () => {
    // argparse rejects the whole line with "unrecognized arguments: --server"
    // when a *global* option follows the subcommand — the same trap
    // test_service_units.py exists for with --config. Verified against the
    // agent's own build_parser(): only this ordering parses.
    const command = enrollmentPlan(
      build({ os: "windows", filename: "sentinel-agent.exe" }),
      "X4T9-K2QM-7PDR",
      LAN,
    ).find((s) => s.command.includes("enroll"))!.command

    expect(command).toBe(
      `.\\sentinel-agent.exe --server ${LAN} enroll --code X4T9-K2QM-7PDR --insecure`,
    )
    expect(command.indexOf("--server")).toBeLessThan(command.indexOf("enroll"))
    expect(command.indexOf("--insecure")).toBeGreaterThan(command.indexOf("enroll"))
  })

  it("does not repeat --server on the steps after enroll, which saved it", () => {
    // enroll() calls config.save() with the overridden server_url, so
    // install-service / status / run read it back from the config file.
    const after = enrollmentPlan(build(), null, LAN)
      .filter((s) => !s.command.includes("enroll"))
      .map((s) => s.command)
      .join(" ")
    expect(after).not.toContain("--server")
  })

  it("adds --insecure for a plaintext remote server, because enroll refuses without it", () => {
    // config.py's requires_tls(): the code and the long-lived token it becomes
    // both travel in cleartext, so `enroll` refuses a remote http:// URL
    // outright. Showing the command without the flag hands the user a line
    // that cannot work against the only backend this project has.
    const enrol = enrollmentPlan(build(), null, LAN).find((s) => s.command.includes("enroll"))
    expect(enrol?.command).toContain("--insecure")
    expect(enrol?.note).toContain("cleartext")
  })

  it("omits --insecure when it would be neither needed nor accurate", () => {
    // Mirrors is_loopback()/requires_tls() exactly: https anywhere, or plain
    // http to this same machine, is already allowed.
    const https = enrollmentPlan(build(), null, "https://sentinel.example.com").find((s) =>
      s.command.includes("enroll"),
    )
    expect(https?.command).not.toContain("--insecure")

    for (const origin of ["http://localhost:8000", "http://127.0.0.1:8000"]) {
      const local = enrollmentPlan(build(), null, origin).find((s) => s.command.includes("enroll"))
      expect(local?.command).not.toContain("--insecure")
    }
  })

  it("uses the browser's own origin once the console is served by the API", () => {
    // `make serve` puts the console, the REST API and the viewer socket on one
    // origin, which is the whole point of it — so the address in the command
    // is the address in the address bar.
    expect(agentServerOrigin("http://10.233.129.19:8000", false)).toBe("http://10.233.129.19:8000")
  })

  it("does not hand out the Vite dev server's port, which forwards only /api", () => {
    // The agent posts to /enroll with no prefix (Phase 1), and the dev proxy
    // forwards /api and /ws only — so :5173 in a command is a command that
    // cannot work. 8000 is the proxy's own target, read from vite.config.ts.
    expect(agentServerOrigin("http://localhost:5173", true)).toBe("http://localhost:8000")
  })

  it("ends with a plain run command for bringing the agent back up by hand", () => {
    // Asked for directly: what to type after a dropped connection, or once a
    // machine is switched back on. install-service covers both automatically,
    // which is why the note says so rather than presenting this as routine.
    const windows = enrollmentPlan(
      build({ os: "windows", filename: "sentinel-agent-0.1.0-windows-x64.exe" }),
      null,
      LAN,
    )
    const last = windows[windows.length - 1]
    expect(last.command).toBe(".\\sentinel-agent-0.1.0-windows-x64.exe run")
    expect(last.note).toContain("install-service")

    const unix = enrollmentPlan(build(), null, LAN)
    expect(unix[unix.length - 1].command).toBe("./sentinel-agent-0.1.0-macos-arm64 run")
  })
})

describe("shellFor", () => {
  it("names the terminal each OS's user would recognise", () => {
    expect(shellFor(build({ os: "windows" }))).toBe("PowerShell")
    expect(shellFor(build({ os: "macos" }))).toBe("Terminal")
    expect(shellFor(build({ os: "linux" }))).toBe("Terminal")
  })
})

describe("verifyCommand's host machine", () => {
  it("uses the browsing machine's tool, not the target platform's", () => {
    // The APK is checked on the computer that downloaded it — there is no
    // shell on the phone. Keyed on build.os it handed a Mac user
    // `sha256sum app-release.apk`, which macOS does not ship. Seen on the
    // real page.
    const apk = build({ os: "android", filename: "app-release.apk" })
    expect(verifyCommand(apk, "macos")).toBe("shasum -a 256 app-release.apk")
    expect(verifyCommand(apk, "windows")).toContain("Get-FileHash")
    expect(verifyCommand(apk, "linux")).toBe("sha256sum app-release.apk")
  })

  it("falls back to the build's own OS when the browser did not identify itself", () => {
    // The right guess for a desktop agent: the common case is downloading on
    // the machine you are about to enrol.
    expect(verifyCommand(build({ os: "linux" }), null)).toContain("sha256sum")
    expect(verifyCommand(build({ os: "macos" }), null)).toContain("shasum -a 256")
  })
})
