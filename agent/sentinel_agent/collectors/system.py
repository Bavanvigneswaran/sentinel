"""System-wide collectors.

Every reading comes from psutil against the real machine. When a value cannot
be measured on this platform the collector returns None — it is never guessed,
defaulted to zero, or carried over from a previous sample. The UI renders None
as "unavailable", which is the honest answer.

Counters (context switches, disk and network bytes) are differentiated against
the previous reading and reported as per-second rates. Doing that here rather
than server-side means a counter reset — a reboot, a NIC reappearing, a 32-bit
wrap — can be detected and dropped instead of surfacing as an enormous spike.
"""

from __future__ import annotations

import logging
import platform
import time
from dataclasses import dataclass, field

import psutil

logger = logging.getLogger(__name__)

_SYSTEM = platform.system()

#: psutil's ctx_switches is a cumulative counter on Linux and Windows but a
#: per-interval delta on macOS — sampling it three times here gave 18172,
#: 18575, 18434. Differentiating that produces a meaningless rate roughly half
#: the time and nothing the rest, so on Darwin the metric is reported as
#: unavailable rather than as a number that does not mean what it says.
CTX_SWITCHES_IS_CUMULATIVE = _SYSTEM != "Darwin"

#: A plausible floor for a real CPU clock in MHz. psutil on Apple Silicon
#: reports scpufreq(current=4, min=0, max=4), which is not MHz and is not
#: anything else usable; rendering "4 MHz" on a dashboard would be a fabricated
#: reading.
MIN_PLAUSIBLE_CPU_MHZ = 100


def _safe(fn, default=None):  # noqa: ANN001
    """psutil raises a wide variety of platform-specific errors for metrics that
    simply are not exposed. Those are expected, not exceptional."""
    try:
        return fn()
    except (OSError, PermissionError, AttributeError, NotImplementedError):
        return default
    except Exception:  # noqa: BLE001 — psutil surfaces odd platform errors
        logger.debug("collector failed", exc_info=True)
        return default


@dataclass
class _Rate:
    """Turns a monotonic counter into a per-second rate."""

    previous: float | None = None
    previous_at: float | None = None

    def update(self, value: float | None, now: float) -> float | None:
        if value is None:
            self.previous, self.previous_at = None, None
            return None

        prev, prev_at = self.previous, self.previous_at
        self.previous, self.previous_at = value, now

        if prev is None or prev_at is None:
            return None  # first reading: no interval to divide by
        if value < prev:
            # Counter went backwards: a reboot, a wrap, or an interface that
            # disappeared and came back. Report nothing rather than a spike.
            return None
        elapsed = now - prev_at
        if elapsed <= 0:
            return None
        return (value - prev) / elapsed


@dataclass
class SystemCollector:
    """Holds the counter state needed to compute rates between samples."""

    _ctx_switches: _Rate = field(default_factory=_Rate)

    def collect(self) -> dict:
        now = time.monotonic()

        cpu_times = _safe(lambda: psutil.cpu_times_percent(interval=None))
        vmem = _safe(psutil.virtual_memory)
        swap = _safe(psutil.swap_memory)
        # Called through a lambda, not passed as `psutil.cpu_freq`: psutil
        # defines the function only `if cext.has_cpu_freq()`, which is false on
        # some Apple Silicon Macs, and the attribute lookup in an argument is
        # evaluated *before* _safe() can catch anything. Same psutil version
        # (7.2.2) has it on this Mac and not on GitHub's macos-14 arm64 runner,
        # which is how the AttributeError was found.
        freq = _safe(lambda: psutil.cpu_freq())  # noqa: PLW0108
        load = _safe(psutil.getloadavg)  # Unix only
        stats = _safe(psutil.cpu_stats)
        boot = _safe(psutil.boot_time)

        sample: dict = {
            "cpu_percent": _safe(lambda: psutil.cpu_percent(interval=None)),
            "cpu_user_percent": getattr(cpu_times, "user", None),
            "cpu_system_percent": getattr(cpu_times, "system", None),
            # iowait exists on Linux only; macOS and Windows do not expose it.
            "cpu_iowait_percent": getattr(cpu_times, "iowait", None),
            "cpu_freq_mhz": self._cpu_freq_mhz(freq),
            "ctx_switches_per_s": (
                self._ctx_switches.update(getattr(stats, "ctx_switches", None), now)
                if CTX_SWITCHES_IS_CUMULATIVE
                else None
            ),
            "load1": load[0] if load else None,
            "load5": load[1] if load else None,
            "load15": load[2] if load else None,
            "mem_total_bytes": getattr(vmem, "total", None),
            "mem_used_bytes": getattr(vmem, "used", None),
            "mem_available_bytes": getattr(vmem, "available", None),
            "mem_percent": getattr(vmem, "percent", None),
            "swap_total_bytes": getattr(swap, "total", None),
            "swap_used_bytes": getattr(swap, "used", None),
            "swap_percent": getattr(swap, "percent", None),
            "uptime_seconds": int(time.time() - boot) if boot else None,
            "logged_in_users": _safe(lambda: len(psutil.users())),
            "process_count": _safe(lambda: len(psutil.pids())),
            "active_connections": _safe(lambda: len(psutil.net_connections(kind="inet"))),
            "temperature_celsius": self._temperature(),
            "fan_rpm": self._fan_rpm(),
        }
        sample.update(self._battery())
        return sample

    @staticmethod
    def _cpu_freq_mhz(freq) -> float | None:  # noqa: ANN001
        current = getattr(freq, "current", None)
        if current is None or current < MIN_PLAUSIBLE_CPU_MHZ:
            return None
        return float(current)

    @staticmethod
    def _temperature() -> float | None:
        """Not available on macOS without an elevated helper, and absent in most
        VMs. Prefers a package/CPU sensor when several are exposed."""
        temps = _safe(lambda: psutil.sensors_temperatures())  # type: ignore[attr-defined]
        if not temps:
            return None
        preferred = ("coretemp", "k10temp", "cpu_thermal", "acpitz")
        for key in preferred:
            entries = temps.get(key)
            if entries:
                return entries[0].current
        for entries in temps.values():
            if entries:
                return entries[0].current
        return None

    @staticmethod
    def _fan_rpm() -> float | None:
        fans = _safe(lambda: psutil.sensors_fans())  # type: ignore[attr-defined]
        if not fans:
            return None
        for entries in fans.values():
            if entries:
                return entries[0].current
        return None

    @staticmethod
    def _battery() -> dict:
        battery = _safe(lambda: psutil.sensors_battery())  # type: ignore[attr-defined]
        if battery is None:
            # A desktop or server has no battery. Absent, not zero.
            return {"battery_percent": None, "battery_plugged": None}
        return {
            "battery_percent": battery.percent,
            "battery_plugged": battery.power_plugged,
        }
