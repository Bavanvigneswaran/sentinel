"""Health score: one number per device, and the arithmetic behind it.

Deliberately dull. This is a weighted average of piecewise-linear curves over
metrics the agent actually measured — no model, no training, no LLM. Phase 8's
Claude integration may *explain* a score; it never produces one. Keeping the
computation this plain is what makes the explanation checkable.

Three rules follow from CLAUDE.md's "never synthesise a metric":

* **A component with no reading is excluded, not zeroed.** A machine that cannot
  report swap does not thereby have perfect swap health, and it is not unhealthy
  either. The weights of the components that *did* report are renormalised, and
  the missing ones are named in the result so the UI can say which parts of the
  score were not measured.
* **An offline device has no score at all.** Its last reading describes a moment
  that has passed; presenting it as current health would be a claim about a
  machine nobody is talking to. The band is "unknown" and the reason says why.
* **Round-trip time is not scored.** 9 ms to a LAN gateway and 9 ms to a
  public resolver mean entirely different things, and the target is
  user-configured, so an absolute threshold would be arbitrary. Packet loss —
  which is bad at any distance — carries the network component instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Band = Literal["healthy", "degraded", "critical", "unknown"]

#: Score at or above which a device is in each band.
HEALTHY_AT = 80
DEGRADED_AT = 50

#: (metric value, score at that value), ascending by value. Between two points
#: the score is linear; outside the ends it is clamped. Curves rather than
#: thresholds so that 71% CPU and 89% CPU are not the same colour.
Curve = tuple[tuple[float, float], ...]

CPU_CURVE: Curve = ((0, 100), (60, 92), (80, 65), (92, 30), (100, 0))
MEMORY_CURVE: Curve = ((0, 100), (65, 92), (85, 60), (95, 25), (100, 0))
# Some swap in use is normal on every modern OS; sustained heavy swap is not.
SWAP_CURVE: Curve = ((0, 100), (10, 95), (40, 70), (75, 30), (100, 0))
# Disk is the one that ends in an outage rather than a slowdown, so it falls
# off harder at the top and is the highest-weighted component.
DISK_CURVE: Curve = ((0, 100), (70, 90), (85, 60), (95, 20), (100, 0))
# Above ~5% loss a link is unusable for interactive work long before 100%.
PACKET_LOSS_CURVE: Curve = ((0, 100), (0.5, 95), (2, 75), (5, 45), (20, 10), (100, 0))
# iowait is a symptom rather than a resource, so it is weighted low; a machine
# spending a fifth of its time waiting on disk is still worth flagging.
IOWAIT_CURVE: Curve = ((0, 100), (5, 95), (20, 65), (40, 30), (100, 0))


def score_on_curve(value: float, curve: Curve) -> float:
    """Linear interpolation along `curve`, clamped at both ends."""
    if value <= curve[0][0]:
        return curve[0][1]
    for (x0, y0), (x1, y1) in zip(curve, curve[1:], strict=False):
        if value <= x1:
            span = x1 - x0
            if span == 0:
                return y1
            return y0 + (y1 - y0) * (value - x0) / span
    return curve[-1][1]


@dataclass(frozen=True)
class HealthComponent:
    key: str
    label: str
    #: The measured value, or None when the platform did not report it.
    value: float | None
    unit: str
    #: 0–100 for this component alone; None when `value` is None.
    score: float | None
    weight: int

    @property
    def available(self) -> bool:
        return self.score is not None


@dataclass(frozen=True)
class HealthInputs:
    """Recent readings for one device. Every field is optional on purpose."""

    cpu_percent: float | None = None
    memory_percent: float | None = None
    swap_percent: float | None = None
    #: The fullest mount on the machine — the one that will run out first.
    disk_percent: float | None = None
    #: Worst packet loss across the device's configured latency targets.
    packet_loss_percent: float | None = None
    cpu_iowait_percent: float | None = None


@dataclass(frozen=True)
class HealthResult:
    score: int | None
    band: Band
    components: tuple[HealthComponent, ...]
    #: Why there is no score. None whenever `score` is not None.
    reason: str | None = None

    @property
    def unavailable(self) -> tuple[str, ...]:
        return tuple(c.key for c in self.components if not c.available)


#: (key, label, inputs attribute, unit, curve, weight). Disk outranks CPU and
#: memory because a full disk takes the machine down and a busy CPU does not.
_SPEC: tuple[tuple[str, str, str, str, Curve, int], ...] = (
    ("cpu", "CPU", "cpu_percent", "%", CPU_CURVE, 3),
    ("memory", "Memory", "memory_percent", "%", MEMORY_CURVE, 3),
    ("disk", "Disk capacity", "disk_percent", "%", DISK_CURVE, 4),
    ("swap", "Swap", "swap_percent", "%", SWAP_CURVE, 1),
    ("network", "Packet loss", "packet_loss_percent", "%", PACKET_LOSS_CURVE, 2),
    ("iowait", "IO wait", "cpu_iowait_percent", "%", IOWAIT_CURVE, 1),
)


def band_for(score: float) -> Band:
    if score >= HEALTHY_AT:
        return "healthy"
    if score >= DEGRADED_AT:
        return "degraded"
    return "critical"


def unknown_health(reason: str) -> HealthResult:
    """No score, and a plain statement of why — used for a device that is
    offline or has not reported since it enrolled."""
    return HealthResult(score=None, band="unknown", components=(), reason=reason)


def compute_health(inputs: HealthInputs) -> HealthResult:
    """Weighted mean of the components that reported a value.

    Renormalising over the available weights (rather than dividing by the full
    weight total) is the whole point: a Windows box with no iowait reading and a
    VM with no swap must still be comparable to a laptop that reports both.
    """
    components = tuple(
        HealthComponent(
            key=key,
            label=label,
            value=value,
            unit=unit,
            score=None if value is None else score_on_curve(value, curve),
            weight=weight,
        )
        for key, label, attr, unit, curve, weight in _SPEC
        for value in (getattr(inputs, attr),)
    )

    available = [c for c in components if c.available]
    if not available:
        return HealthResult(
            score=None,
            band="unknown",
            components=components,
            reason="no measurable metrics reported",
        )

    total_weight = sum(c.weight for c in available)
    weighted = sum(c.score * c.weight for c in available)  # type: ignore[operator]
    score = weighted / total_weight

    return HealthResult(score=round(score), band=band_for(score), components=components)
