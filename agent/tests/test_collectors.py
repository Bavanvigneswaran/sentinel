"""Collectors, against this real machine.

These deliberately assert shape and honesty rather than specific values — the
numbers are whatever the host is actually doing. What matters is that an
unavailable metric is None and never an invented zero.
"""

import platform

import pytest

from sentinel_agent.collectors.host import collect_host_info
from sentinel_agent.collectors.latency import LatencyTarget, measure
from sentinel_agent.collectors.resources import (
    DiskIoCollector,
    NetCollector,
    collect_disk_usage,
    collect_processes,
)
from sentinel_agent.collectors.system import SystemCollector, _Rate

# --- counter differentiation ------------------------------------------------


def test_the_first_reading_has_no_rate():
    """There is no interval to divide by yet; None, not zero."""
    rate = _Rate()
    assert rate.update(1000, now=10.0) is None


def test_a_rate_is_per_second():
    rate = _Rate()
    rate.update(1000, now=10.0)
    assert rate.update(1200, now=12.0) == pytest.approx(100.0)


def test_a_counter_reset_reports_nothing_rather_than_a_spike():
    """A reboot, a wrap, or an interface that reappeared. Reporting the
    difference would draw an enormous spike that never happened."""
    rate = _Rate()
    rate.update(1_000_000, now=10.0)
    assert rate.update(5, now=11.0) is None


def test_a_missing_value_clears_the_state():
    rate = _Rate()
    rate.update(1000, now=10.0)
    assert rate.update(None, now=11.0) is None
    # The next real reading is a first reading again, not a huge delta.
    assert rate.update(9999, now=12.0) is None


def test_a_zero_interval_does_not_divide_by_zero():
    rate = _Rate()
    rate.update(1000, now=10.0)
    assert rate.update(2000, now=10.0) is None


# --- system -----------------------------------------------------------------


def test_the_system_collector_returns_the_expected_keys():
    sample = SystemCollector().collect()
    for key in (
        "cpu_percent", "mem_percent", "mem_total_bytes", "swap_percent",
        "uptime_seconds", "battery_percent", "temperature_celsius", "load1",
    ):
        assert key in sample, f"{key} missing from the system sample"


def test_memory_is_actually_measured():
    sample = SystemCollector().collect()
    assert sample["mem_total_bytes"] > 0
    assert 0 <= sample["mem_percent"] <= 100


def test_an_implausible_cpu_frequency_is_reported_as_unavailable():
    """psutil returns scpufreq(current=4) on Apple Silicon, which is not MHz.
    Rendering '4 MHz' would be a fabricated reading."""
    collector = SystemCollector()

    class _Freq:
        current = 4

    assert collector._cpu_freq_mhz(_Freq()) is None
    assert collector._cpu_freq_mhz(None) is None

    class _Real:
        current = 3200

    assert collector._cpu_freq_mhz(_Real()) == 3200.0


@pytest.mark.skipif(platform.system() != "Darwin", reason="macOS-specific behaviour")
def test_context_switches_are_unavailable_on_macos():
    """psutil's ctx_switches is a per-interval delta on Darwin, not a
    cumulative counter, so a derived rate would be meaningless."""
    collector = SystemCollector()
    collector.collect()
    assert collector.collect()["ctx_switches_per_s"] is None


# --- disks and interfaces ---------------------------------------------------

def test_disk_usage_reports_real_filesystems():
    entries = collect_disk_usage()
    assert entries, "no filesystems reported"
    for entry in entries:
        assert entry["total_bytes"] > 0
        assert 0 <= entry["percent"] <= 100


@pytest.mark.skipif(platform.system() != "Darwin", reason="APFS-specific behaviour")
def test_apfs_hidden_volumes_are_filtered_but_the_data_volume_is_kept():
    """Filtering read-only would drop the sealed root; filtering Apple's
    dontbrowse flag would drop /System/Volumes/Data, where all user data
    lives — on this machine that understates real usage sevenfold."""
    mounts = {e["mount"] for e in collect_disk_usage()}

    assert "/" in mounts
    assert "/System/Volumes/Data" in mounts
    assert "/System/Volumes/Preboot" not in mounts
    assert "/System/Volumes/VM" not in mounts


def test_only_active_interfaces_are_reported():
    """A down interface has nothing to measure, and one that has never carried
    a byte would emit a row of zeroes every second forever."""
    import psutil

    collector = NetCollector()
    collector.collect(now=1.0)
    reported = {e["nic"] for e in collector.collect(now=2.0)}

    stats = psutil.net_if_stats()
    for nic in reported:
        if nic in stats:
            assert stats[nic].isup, f"{nic} is down but was reported"


def test_disk_io_rates_appear_on_the_second_reading():
    collector = DiskIoCollector()
    assert all(e["read_bytes_per_s"] is None for e in collector.collect(now=1.0))

    second = collector.collect(now=2.0)
    if second:  # some CI environments expose no per-disk counters at all
        assert any(e["read_bytes_per_s"] is not None for e in second)


# --- processes --------------------------------------------------------------


def test_processes_are_ranked_by_cpu_and_memory():
    entries = collect_processes(limit=5)
    by_cpu = [e for e in entries if e["rank_by"] == "cpu"]
    by_mem = [e for e in entries if e["rank_by"] == "memory"]

    assert 0 < len(by_cpu) <= 5
    assert 0 < len(by_mem) <= 5
    assert [e["rank"] for e in by_cpu] == list(range(1, len(by_cpu) + 1))

    memory = [e["memory_bytes"] or 0 for e in by_mem]
    assert memory == sorted(memory, reverse=True), "memory ranking is not ordered"


# --- host -------------------------------------------------------------------


def test_host_info_describes_this_machine():
    host = collect_host_info()
    assert host["hostname"]
    assert host["os"] == platform.system()
    assert host["cpu_cores"] >= 1
    assert host["agent_version"]


# --- latency ----------------------------------------------------------------


def test_latency_target_parsing():
    assert LatencyTarget.parse("example.com") == LatencyTarget("example.com", 443)
    assert LatencyTarget.parse("example.com:8443") == LatencyTarget("example.com", 8443)
    # A malformed port must not crash the agent.
    assert LatencyTarget.parse("example.com:nope").host == "example.com:nope"


async def test_an_unreachable_target_reports_null_rtt_not_zero():
    """Zero would read as a perfect connection on every chart."""
    # Reserved for documentation (RFC 5737); nothing answers here.
    result = await measure(LatencyTarget("192.0.2.1", 9), probes=1, timeout=0.2)

    assert result["reachable"] is False
    assert result["rtt_ms_avg"] is None
    assert result["packet_loss_percent"] == 100.0
