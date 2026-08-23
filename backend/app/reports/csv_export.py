"""Renders a services/report_service.ReportBundle to CSV: one flat,
denormalized table (device × metric per row, device-level availability and
reliability figures repeated on every one of that device's rows) — the shape
a spreadsheet import wants, rather than the nested JSON the API's own
analytics endpoint returns.

Synchronous and cheap (a handful of devices × six metrics, string
formatting only) — unlike PDF rendering this needs no asyncio.to_thread
wrapper.
"""

from __future__ import annotations

import csv
import io

from app.reports.pdf import METRIC_LABELS
from app.services.report_service import ReportBundle

_HEADER = (
    "device",
    "period_start",
    "period_end",
    "uptime_percent",
    "incident_count",
    "resolved_incident_count",
    "mean_time_to_resolve_seconds",
    "alert_fired_count",
    "metric",
    "entity",
    "current_avg",
    "current_min",
    "current_max",
    "previous_avg",
    "delta_percent",
)


def render_report_csv(bundle: ReportBundle) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(_HEADER)

    for da in bundle.devices:
        device_fields = (
            da.device.name,
            da.period_start.isoformat(),
            da.period_end.isoformat(),
            da.availability.uptime_percent,
            da.reliability.incident_count,
            da.reliability.resolved_incident_count,
            da.reliability.mean_time_to_resolve_seconds,
            da.reliability.alert_fired_count,
        )
        if not da.trends:
            writer.writerow((*device_fields, "", "", "", "", "", "", ""))
            continue
        for trend in da.trends:
            writer.writerow(
                (
                    *device_fields,
                    METRIC_LABELS.get(trend.metric, trend.metric),
                    trend.entity or "",
                    trend.current.avg,
                    trend.current.min,
                    trend.current.max,
                    trend.previous.avg,
                    trend.delta_percent,
                )
            )

    return buffer.getvalue()
