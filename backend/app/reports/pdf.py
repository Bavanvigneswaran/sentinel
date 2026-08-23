"""Renders a services/report_service.ReportBundle to PDF bytes via Jinja2 +
WeasyPrint.

Both steps are synchronous, CPU-bound work — laying out and rasterizing a
multi-page PDF is not fast. Every caller (the on-demand download route and
app/workers/report_worker.py's scheduled email) wraps `render_report_pdf` in
`asyncio.to_thread`, the same posture Phase 7's forecast worker takes for its
own CPU-bound ETS fit, per CLAUDE.md's "nothing blocking on the event loop"
hard rule.
"""

from __future__ import annotations

from datetime import datetime

import jinja2
from weasyprint import HTML

from app.reports.templates import REPORT_TEMPLATE
from app.services.report_service import ReportBundle

METRIC_LABELS: dict[str, str] = {
    "cpu_percent": "CPU",
    "mem_percent": "Memory",
    "swap_percent": "Swap",
    "disk_percent": "Disk usage",
    "packet_loss_percent": "Packet loss",
    "cpu_iowait_percent": "IO wait",
}


def _fmt_num(value: float | None) -> str:
    return "—" if value is None else f"{value:.1f}"


def _fmt_pct(value: float | None) -> str:
    return "—" if value is None else f"{value:.1f}%"


def _fmt_delta(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{'+' if value >= 0 else ''}{value:.1f}%"


def _delta_class(value: float | None) -> str:
    if value is None:
        return ""
    return "up" if value >= 0 else "down"


def _fmt_dt(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M UTC")


def _fmt_duration(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    seconds = int(seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes = remainder // 60
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m"
    return f"{seconds}s"


def _metric_label(metric: str) -> str:
    return METRIC_LABELS.get(metric, metric)


_ENV = jinja2.Environment(autoescape=True, undefined=jinja2.StrictUndefined)
_ENV.filters["fmt_num"] = _fmt_num
_ENV.filters["fmt_pct"] = _fmt_pct
_ENV.filters["fmt_delta"] = _fmt_delta
_ENV.filters["delta_class"] = _delta_class
_ENV.filters["fmt_dt"] = _fmt_dt
_ENV.filters["fmt_duration"] = _fmt_duration
_ENV.filters["metric_label"] = _metric_label
_TEMPLATE = _ENV.from_string(REPORT_TEMPLATE)


def render_report_html(bundle: ReportBundle) -> str:
    return _TEMPLATE.render(bundle=bundle)


def render_report_pdf(bundle: ReportBundle) -> bytes:
    return HTML(string=render_report_html(bundle)).write_pdf()
