"""Ring buffer and aggregation.

The aggregation rules are where a bug would quietly fabricate data, so these
pin down the specific choices rather than just "it returns something".
"""

import pytest

from sentinel_agent.buffer import SampleBuffer, aggregate


def _sample(ts: str, cpu: float | None, mem: float = 50.0, **system) -> dict:
    return {
        "ts": ts,
        "system": {"cpu_percent": cpu, "mem_percent": mem, **system},
        "disk_usage": [],
        "disk_io": [],
        "net": [],
        "latency": [],
        "processes": [],
    }


# --- buffer -----------------------------------------------------------------


def test_the_buffer_is_bounded_and_keeps_the_newest():
    """Under a long outage the recent hour is worth more than the first hour."""
    buffer = SampleBuffer(maxlen=3)
    for i in range(10):
        buffer.add(_sample(f"t{i}", cpu=float(i)))

    assert len(buffer) == 3
    assert [s["ts"] for s in buffer.peek()] == ["t7", "t8", "t9"]


def test_discard_removes_only_what_was_acked():
    """Samples collected while a push was in flight must survive it."""
    buffer = SampleBuffer(maxlen=100)
    for i in range(5):
        buffer.add(_sample(f"t{i}", cpu=1.0))

    buffer.discard(3)

    assert [s["ts"] for s in buffer.peek()] == ["t3", "t4"]


def test_discarding_more_than_present_is_safe():
    buffer = SampleBuffer(maxlen=10)
    buffer.add(_sample("t0", cpu=1.0))
    buffer.discard(50)
    assert len(buffer) == 0


# --- aggregation ------------------------------------------------------------


def test_aggregate_of_nothing_is_none():
    assert aggregate([], resolution_seconds=10) is None


def test_gauges_are_averaged():
    samples = [_sample("t1", 10.0), _sample("t2", 20.0), _sample("t3", 30.0)]
    result = aggregate(samples, resolution_seconds=10)
    assert result["system"]["cpu_percent"] == pytest.approx(20.0)


def test_cpu_and_memory_carry_min_and_max():
    """A spike inside the window is exactly the signal alerting needs; a mean
    alone would erase it."""
    samples = [_sample("t1", 3.0, mem=40.0), _sample("t2", 90.0, mem=44.0)]
    system = aggregate(samples, resolution_seconds=10)["system"]

    assert system["cpu_percent_min"] == 3.0
    assert system["cpu_percent_max"] == 90.0
    assert system["mem_percent_min"] == 40.0
    assert system["mem_percent_max"] == 44.0


def test_the_timestamp_is_the_window_end():
    """A sample must never carry a timestamp in the future."""
    samples = [_sample("t1", 1.0), _sample("t2", 2.0), _sample("t3", 3.0)]
    assert aggregate(samples, resolution_seconds=10)["ts"] == "t3"


def test_byte_counts_stay_integers():
    """Averaging into a float would render as '8589934592.4 bytes'."""
    samples = [
        _sample("t1", 1.0, mem_used_bytes=100),
        _sample("t2", 1.0, mem_used_bytes=201),
    ]
    value = aggregate(samples, resolution_seconds=10)["system"]["mem_used_bytes"]
    assert value == 150
    assert isinstance(value, int)


def test_unavailable_metrics_stay_none_rather_than_becoming_zero():
    samples = [_sample("t1", None), _sample("t2", None)]
    system = aggregate(samples, resolution_seconds=10)["system"]
    assert system["cpu_percent"] is None
    assert system["cpu_percent_min"] is None


def test_a_partially_available_metric_averages_what_exists():
    samples = [_sample("t1", None), _sample("t2", 10.0), _sample("t3", 20.0)]
    assert aggregate(samples, resolution_seconds=10)["system"]["cpu_percent"] == pytest.approx(
        15.0
    )


def test_booleans_take_the_latest_value():
    samples = [
        _sample("t1", 1.0, battery_plugged=False),
        _sample("t2", 1.0, battery_plugged=True),
    ]
    assert aggregate(samples, resolution_seconds=10)["system"]["battery_plugged"] is True


def test_resolution_is_recorded():
    result = aggregate([_sample("t1", 1.0)], resolution_seconds=10)
    assert result["resolution_seconds"] == 10


# --- per-entity series ------------------------------------------------------


def _with_disk(ts: str, percent: float, used: int) -> dict:
    sample = _sample(ts, 1.0)
    sample["disk_usage"] = [
        {"mount": "/", "percent": percent, "used_bytes": used, "filesystem": "apfs"}
    ]
    return sample


def test_capacity_takes_the_latest_reading_not_an_average():
    """"How full is the disk" is a current fact, not a windowed mean."""
    result = aggregate([_with_disk("t1", 10.0, 100), _with_disk("t2", 90.0, 900)], 10)
    entry = result["disk_usage"][0]
    assert entry["percent"] == 90.0
    assert entry["used_bytes"] == 900


def test_rates_are_averaged_across_the_window():
    def with_net(ts: str, rx: float) -> dict:
        sample = _sample(ts, 1.0)
        sample["net"] = [{"nic": "en0", "rx_bytes_per_s": rx, "tx_bytes_per_s": 0.0}]
        return sample

    result = aggregate([with_net("t1", 100.0), with_net("t2", 300.0)], 10)
    assert result["net"][0]["rx_bytes_per_s"] == pytest.approx(200.0)


def test_entities_appearing_midway_are_still_reported():
    """A NIC that comes up mid-window should not be dropped."""
    first = _sample("t1", 1.0)
    second = _sample("t2", 1.0)
    second["net"] = [{"nic": "en1", "rx_bytes_per_s": 50.0}]

    result = aggregate([first, second], 10)
    assert [e["nic"] for e in result["net"]] == ["en1"]


def test_processes_take_the_latest_ranking_only():
    """A top-10 list cannot be averaged across a window whose membership
    changes."""
    first = _sample("t1", 1.0)
    first["processes"] = [{"rank_by": "cpu", "rank": 1, "pid": 1, "name": "old"}]
    second = _sample("t2", 1.0)
    second["processes"] = [{"rank_by": "cpu", "rank": 1, "pid": 2, "name": "new"}]

    result = aggregate([first, second], 10)
    assert [p["name"] for p in result["processes"]] == ["new"]
