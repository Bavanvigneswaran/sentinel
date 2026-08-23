"""The report's HTML/CSS, rendered by app/reports/pdf.py via Jinja2 and then
by WeasyPrint into a PDF. Kept as one template string rather than a loose
file on disk — this package has no other static assets, and a string
constant is one less path-resolution concern for a module that already has
to survive being imported from a background worker, not just a request.
"""

from __future__ import annotations

REPORT_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  @page {
    size: A4;
    margin: 18mm 14mm;
    @bottom-right { content: "Page " counter(page) " of " counter(pages); font-size: 8pt; color: #888; }
  }
  body { font-family: "Helvetica Neue", Arial, sans-serif; color: #1a1a1a; font-size: 10pt; }
  h1 { font-size: 18pt; margin-bottom: 2pt; }
  .subtitle { color: #666; font-size: 9pt; margin-bottom: 16pt; }
  h2 { font-size: 13pt; margin-top: 20pt; margin-bottom: 4pt; border-bottom: 1px solid #ddd; padding-bottom: 3pt; }
  .device-meta { color: #555; font-size: 9pt; margin-bottom: 8pt; }
  .stat-row { display: flex; gap: 18pt; margin-bottom: 10pt; }
  .stat { border: 1px solid #e2e2e2; border-radius: 4pt; padding: 6pt 10pt; flex: 1; }
  .stat .label { font-size: 8pt; color: #888; text-transform: uppercase; letter-spacing: 0.03em; }
  .stat .value { font-size: 14pt; font-weight: 600; margin-top: 2pt; }
  table { width: 100%; border-collapse: collapse; margin-bottom: 8pt; }
  th, td { text-align: left; padding: 4pt 6pt; border-bottom: 1px solid #eee; font-size: 9pt; }
  th { color: #888; font-weight: 600; text-transform: uppercase; font-size: 7.5pt; letter-spacing: 0.03em; }
  td.num, th.num { text-align: right; }
  .up { color: #b23b3b; }
  .down { color: #2f7a3f; }
  .empty { color: #999; font-style: italic; padding: 6pt 0; }
  .device-block { page-break-inside: avoid; margin-bottom: 14pt; }
</style>
</head>
<body>
  <h1>Sentinel fleet report</h1>
  <div class="subtitle">
    {{ bundle.period_start | fmt_dt }} &ndash; {{ bundle.period_end | fmt_dt }}
    ({{ bundle.period_days }} days) &middot; generated {{ bundle.generated_at | fmt_dt }}
  </div>

  {% if not bundle.devices %}
    <p class="empty">No devices in scope for this report.</p>
  {% endif %}

  {% for da in bundle.devices %}
  <div class="device-block">
    <h2>{{ da.device.name }}</h2>
    <div class="device-meta">
      {{ da.device.os or "unknown OS" }}{% if da.device.hostname %} &middot; {{ da.device.hostname }}{% endif %}
    </div>

    <div class="stat-row">
      <div class="stat">
        <div class="label">Uptime</div>
        <div class="value">{{ da.availability.uptime_percent | fmt_pct }}</div>
      </div>
      <div class="stat">
        <div class="label">Incidents</div>
        <div class="value">{{ da.reliability.incident_count }}</div>
      </div>
      <div class="stat">
        <div class="label">Mean time to resolve</div>
        <div class="value">{{ da.reliability.mean_time_to_resolve_seconds | fmt_duration }}</div>
      </div>
      <div class="stat">
        <div class="label">Alerts fired</div>
        <div class="value">{{ da.reliability.alert_fired_count }}</div>
      </div>
    </div>

    {% if da.trends %}
    <table>
      <thead>
        <tr>
          <th>Metric</th>
          <th class="num">Avg</th>
          <th class="num">Min</th>
          <th class="num">Max</th>
          <th class="num">Prev. avg</th>
          <th class="num">Change</th>
        </tr>
      </thead>
      <tbody>
        {% for trend in da.trends %}
        <tr>
          <td>{{ trend.metric | metric_label }}{% if trend.entity %} ({{ trend.entity }}){% endif %}</td>
          <td class="num">{{ trend.current.avg | fmt_num }}</td>
          <td class="num">{{ trend.current.min | fmt_num }}</td>
          <td class="num">{{ trend.current.max | fmt_num }}</td>
          <td class="num">{{ trend.previous.avg | fmt_num }}</td>
          <td class="num {{ trend.delta_percent | delta_class }}">{{ trend.delta_percent | fmt_delta }}</td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
    {% else %}
      <p class="empty">No metrics available for this device in the period.</p>
    {% endif %}
  </div>
  {% endfor %}
</body>
</html>
"""
