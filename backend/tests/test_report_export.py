"""GET /reports/analytics, /reports/export.csv, /reports/export.pdf against a
device with real seeded history — same "create via the API, seed via
write_samples" pattern test_alert_notifications.py and test_forecast_worker.py
use.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from app.db import AdminSessionLocal
from app.ingest.writer import write_samples
from app.models import Device
from app.schemas.protocol import Sample, SystemSample

SIGNUP = "/auth/signup"
CREDS = {"email": "report-export@example.com", "password": "a-perfectly-fine-password"}

T0 = datetime.now(UTC)


async def _auth_headers(client, creds=CREDS) -> dict:
    token = (await client.post(SIGNUP, json=creds)).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


async def _seed_a_little_history(device_id: str) -> None:
    async with AdminSessionLocal() as session:
        device = await session.get(Device, uuid.UUID(device_id))
        for i in range(5):
            ts = T0 - timedelta(hours=5 - i)
            await write_samples(
                session,
                device_id=device.id,
                user_id=device.user_id,
                samples=[
                    Sample(
                        ts=ts,
                        resolution_seconds=10,
                        system=SystemSample(cpu_percent=40.0 + i, mem_percent=50.0 + i),
                    )
                ],
                now=ts,
            )


async def test_analytics_requires_authentication(client):
    assert (await client.get("/reports/analytics")).status_code == 401


async def test_analytics_reports_a_device_with_history(client):
    headers = await _auth_headers(client)
    device = (await client.post("/devices", json={"name": "export-box"}, headers=headers)).json()
    await _seed_a_little_history(device["id"])

    resp = await client.get("/reports/analytics", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["period_days"] == 30
    assert len(body["devices"]) == 1
    device_analytics = body["devices"][0]
    assert device_analytics["device_id"] == device["id"]
    cpu_trend = next(t for t in device_analytics["trends"] if t["metric"] == "cpu_percent")
    assert cpu_trend["current"]["avg"] is not None
    assert cpu_trend["current"]["avg"] > 0


async def test_analytics_scoped_to_a_single_device(client):
    headers = await _auth_headers(client)
    device_a = (await client.post("/devices", json={"name": "box-a"}, headers=headers)).json()
    device_b = (await client.post("/devices", json={"name": "box-b"}, headers=headers)).json()
    await _seed_a_little_history(device_a["id"])
    await _seed_a_little_history(device_b["id"])

    resp = await client.get(
        "/reports/analytics", params={"device_id": device_a["id"]}, headers=headers
    )
    assert resp.status_code == 200
    assert [d["device_id"] for d in resp.json()["devices"]] == [device_a["id"]]


async def test_analytics_for_a_nonexistent_device_is_404(client):
    headers = await _auth_headers(client)
    resp = await client.get(
        "/reports/analytics",
        params={"device_id": "00000000-0000-0000-0000-000000000000"},
        headers=headers,
    )
    assert resp.status_code == 404


async def test_export_csv_returns_a_real_csv_with_a_header_row(client):
    headers = await _auth_headers(client)
    device = (await client.post("/devices", json={"name": "csv-box"}, headers=headers)).json()
    await _seed_a_little_history(device["id"])

    resp = await client.get("/reports/export.csv", headers=headers)
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    lines = resp.text.strip().splitlines()
    assert lines[0].startswith("device,period_start,period_end")
    assert any("CPU" in line for line in lines[1:])


async def test_export_pdf_returns_real_pdf_bytes(client):
    headers = await _auth_headers(client)
    device = (await client.post("/devices", json={"name": "pdf-box"}, headers=headers)).json()
    await _seed_a_little_history(device["id"])

    resp = await client.get("/reports/export.pdf", headers=headers)
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content[:5] == b"%PDF-"


async def test_export_pdf_with_no_devices_is_still_a_valid_empty_pdf(client):
    """A user with no devices at all is not an error case — the report is
    just empty, never synthesized."""
    headers = await _auth_headers(client)
    resp = await client.get("/reports/export.pdf", headers=headers)
    assert resp.status_code == 200
    assert resp.content[:5] == b"%PDF-"
