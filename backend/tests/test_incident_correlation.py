"""analysis/incidents.py's pure logic: next_incident_status() and
correlation_fingerprint(). No DB — same style as test_alert_state_machine.py
for analysis/alerts.py's step().
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from app.analysis.incidents import EventMembership, correlation_fingerprint, next_incident_status


def test_incident_stays_open_while_any_event_is_firing():
    assert next_incident_status(firing_event_count=1) == "open"
    assert next_incident_status(firing_event_count=3) == "open"


def test_incident_resolves_once_no_event_is_firing():
    assert next_incident_status(firing_event_count=0) == "resolved"


def test_fingerprint_is_order_independent():
    a = uuid.uuid4()
    b = uuid.uuid4()
    m1 = [EventMembership(a, "firing", None), EventMembership(b, "resolved", datetime.now(UTC))]
    m2 = list(reversed(m1))
    assert correlation_fingerprint(m1) == correlation_fingerprint(m2)


def test_fingerprint_changes_when_a_status_changes():
    event_id = uuid.uuid4()
    firing = correlation_fingerprint([EventMembership(event_id, "firing", None)])
    resolved = correlation_fingerprint(
        [EventMembership(event_id, "resolved", datetime.now(UTC))]
    )
    assert firing != resolved


def test_fingerprint_changes_when_membership_changes():
    a, b = uuid.uuid4(), uuid.uuid4()
    one_event = correlation_fingerprint([EventMembership(a, "firing", None)])
    two_events = correlation_fingerprint(
        [EventMembership(a, "firing", None), EventMembership(b, "firing", None)]
    )
    assert one_event != two_events


def test_empty_membership_is_stable():
    assert correlation_fingerprint([]) == correlation_fingerprint([])
