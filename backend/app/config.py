"""Application settings.

Loaded from environment variables and, for local development, from a `.env` file.

Env-file resolution is deliberately CWD-independent: paths are derived from this
file's location, not from where the process happens to be started. Makefile targets
`cd backend` first, so a CWD-relative `.env` would silently never load and every
secret would fall back to its insecure default.
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_BACKEND_DIR = Path(__file__).resolve().parents[1]
_REPO_ROOT = _BACKEND_DIR.parent

# Later entries win. `SENTINEL_ENV_FILE` overrides both (used by `make test`).
_ENV_FILES: tuple[Path, ...] | Path = (
    Path(os.environ["SENTINEL_ENV_FILE"])
    if os.environ.get("SENTINEL_ENV_FILE")
    else (_REPO_ROOT / ".env", _BACKEND_DIR / ".env")
)

DEV_JWT_SECRET = "dev-secret-change-me"  # noqa: S105 — a sentinel to detect, not a secret
DEV_APP_DB_PASSWORD = "sentinel_app"  # noqa: S105 — dev-only default, rejected in prod

# The role password is interpolated into `CREATE ROLE ... PASSWORD '...'`, which
# cannot take a bind parameter. This pattern makes that interpolation provably safe.
ROLE_PASSWORD_RE = re.compile(r"^[A-Za-z0-9_\-]{8,64}$")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ENV_FILES, env_file_encoding="utf-8", extra="ignore"
    )

    environment: Literal["dev", "test", "prod"] = "dev"

    # --- Database -------------------------------------------------------
    # `database_url` connects as the restricted role (RLS enforced).
    # `admin_database_url` connects as the owner: migrations, tests, and the
    # pre-auth paths that must read rows before a user identity exists.
    database_url: str = "postgresql+asyncpg://sentinel_app:sentinel_app@localhost:5432/sentinel"
    admin_database_url: str = "postgresql+asyncpg://sentinel:sentinel@localhost:5432/sentinel"

    app_db_role: str = "sentinel_app"
    app_db_password: str = DEV_APP_DB_PASSWORD
    manage_app_role: bool = True

    db_pool_size: int = 10
    db_max_overflow: int = 5
    db_pool_pre_ping: bool = True

    redis_url: str = "redis://localhost:6379/0"

    # --- JWT ------------------------------------------------------------
    jwt_secret: str = DEV_JWT_SECRET
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = "sentinel"
    jwt_audience: str = "sentinel-web"
    jwt_access_ttl_seconds: int = 900
    jwt_refresh_ttl_seconds: int = 60 * 60 * 24 * 30

    # --- Refresh cookie ---------------------------------------------------
    # `path` is always "/" (see app/security/cookies.py) because the Vite proxy
    # strips the `/api` prefix; a narrower path would never be sent back.
    refresh_cookie_name: str = "sentinel_refresh"
    cookie_secure: bool = False
    cookie_samesite: Literal["strict", "lax", "none"] = "strict"
    cookie_domain: str | None = None

    # --- Argon2id ---------------------------------------------------------
    # argon2-cffi's own defaults, matching the OWASP Argon2id recommendation.
    # Pinned explicitly so a library default change cannot silently weaken them.
    argon2_time_cost: int = 3
    argon2_memory_cost: int = 65536  # KiB
    argon2_parallelism: int = 4
    argon2_hash_len: int = 32
    argon2_salt_len: int = 16

    password_min_length: int = 12
    password_max_length: int = 128

    # --- Rate limiting ----------------------------------------------------
    # Off in dev: the Vite proxy sets no X-Forwarded-For, so every request looks
    # like 127.0.0.1 and shares one bucket — a couple of failed manual logins
    # would lock you out of your own dev server.
    rate_limit_enabled: bool = False
    rl_login_per_minute: int = 10
    rl_login_per_email_per_15m: int = 5
    rl_signup_per_hour: int = 5
    rl_refresh_per_minute: int = 30
    rl_logout_per_minute: int = 60

    anthropic_api_key: str | None = None
    # Haiku writes the short incident summary; Sonnet writes the deeper
    # root-cause analysis. Pinned to specific model IDs, not aliases, so a
    # provider-side default change cannot silently alter the tone/cost of
    # either without a deliberate bump here.
    anthropic_haiku_model: str = "claude-haiku-4-5-20251001"
    anthropic_sonnet_model: str = "claude-sonnet-5"

    # --- Alerts ------------------------------------------------------------
    alert_evaluator_interval_seconds: int = 15

    # --- Forecasting ---------------------------------------------------------
    # CPU-bound (an ETS fit per device per metric), unlike the 15s alert sweep,
    # so this cadence only has to suit its own cost, not alerting latency.
    # Was 900 (15 minutes). A brand-new device qualifies for its first forecast
    # after roughly 4 minutes of real samples (MIN_WINDOW_SECONDS /
    # MIN_POINTS_TREND), so a 15-minute sweep meant waiting up to 11 extra
    # minutes past qualifying just for the next tick — measured directly: a
    # device enrolled at 20:47:59Z got its first (fully-fitted) row at
    # 20:59:31Z. 120s caps that wait at roughly two minutes past qualifying
    # instead. Each tick's fitting work is real CPU, but it already runs off
    # the event loop via asyncio.to_thread (see forecast_worker.py), and for
    # the number of devices any one deployment of this project actually has,
    # eight times the cadence is not a meaningful cost.
    forecast_worker_interval_seconds: int = 120
    forecast_history_days: int = 14

    # --- AI insights -----------------------------------------------------------
    # Network-bound (an Anthropic call per stale incident), not CPU-bound like the
    # forecast worker — but still far looser than the 15s alert sweep, since each
    # tick that finds nothing changed costs no API call at all (see
    # app/ai/insights_service.py's fingerprint cache).
    insights_worker_interval_seconds: int = 60

    # --- Reports -----------------------------------------------------------
    # An hourly sweep is plenty: a schedule's own cadence is daily at the
    # finest (weekly/monthly), so the worker only needs to notice a due
    # schedule sometime during the day it becomes due, not the instant it does.
    report_worker_interval_seconds: int = 3600
    reports_default_period_days: int = 30
    reports_max_period_days: int = 366

    # --- Notifications: email ------------------------------------------------
    # All optional. Email dispatch is a no-op (logged, not an error) whenever
    # smtp_host is unset, matching anthropic_api_key's graceful-absence pattern.
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None
    smtp_use_tls: bool = True

    # --- Notifications: web push (VAPID) --------------------------------------
    # Also optional and off when unset. Generate a keypair once with py-vapid's
    # `vapid --gen` and place them here — this is an ops step, not code.
    vapid_public_key: str | None = None
    vapid_private_key: str | None = None
    vapid_subject: str | None = None  # a "mailto:" URI, required by the spec

    # --- Notifications: FCM (Android) -----------------------------------------
    # Optional and off when unset, exactly like SMTP and VAPID above. Both must
    # be set for anything to send: the project id names the FCM v1 endpoint, and
    # the service-account JSON is what mints the OAuth2 access token for it.
    # Generate the key file once in the Firebase console (Project settings →
    # Service accounts → Generate new private key) — an ops step, not code.
    fcm_project_id: str | None = None
    fcm_service_account_file: str | None = None

    # --- Agent distribution (Phase 11) ---------------------------------------
    # Where a CI run's `manifest.json` and its binaries were published. Unset is
    # a fully supported state, not a broken one: the download page then says no
    # build exists for the visitor's OS and points at building from source,
    # rather than offering a link that 404s. Same posture as unset SMTP/VAPID/
    # ANTHROPIC_API_KEY.
    agent_dist_dir: str | None = None
    # When set, the manifest is still read locally but the binaries themselves
    # are linked from here (a release host or CDN) instead of being streamed by
    # this API. Serving multi-megabyte files from the app process is fine for a
    # handful of installs and wrong at any scale.
    agent_download_base_url: str | None = None

    # --- Web console (Phase 11) ------------------------------------------------
    # When `frontend/web/dist` has been built (`make web-build`), the API process serves
    # the console too, so one origin is the whole product and a second machine
    # on the network can actually load it — `vite dev` binds to localhost and
    # is not a way to hand anyone a working install. Unset/unbuilt is a
    # supported state: the API serves only its own routes, exactly as before.
    serve_web_console: bool = True
    web_dist_dir: str | None = None

    # NOTE: pydantic-settings parses complex types as JSON, so this must be
    # written as CORS_ORIGINS=["http://localhost:5173"] — a bare URL fails.
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    @model_validator(mode="after")
    def _refuse_insecure_production(self) -> Settings:
        if not ROLE_PASSWORD_RE.match(self.app_db_password):
            raise ValueError(
                "app_db_password must match ^[A-Za-z0-9_-]{8,64}$ "
                "(it is interpolated into CREATE ROLE, which takes no bind parameters)"
            )
        if self.environment == "prod":
            problems = []
            if self.jwt_secret == DEV_JWT_SECRET:
                problems.append("jwt_secret is still the development default")
            elif len(self.jwt_secret.encode()) < 32:
                # RFC 7518 3.2: an HMAC key shorter than the digest weakens HS256.
                problems.append("jwt_secret must be at least 32 bytes")
            if not self.cookie_secure:
                problems.append("cookie_secure is False")
            if self.app_db_password == DEV_APP_DB_PASSWORD:
                problems.append("app_db_password is still the development default")
            if not self.rate_limit_enabled:
                # /auth/login and /enroll are the brute-forceable surface: the
                # first guards a password, the second is the only unauthenticated
                # write in the system. .env.example ships RATE_LIMIT_ENABLED=false
                # for good dev reasons (the Vite proxy puts every request in the
                # 127.0.0.1 bucket), so a deployment that copies it and flips
                # ENVIRONMENT=prod would otherwise start wide open and silent.
                problems.append("rate_limit_enabled is False")
            if self.cookie_samesite == "none":
                # SameSite is the only CSRF defence on /auth/refresh and
                # /auth/logout; there is no CSRF token. Relaxing it to "none"
                # makes both endpoints cross-site forgeable.
                problems.append(
                    "cookie_samesite='none' removes the only CSRF defence on the auth endpoints"
                )
            if problems:
                raise ValueError("refusing to start in prod: " + "; ".join(problems))
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
