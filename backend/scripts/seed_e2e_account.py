"""Create the account the end-to-end suites sign in as.

    python scripts/seed_e2e_account.py
    python scripts/seed_e2e_account.py --enrollment-code
    python scripts/seed_e2e_account.py --format env >> "$GITHUB_ENV"

Idempotent: running it twice reuses the existing account and resets its
password to the one given, so a suite never has to care whether the database is
fresh.

What this does NOT do, and why
------------------------------
It creates an account. It does not create a device, and it does not write a
single metric row.

That is not an omission to fix later. CLAUDE.md's first hard rule is that every
number on screen came from a real agent reading a real machine, and a seeder
that inserts plausible-looking CPU history is the exact thing that rule
forbids — worse here than on a chart, because a test suite asserting against
invented data reports "passing" about behaviour nobody has observed.

So the supported way to get a device with real history in front of a test is to
enrol a real agent on the machine running the tests:

    python scripts/seed_e2e_account.py --enrollment-code
    cd ../agent && make agent-enroll code=<printed code> && make agent

On a CI runner that is a real Linux box reporting its own real CPU, memory and
disk — genuinely the thing the product monitors, just short-lived. Tests that
need history rather than a live reading should wait for it the way a user does,
or assert the empty state, which is a real state this product renders on
purpose.

Why a script under backend/scripts/ rather than a route or a fixture: creating
an account reaches past RLS (there is no tenant yet), and the same reasoning
train_novelty_model.py records applies — that is what
tests/test_unscoped_import_guard.py exists to keep out of app/.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

import sqlalchemy as sa

# Importable when run as `python scripts/seed_e2e_account.py` from backend/.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings  # noqa: E402
from app.db import AdminSessionLocal, SessionLocal, scope_to_user  # noqa: E402
from app.models import User  # noqa: E402
from app.schemas.auth import SignupRequest  # noqa: E402
from app.security.passwords import hash_password  # noqa: E402
from app.services import auth_service, enrollment_service  # noqa: E402

#: example.com is IANA's reserved documentation domain: it resolves nowhere, so
#: nothing this account is ever signed up to can actually be delivered. That is
#: the point — but the reason it is *this* reserved name and not the more
#: obvious `sentinel.test` is that email-validator rejects `.test`, `.local` and
#: `.invalid` as special-use, and EmailStr is what /auth/login parses with. An
#: account seeded at `e2e@sentinel.test` is created happily and then cannot log
#: in: the API answers 422 before it ever reaches the password, which reads as a
#: broken login endpoint rather than a bad fixture. _validated() below is what
#: stops that being possible a second time.
DEFAULT_EMAIL = "e2e@example.com"

#: Both overridable from the environment, because the credentials are not ours
#: to choose: a generated suite hardcodes whatever pair it was written with, and
#: the account has to be seeded under *that* pair or every case fails at sign-in.
#: Flags still win over the environment.
ENV_EMAIL = "SENTINEL_E2E_EMAIL"
ENV_PASSWORD = "SENTINEL_E2E_PASSWORD"  # noqa: S105 — a variable name
#: Twelve characters is the product's own minimum (`password_min_length`), and
#: the suites assert against that boundary, so the seeded password has to clear
#: it rather than be a convenient short string.
DEFAULT_PASSWORD = "e2e-Sentinel-Test-2026"  # noqa: S105 — a test fixture, guarded below


def _validated(email: str, password: str) -> str:
    """Put the credentials through the API's own signup schema first.

    Creating the account goes around the HTTP layer, so nothing else here would
    notice credentials the API will not accept — and the failure surfaces much
    later, as a login that 422s before it looks at the password.
    """
    try:
        return str(SignupRequest(email=email, password=password).email)
    except ValueError as exc:
        raise SystemExit(
            f"these credentials would create an account that cannot log in:\n{exc}"
        ) from exc


def _refuse_in_production(settings) -> None:  # noqa: ANN001
    """An account with a published password is a backdoor, not a fixture."""
    if settings.environment == "prod":
        raise SystemExit(
            "refusing to seed a known-password account in ENVIRONMENT=prod.\n"
            "The credentials this script writes are in the repository, so in a\n"
            "real deployment it is an unauthenticated login for anyone who can\n"
            "read the source. Point it at a test database instead:\n"
            "\n"
            "    SENTINEL_ENV_FILE=backend/.env.ci python scripts/seed_e2e_account.py"
        )


async def _upsert_account(email: str, password: str) -> tuple[User, bool]:
    """Return the account and whether it had to be created."""
    async with AdminSessionLocal() as session:
        existing = await session.scalar(sa.select(User).where(User.email == email.lower()))
        if existing is not None:
            # Reset rather than assume: a suite that fails to sign in because
            # the password drifted looks exactly like a broken login page.
            existing.password_hash = await hash_password(password)
            existing.is_active = True
            await session.commit()
            return existing, False

        # Through the product's own signup path, so the account gets the same
        # default alert rules a real one does. A seeded account missing them
        # would make the alert-rule screens render an empty state the suites
        # would then encode as correct.
        user = await auth_service.signup(session, email=email, password=password)
        return user, True


async def _mint_code(user: User, ttl_seconds: int) -> tuple[str, object]:
    async with SessionLocal() as session:
        scope_to_user(session, user.id)
        issued = await enrollment_service.create_enrollment_code(
            session, user.id, ttl_seconds=ttl_seconds
        )
        await session.commit()
        return issued.code, issued.expires_at


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", default=os.environ.get(ENV_EMAIL, DEFAULT_EMAIL))
    parser.add_argument(
        "--password", default=os.environ.get(ENV_PASSWORD, DEFAULT_PASSWORD)
    )
    parser.add_argument(
        "--enrollment-code",
        action="store_true",
        help="also mint a one-time code, so a real agent can enrol and report real metrics",
    )
    parser.add_argument("--ttl-seconds", type=int, default=3600)
    parser.add_argument(
        "--format",
        choices=("human", "env"),
        default="human",
        help="'env' prints KEY=VALUE lines for $GITHUB_ENV or a shell eval",
    )
    args = parser.parse_args()

    settings = get_settings()
    _refuse_in_production(settings)

    email = _validated(args.email.strip().lower(), args.password)

    user, created = await _upsert_account(email, args.password)

    code = expires_at = None
    if args.enrollment_code:
        code, expires_at = await _mint_code(user, args.ttl_seconds)

    if args.format == "env":
        print(f"SENTINEL_E2E_EMAIL={user.email}")
        print(f"SENTINEL_E2E_PASSWORD={args.password}")
        print(f"SENTINEL_E2E_USER_ID={user.id}")
        if code:
            print(f"SENTINEL_E2E_ENROLLMENT_CODE={code}")
    else:
        print(f"{'created' if created else 'reset password on'} {user.email}")
        print(f"  user_id:  {user.id}")
        print(f"  password: {args.password}")
        if code:
            print(f"  enrollment code: {code}  (expires {expires_at:%Y-%m-%d %H:%M:%SZ})")
            print("\n  Enrol a real agent to give this account real metrics:")
            print(f"    make agent-enroll code={code} && make agent")

    from app.db import dispose_engines

    await dispose_engines()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
