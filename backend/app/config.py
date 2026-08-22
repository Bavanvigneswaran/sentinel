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
            if problems:
                raise ValueError("refusing to start in prod: " + "; ".join(problems))
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
