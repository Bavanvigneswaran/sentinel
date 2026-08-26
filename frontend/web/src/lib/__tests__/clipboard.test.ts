import { afterEach, describe, expect, it, vi } from "vitest"

import { copyText } from "@/lib/clipboard"

/**
 * There is no jsdom in this project's test setup, so the DOM the fallback
 * needs is faked here — narrowly, and only the handful of members
 * copyViaExecCommand actually touches.
 *
 * That is worth the awkwardness because the bug being guarded against is
 * invisible to a pure-function test: `navigator.clipboard?.writeText(v).then(…)`
 * short-circuits the *whole* chain when `clipboard` is undefined, which is
 * exactly what a plain-http LAN origin gives you. Nothing threw, nothing
 * copied, and no callback ran. The only way to observe that is to ask whether
 * the text arrived somewhere.
 */
function fakeDom() {
  const copied: string[] = []
  const appended: unknown[] = []

  const fakeDocument = {
    activeElement: null,
    body: { appendChild: (node: unknown) => appended.push(node) },
    createElement: () => ({
      value: "",
      style: {} as Record<string, string>,
      setAttribute: () => undefined,
      select: () => undefined,
      setSelectionRange: () => undefined,
      remove: () => undefined,
    }),
    execCommand: (command: string) => {
      if (command !== "copy") return false
      const textarea = appended.at(-1) as { value: string } | undefined
      if (!textarea) return false
      copied.push(textarea.value)
      return true
    },
  }

  vi.stubGlobal("document", fakeDocument)
  return copied
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe("copyText", () => {
  it("uses the async clipboard API when the context allows it", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    vi.stubGlobal("navigator", { clipboard: { writeText } })
    const copied = fakeDom()

    await expect(copyText("X4T9-K2QM-7PDR")).resolves.toBe(true)
    expect(writeText).toHaveBeenCalledWith("X4T9-K2QM-7PDR")
    // The deprecated path is not touched when the modern one works.
    expect(copied).toEqual([])
  })

  it("still copies when navigator.clipboard does not exist at all", async () => {
    // A non-secure context — http:// on a LAN address, which is exactly how
    // `make serve` is reached. This is the case that silently did nothing.
    vi.stubGlobal("navigator", {})
    const copied = fakeDom()

    await expect(copyText("X4T9-K2QM-7PDR")).resolves.toBe(true)
    expect(copied).toEqual(["X4T9-K2QM-7PDR"])
  })

  it("falls back when the async API exists but rejects", async () => {
    // Permission denied, or a document that has lost focus.
    vi.stubGlobal("navigator", {
      clipboard: { writeText: vi.fn().mockRejectedValue(new Error("denied")) },
    })
    const copied = fakeDom()

    await expect(copyText("hello")).resolves.toBe(true)
    expect(copied).toEqual(["hello"])
  })

  it("reports failure rather than claiming a copy that did not happen", async () => {
    vi.stubGlobal("navigator", {})
    vi.stubGlobal("document", {
      activeElement: null,
      body: { appendChild: () => undefined },
      createElement: () => ({
        value: "",
        style: {} as Record<string, string>,
        setAttribute: () => undefined,
        select: () => undefined,
        setSelectionRange: () => undefined,
        remove: () => undefined,
      }),
      execCommand: () => false,
    })

    // The caller renders "select it by hand" off this. Returning true here
    // would put "Copied" over a clipboard holding something else entirely.
    await expect(copyText("hello")).resolves.toBe(false)
  })
})
