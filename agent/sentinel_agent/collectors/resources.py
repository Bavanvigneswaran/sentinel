"""Per-entity collectors: filesystems, disks, network interfaces, processes.

Disk usage and disk IO are separate collectors because they are measured
against different things. psutil reports usage per *mount point* and IO
counters per *physical disk*, and there is no reliable way to map one onto the
other. Presenting them as one thing would mean inventing a relationship that
was never measured.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import psutil

from sentinel_agent.collectors.system import _Rate, _safe

logger = logging.getLogger(__name__)

#: Pseudo-filesystems carry no useful capacity signal and would swamp the list.
SKIP_FSTYPES = frozenset(
    {
        "autofs", "devfs", "devtmpfs", "tmpfs", "squashfs", "overlay",
        "proc", "sysfs", "cgroup", "cgroup2", "ramfs", "nullfs", "fdescfs",
    }
)

#: macOS mounts several hidden APFS volumes that share one container and so all
#: report the same total_bytes; charting them separately would imply the machine
#: has eight disks.
#:
#: Two heuristics that look right are both wrong here. Filtering read-only
#: mounts drops `/`, because the modern macOS system volume is sealed and
#: mounted ro. Filtering Apple's `dontbrowse` flag drops
#: `/System/Volumes/Data`, which is where every byte of user data actually
#: lives — on this machine `/` reports 4% used while Data reports 29%, so
#: losing it would understate real disk usage sevenfold.
#:
#: An explicit list of Apple's firmware and boot-support volumes is the only
#: filter that keeps both of the volumes a user cares about.
SKIP_MOUNTS = frozenset(
    {
        "/System/Volumes/Preboot",
        "/System/Volumes/VM",
        "/System/Volumes/Update",
        "/System/Volumes/xarts",
        "/System/Volumes/iSCPreboot",
        "/System/Volumes/Hardware",
        "/System/Volumes/Recovery",
    }
)

TOP_PROCESSES = 10


def collect_disk_usage() -> list[dict]:
    entries: list[dict] = []
    partitions = _safe(lambda: psutil.disk_partitions(all=False), default=[]) or []

    for part in partitions:
        if part.fstype.lower() in SKIP_FSTYPES:
            continue
        if part.mountpoint in SKIP_MOUNTS:
            continue
        # A disconnected network mount or an unreadable volume raises here;
        # skipping it is right, inventing a zero would not be.
        usage = _safe(lambda p=part: psutil.disk_usage(p.mountpoint))
        if usage is None:
            continue
        entries.append(
            {
                "mount": part.mountpoint[:255],
                "filesystem": (part.fstype or None),
                "total_bytes": usage.total,
                "used_bytes": usage.used,
                "free_bytes": usage.free,
                "percent": usage.percent,
            }
        )
    return entries


@dataclass
class DiskIoCollector:
    _rates: dict[str, dict[str, _Rate]] = field(default_factory=dict)

    def collect(self, now: float) -> list[dict]:
        counters = _safe(lambda: psutil.disk_io_counters(perdisk=True), default={}) or {}
        entries: list[dict] = []

        for disk, io in counters.items():
            rates = self._rates.setdefault(
                disk, {k: _Rate() for k in ("rb", "wb", "rc", "wc", "busy")}
            )
            entry = {
                "disk": disk[:255],
                "read_bytes_per_s": rates["rb"].update(io.read_bytes, now),
                "write_bytes_per_s": rates["wb"].update(io.write_bytes, now),
                "read_iops": rates["rc"].update(io.read_count, now),
                "write_iops": rates["wc"].update(io.write_count, now),
                "busy_percent": None,
            }
            # busy_time is milliseconds of IO per second of wall clock, so the
            # rate is already a fraction; Linux only.
            busy_ms = getattr(io, "busy_time", None)
            busy_rate = rates["busy"].update(busy_ms, now)
            if busy_rate is not None:
                entry["busy_percent"] = min(busy_rate / 10.0, 100.0)
            entries.append(entry)
        return entries


@dataclass
class NetCollector:
    _rates: dict[str, dict[str, _Rate]] = field(default_factory=dict)

    def collect(self, now: float) -> list[dict]:
        counters = _safe(lambda: psutil.net_io_counters(pernic=True), default={}) or {}
        stats = _safe(lambda: psutil.net_if_stats(), default={}) or {}
        entries: list[dict] = []

        for nic, io in counters.items():
            # A down interface has nothing to measure, and one that has never
            # carried a byte would contribute a row of zeroes every second for
            # the life of the device. This machine reports 21 interfaces, of
            # which a handful are real.
            if_stats = stats.get(nic)
            if if_stats is not None and not if_stats.isup:
                continue
            if (io.bytes_sent + io.bytes_recv) == 0:
                continue
            keys = ("tx", "rx", "txp", "rxp", "ein", "eout", "din", "dout")
            rates = self._rates.setdefault(nic, {k: _Rate() for k in keys})
            entries.append(
                {
                    "nic": nic[:255],
                    "tx_bytes_per_s": rates["tx"].update(io.bytes_sent, now),
                    "rx_bytes_per_s": rates["rx"].update(io.bytes_recv, now),
                    "tx_packets_per_s": rates["txp"].update(io.packets_sent, now),
                    "rx_packets_per_s": rates["rxp"].update(io.packets_recv, now),
                    "errors_in_per_s": rates["ein"].update(io.errin, now),
                    "errors_out_per_s": rates["eout"].update(io.errout, now),
                    "drops_in_per_s": rates["din"].update(io.dropin, now),
                    "drops_out_per_s": rates["dout"].update(io.dropout, now),
                }
            )
        return entries


def collect_processes(limit: int = TOP_PROCESSES) -> list[dict]:
    """Top processes by CPU and by memory.

    cpu_percent() is only meaningful on the second and later calls for a given
    Process object, so psutil's process cache must persist between samples —
    process_iter() maintains it internally.
    """
    procs: list[dict] = []
    for proc in psutil.process_iter(["pid", "name", "username", "memory_info"]):
        try:
            info = proc.info
            procs.append(
                {
                    "pid": info["pid"],
                    "name": (info["name"] or "?")[:255],
                    "username": (info["username"] or None),
                    "cpu_percent": proc.cpu_percent(interval=None),
                    "memory_bytes": getattr(info.get("memory_info"), "rss", None),
                }
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            # Processes come and go between iteration and read; that is normal.
            continue

    entries: list[dict] = []
    for rank_by, key in (("cpu", "cpu_percent"), ("memory", "memory_bytes")):
        ranked = sorted(procs, key=lambda p: p[key] or 0, reverse=True)[:limit]
        for rank, proc_info in enumerate(ranked, start=1):
            username = proc_info["username"]
            entries.append(
                {
                    "rank_by": rank_by,
                    "rank": rank,
                    "pid": proc_info["pid"],
                    "name": proc_info["name"],
                    "username": username[:255] if username else None,
                    "cpu_percent": proc_info["cpu_percent"],
                    "memory_bytes": proc_info["memory_bytes"],
                }
            )
    return entries
