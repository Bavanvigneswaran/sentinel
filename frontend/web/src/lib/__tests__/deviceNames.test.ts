import { describe, expect, it } from "vitest"

import { deviceLabel, indexDevices } from "@/lib/deviceNames"
import type { Device } from "@/types/api"

function device(overrides: Partial<Device> & Pick<Device, "id" | "name">): Device {
  return {
    hostname: null,
    os: null,
    os_version: null,
    kernel_version: null,
    arch: null,
    cpu_cores: null,
    total_memory_bytes: null,
    agent_version: null,
    platform: "desktop",
    status: "offline",
    last_seen_at: null,
    enrolled_at: null,
    created_at: "2026-08-01T00:00:00Z",
    ...overrides,
  }
}

describe("deviceLabel", () => {
  const live = device({ id: "aaaaaaaa-1111-2222-3333-444444444444", name: "my-mac" })
  const removed = device({
    id: "bbbbbbbb-1111-2222-3333-444444444444",
    name: "old-phone",
    deleted_at: "2026-08-20T10:00:00Z",
  })
  const index = indexDevices([live, removed])

  it("names a live device plainly", () => {
    expect(deviceLabel(index, live.id)).toBe("my-mac")
  })

  it("names a removed device and says it is removed", () => {
    // The whole point: this row is history worth keeping, and it used to
    // render as a bare UUID because /devices had filtered the device out.
    expect(deviceLabel(index, removed.id)).toBe("old-phone (removed)")
  })

  it("falls back to a short id when the device list has not loaded", () => {
    // Distinct from "removed": nothing is known yet, so claiming the device
    // was removed would be inventing an explanation.
    expect(deviceLabel({}, live.id)).toBe("aaaaaaaa")
  })
})
