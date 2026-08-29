"""Force row-level security on the tables no owner-role path touches.

RLS has been enabled with a policy on all 23 tenant tables since 0002/0006, and
the application connects as a NOSUPERUSER/NOBYPASSRLS role, so those policies
are real. What they do *not* constrain is the table owner: without
`FORCE ROW LEVEL SECURITY`, `sentinel` is exempt, and `get_unscoped_session()`
hands out a connection on exactly that role.

That exemption is load-bearing and cannot simply be removed. Fourteen tables are
legitimately read or written through the owner role, because there is no tenant
to scope by at the time:

    users, refresh_tokens            signup and login read rows before an
                                     identity exists
    alert_rules                      seeded in the same transaction as a new
                                     account, and enumerated by the evaluator
    enrollment_codes, devices,       POST /enroll is authenticated by the code
    agent_tokens                     itself; the agent has no user identity
    metric_samples, disk_usage_,     the ingest socket authenticates a *device*,
    disk_io_, net_, latency_,        not a user
    process_samples
    incidents, report_schedules      enumerated by their background workers,
                                     which have no request and no JWT

The nine below are touched only through sessions already scoped by
`scope_to_user()`. Forcing RLS on them costs nothing today and converts a whole
class of future mistake — a new query routed through `get_unscoped_session()` —
from a silent cross-tenant read into a Postgres error.

`tests/test_unscoped_import_guard.py` remains the primary control; this is the
second line behind it. TRUNCATE is not subject to RLS, so the test suite's
between-test cleanup is unaffected.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0016_force_rls"
down_revision: str | Sequence[str] | None = "0015_multivariate_rules"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Every tenant table that no owner-role code path reads or writes. Adding a
#: table here is safe only if nothing reachable from get_unscoped_session()
#: touches it — check before you do.
FORCED_TABLES = (
    "alert_events",
    "alert_states",
    "alert_silences",
    "anomaly_baselines",
    "exhaustion_forecasts",
    "fcm_tokens",
    "metric_forecasts",
    "notification_settings",
    "web_push_subscriptions",
)


def upgrade() -> None:
    for table in FORCED_TABLES:
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY;")


def downgrade() -> None:
    for table in FORCED_TABLES:
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY;")
