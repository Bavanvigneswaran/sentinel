import { describe, expect, it } from "vitest"

import { detectPlatform, formatBytes } from "@/lib/platform"

const UA = {
  macIntel:
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
  windows:
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
  windowsArm:
    "Mozilla/5.0 (Windows NT 10.0; ARM64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
  linux:
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
  linuxArm: "Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0",
  android:
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Mobile Safari/537.36",
  iphone:
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
  ipad: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
}

describe("detectPlatform", () => {
  it("recognises the three desktop platforms", () => {
    expect(detectPlatform(UA.macIntel).os).toBe("macos")
    expect(detectPlatform(UA.windows).os).toBe("windows")
    expect(detectPlatform(UA.linux).os).toBe("linux")
  })

  it("never claims to know a Mac's architecture from the user agent alone", () => {
    // Safari and Chrome both report "Intel Mac OS X" on Apple Silicon,
    // permanently, for compatibility. Guessing here hands half of all Mac
    // users a binary that will not run.
    const detected = detectPlatform(UA.macIntel)
    expect(detected.archCertain).toBe(false)
    expect(detected.arch).toBeNull()
  })

  it("takes Chromium's architecture hint when it is offered", () => {
    const detected = detectPlatform(UA.macIntel, { architecture: "arm" })
    expect(detected.arch).toBe("arm64")
    expect(detected.archCertain).toBe(true)
  })

  it("maps the hint's x86 to the manifest's x64", () => {
    expect(detectPlatform(UA.macIntel, { architecture: "x86" }).arch).toBe("x64")
  })

  it("ignores a hint it does not understand", () => {
    expect(detectPlatform(UA.macIntel, { architecture: "sparc" }).archCertain).toBe(false)
  })

  it("reads Linux's architecture, which is actually in the string", () => {
    expect(detectPlatform(UA.linux).arch).toBe("x64")
    expect(detectPlatform(UA.linux).archCertain).toBe(true)
    expect(detectPlatform(UA.linuxArm).arch).toBe("arm64")
  })

  it("treats an unhinted Windows reading as a default, not a measurement", () => {
    // Windows on ARM reports x64 for compatibility too.
    expect(detectPlatform(UA.windows).arch).toBe("x64")
    expect(detectPlatform(UA.windows).archCertain).toBe(false)
    expect(detectPlatform(UA.windowsArm).archCertain).toBe(true)
  })

  it("puts Android before Linux", () => {
    // Every Android user agent also contains "Linux"; ordering is the whole
    // fix, and getting it wrong offers a phone a Linux binary.
    expect(detectPlatform(UA.android).os).toBe("android")
  })

  it("recognises iOS, including an iPad pretending to be a Mac", () => {
    expect(detectPlatform(UA.iphone).os).toBe("ios")
    expect(detectPlatform(UA.ipad).os).toBe("ios")
  })

  it("returns null rather than guessing for something unrecognised", () => {
    const detected = detectPlatform("Mozilla/5.0 (PlayStation 5) AppleWebKit/605.1.15")
    expect(detected.os).toBeNull()
    expect(detected.label).toContain("unrecognised")
  })

  it("survives an empty user agent", () => {
    expect(detectPlatform("").os).toBeNull()
  })
})

describe("formatBytes", () => {
  it("uses the unit the number deserves", () => {
    expect(formatBytes(512)).toBe("512 B")
    expect(formatBytes(2048)).toBe("2 KB")
    expect(formatBytes(12_800_000)).toBe("12.2 MB")
  })
})
