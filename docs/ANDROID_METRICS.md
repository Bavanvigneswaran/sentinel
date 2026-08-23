# What an Android device can honestly report

**Decided before the collector was written, not discovered afterwards.** This file is the
authority for `frontend/mobile/modules/sentinel-collector`; if a collector and this table disagree, the
collector is wrong.

CLAUDE.md's hard rule applies without exception: *a value the platform cannot measure is `null` on
the wire and NULL in the database, and renders as "unavailable".* Never a synthesised zero, and —
the subtler trap — never a number scraped from an API that returns something plausible but wrong.

Android is not a small Linux box. It is a Linux box with most of `/proc` behind SELinux and most
system state behind privileged APIs. The result is that a phone fills in roughly a third of
`app/schemas/protocol.py`'s `SystemSample`, and that is the honest answer.

## The three categories

Every field of the protocol falls into exactly one of these. Nothing is left undecided.

### 1. Measured — a real reading from a real API

| Protocol field | Source | Note |
|---|---|---|
| `mem_total_bytes` | `ActivityManager.MemoryInfo.totalMem` | Device-wide physical RAM. |
| `mem_available_bytes` | `MemoryInfo.availMem` | Device-wide. |
| `mem_used_bytes` | `totalMem - availMem` | Arithmetic over two real readings, not an estimate. |
| `mem_percent` | `used / total * 100` | Feeds the health score's `memory` component. |
| `uptime_seconds` | `SystemClock.elapsedRealtime() / 1000` | Since boot, including deep sleep — the same thing a desktop's uptime means. |
| `battery_percent` | `BatteryManager.EXTRA_LEVEL / EXTRA_SCALE` | |
| `battery_plugged` | `BatteryManager.EXTRA_PLUGGED != 0` | |
| `temperature_celsius` | `BatteryManager.EXTRA_TEMPERATURE / 10` | **The battery sensor.** See "Named honestly" below. |
| `disk_usage[]` | `StatFs` on the data dir and shared storage | Two real mounts, real block counts. |
| `net[]` | `TrafficStats.getTotalRx/TxBytes`, `…Packets` | One entity, `device-total`. See "Named honestly". |
| `latency[]` | TCP-connect RTT, 3 probes per sample | Identical technique to the Python agent's `collectors/latency.py`. Feeds the health score's `network` (packet loss) component. |

Host metadata is all real: `Build.MODEL` / `Settings.Global.DEVICE_NAME` for the hostname,
`Build.VERSION.RELEASE`, `System.getProperty("os.version")` for the kernel, `Build.SUPPORTED_ABIS[0]`,
`Runtime.availableProcessors()`, and `MemoryInfo.totalMem`.

### 2. Probed — attempted at runtime, `null` when the platform refuses

These are plain file reads that either succeed with a true system-wide value or throw. Attempting
them is honest: the read is the measurement. Whether they work varies by OEM, kernel and API level,
so the collector records *which* probes are live and surfaces that in its status rather than
quietly reporting a metric on some phones and not others without explanation.

| Protocol field | Source | Typically |
|---|---|---|
| `load1` / `load5` / `load15` | `/proc/loadavg` | Readable on many builds. |
| `swap_total_bytes`, `swap_used_bytes`, `swap_percent` | `/proc/meminfo` `SwapTotal`/`SwapFree` | Readable; on Android this is zram. |
| `cpu_freq_mhz` | `/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq` | Increasingly blocked on newer Android. |

### 3. Not measurable — always `null`, never attempted

| Protocol field | Why |
|---|---|
| `cpu_percent`, `_min`, `_max` | **The headline constraint.** `/proc/stat` is denied to apps by SELinux from API 26. See below. |
| `cpu_user_percent`, `cpu_system_percent`, `cpu_iowait_percent` | Same source, same denial. |
| `ctx_switches_per_s` | Same source. |
| `process_count` | An app cannot enumerate other processes since API 26; `/proc/<pid>` of anything but itself is hidden. A count of what we *can* see is always 1, which is not the device's process count. |
| `active_connections` | `/proc/net` is denied from API 28. |
| `fan_rpm` | Phones do not have one, and none is exposed. |
| `logged_in_users` | No such concept. |
| `disk_io[]` (empty list) | `/proc/diskstats` is denied; there is no per-block-device counter available to an app. |
| `processes[]` (empty list) | See "The top-processes trap" below. |

## Global CPU% is not attempted at all — and that is a decision, not an omission

From API 26, `/proc/stat` is unreadable by apps. There is no replacement API: `ActivityManager`
gives memory, not CPU, and everything else that looks like a CPU number is either the app's own
usage or a percentage of something other than the device.

The collector therefore **never opens `/proc/stat`**, rather than trying and falling back. The
reasoning is deliberate and worth stating, because "try it and see" is the tempting alternative:

* On every supported production configuration the read fails, so the fallback is the only path a
  real user ever takes. A code path that only executes on an emulator or a rooted device is a path
  that is never really tested.
* Where the read *does* succeed — an old API 24/25 build, an unusual kernel — the fleet's CPU column
  would then mean "readable on this particular kernel" rather than "CPU". A column that silently
  changes meaning between two phones is worse than a column that is honestly empty on both.

The visible consequence is intended: **an Android device gets a health score with no CPU
component.** `app/analysis/health.py` excludes a component with no reading and renormalises the
weights of the ones that did report, so a phone is scored on memory (3), disk capacity (4), packet
loss (2) and — where `/proc/meminfo` is readable — swap (1), and stays comparable to a Linux box
scored on all six. The UI already names the excluded components; nothing had to be added for that.

## The top-processes trap

The architecture note lists "per-app CPU" as something Android gives you. It does — but only for
*your own* app: `/proc/self/stat` is readable, and no other process's is.

So a `processes[]` list is technically fillable, with exactly one entry: the collector itself. The
protocol's `processes[]` means "top N by CPU/memory **on this machine**", and the UI renders it under
that heading. Sending our own process as rank 1 would put a true number under a false claim — the
reader would take "com.sentinel.viewer, 0.4%" to mean it is the busiest thing on the phone.

**We send an empty list.** The agent's own footprint is real but it is not the fleet-meaningful
metric the field represents, and there is no protocol slot that means "the agent's own CPU".

## Named honestly

Two fields carry a real measurement whose *scope* differs from a desktop's, so the entity name says
so rather than the value quietly meaning something else:

* **`temperature_celsius` is the battery sensor.** It is the only temperature an unprivileged app
  can read (`HardwarePropertiesManager` is device-owner only). It is a real thermometer inside the
  device and it does rise under load, but it is not a CPU package temperature, and the per-device
  screen labels it "Battery temp" for an Android device rather than "Temperature".
* **`net[]` carries one entity named `device-total`, not a NIC.** `TrafficStats` device-wide
  counters are the honest, permission-free source, but they are not broken down per interface.
  Labelling them `wlan0` would claim traffic went over Wi-Fi when it may have gone over cellular.
  `device-total` describes exactly what was counted. Per-interface `TrafficStats.getRxBytes(iface)`
  is not used because enumerating interfaces is itself restricted from API 30, which would put us
  back to guessing a name.

## Deliberately deferred: thermal status

`PowerManager.getCurrentThermalStatus()` (API 29+) reports the device's throttling state
(`NONE`…`SHUTDOWN`). It is arguably the most Android-specific signal there is, and it has no home in
the protocol.

It is **not** added, and `PROTOCOL_VERSION` stays at 1. Carrying it would mean a protocol field, a
`metric_samples` column, rollup columns across three tiers, TS types on two clients and UI on
both — four layers of surface for one enum that exactly one platform can ever populate. The
throttling state is instead shown in the collector's own persistent notification on the phone, where
it costs nothing and is visible to the person holding the device.

If a later phase wants it in the fleet, that is an additive optional field (old agents simply do not
send it) and still needs no version bump — the bump is for *breaking* changes, and this would not
be one.

## Cadence, and why it differs from the desktop agent

The Python agent samples at 1s always and pushes a 10s aggregate. A phone samples at **10s in
normal mode** and upshifts to **1s only while a viewer is watching Live Monitoring**, because a 1s
timer running all day is what actually costs battery — the readings themselves are cheap in-memory
API calls. Every metric Android can measure is slow-moving (memory, storage, battery, temperature),
so a 10s sample loses nothing real; a desktop's CPU, which is the metric that genuinely needs 1s
resolution to be meaningful, is the one thing a phone cannot measure at all.

A 10s aggregate built from a single 10s sample reports that sample's value as its own min, max and
mean. That is accurate for what was measured, and `resolution_seconds` still says 10.
