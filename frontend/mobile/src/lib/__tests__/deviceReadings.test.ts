/**
 * The per-platform readings grid.
 *
 * The rule under test is the one that is easy to get wrong in the direction
 * that looks fine: a phone must not be asked for metrics it can never report,
 * and — the other half, which is what stops this becoming a way to quietly hide
 * bad news — a desktop must keep every key it *could* report even when the
 * value happens to be null right now.
 */

import assert from "node:assert/strict"
import { describe, it } from "node:test"

import { readingKeysFor, unmeasurableNoteFor } from "../deviceReadings.ts"

describe("readingKeysFor", () => {
  it("never asks an Android device for what no app can read", () => {
    const keys = readingKeysFor("android")
    // /proc/stat, /proc/diskstats and per-process /proc entries are all denied
    // to apps from API 26–28. See docs/ANDROID_METRICS.md.
    for (const absent of ["cpu", "load1", "diskRead", "diskWrite", "processes"]) {
      assert.equal(keys.includes(absent as never), false, `${absent} must not be asked of a phone`)
    }
  })

  it("shows a phone the metrics a desktop has no use for", () => {
    const keys = readingKeysFor("android")
    assert.equal(keys.includes("battery"), true)
    assert.equal(keys.includes("temperature"), true)
  })

  it("keeps every desktop key, including ones that are often null", () => {
    const keys = readingKeysFor("desktop")
    // A null here means "nothing measured inside the freshness window", which
    // is real information — quite different from "this platform has no such
    // thing". Dropping the row would erase that distinction.
    assert.equal(keys.includes("diskRead"), true)
    assert.equal(keys.includes("cpu"), true)
    assert.equal(keys.includes("processes"), true)
  })

  it("gives both platforms the readings they share", () => {
    for (const platform of ["desktop", "android"] as const) {
      const keys = readingKeysFor(platform)
      for (const shared of ["memory", "swap", "netIn", "netOut", "rtt", "packetLoss", "uptime"]) {
        assert.equal(keys.includes(shared as never), true, `${platform} is missing ${shared}`)
      }
    }
  })

  it("has no duplicate keys, which would render the same stat twice", () => {
    for (const platform of ["desktop", "android"] as const) {
      const keys = readingKeysFor(platform)
      assert.equal(new Set(keys).size, keys.length)
    }
  })
})

describe("unmeasurableNoteFor", () => {
  it("explains the gap on Android rather than leaving it unexplained", () => {
    const note = unmeasurableNoteFor("android")
    assert.ok(note)
    assert.match(note, /CPU/)
    // The point of the note is that the missing components are *excluded* from
    // the score, not counted as healthy.
    assert.match(note, /health score/)
  })

  it("says nothing on a platform with a complete grid", () => {
    // A reassuring "everything is measurable here" line would be pure noise.
    assert.equal(unmeasurableNoteFor("desktop"), null)
  })
})
